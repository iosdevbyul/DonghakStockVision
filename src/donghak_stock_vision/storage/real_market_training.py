"""Original KRX research source -> existing Phase 2 snapshot/trainer/model contracts.

Protected outer Final Test is never read. A separately pinned inner development
split is required because the existing trainer also computes test diagnostics.
"""

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.data.learning import AnalysisError, Direction, Minimums, digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_ticker
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import TrainingService, load_model
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.signals.dataset import build_dataset, requirements, split_dataset
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.capture_raw_source_contract import prepare_raw_source_custody
from donghak_stock_vision.storage.historical_readiness import _file_hash, _OfflineKRXParser
from donghak_stock_vision.storage.sqlite import OPERATIONAL_APPLICATION_ID

VERSION = "real_market_training_source_v1"
MAX_ROWS = 20000
MAX_RAW_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class RealMarketTrainingRequest:
    database: Path
    database_pin: str
    access_split: ExplicitResearchSplit
    access_split_pin: str
    fit_split: ExplicitResearchSplit | None
    fit_split_pin: str | None
    tickers: tuple[str, ...]
    cutoff: datetime
    research_calendar: ResearchCalendarPolicy | None = None
    source_contract: str = "receipt_archive_v1"

    def validate(self) -> None:
        _splits(self.access_split, self.access_split_pin, self.fit_split, self.fit_split_pin)
        sha(self.database_pin)
        require(
            self.source_contract in ("receipt_archive_v1", "legacy_research_v1"),
            "unsupported_training_source_contract",
        )
        require(
            type(self.tickers) is tuple
            and 0 < len(self.tickers) <= 25
            and self.tickers == tuple(sorted(set(self.tickers))),
            "training_universe_bound",
        )
        for ticker in self.tickers:
            validate_ticker(ticker)
        require(
            self.end < timestamp(self.cutoff).astimezone(SEOUL).date(),
            "training_snapshot_cutoff_after_window_required",
        )
        require((self.end - self.start).days <= 3660, "training_window_bound")
        if self.research_calendar is not None:
            self.research_calendar.__post_init__()
            require(
                len(self.research_calendar.incomplete_dates) <= 64,
                "training_research_exclusion_bound",
            )

    @property
    def start(self) -> date:
        return self.fit_split.training.start if self.fit_split else self.access_split.training.start

    @property
    def end(self) -> date:
        return self.fit_split.final_test.end if self.fit_split else self.access_split.validation.end

    @property
    def document(self) -> FrozenJSON:
        self.validate()
        return FrozenJSON.freeze(
            {
                "version": VERSION,
                "database_pin": self.database_pin,
                "source_contract": self.source_contract,
                "access_split": self.access_split.to_dict(),
                "access_split_pin": self.access_split_pin,
                "fit_split": self.fit_split.to_dict() if self.fit_split else None,
                "fit_split_pin": self.fit_split_pin,
                "tickers": list(self.tickers),
                "cutoff": timestamp(self.cutoff).isoformat(),
                "research_calendar": self.research_calendar.to_dict()
                if self.research_calendar
                else None,
            }
        )


def _splits(
    access: ExplicitResearchSplit,
    access_pin: str,
    fit: ExplicitResearchSplit | None,
    fit_pin: str | None,
) -> None:
    require(type(access) is ExplicitResearchSplit, "original_access_split_required")
    access.__post_init__()
    require(access.identifier == sha(access_pin), "training_split_pin_mismatch")
    if fit is None:
        require(fit_pin is None, "training_fit_split_required")
        return
    require(type(fit) is ExplicitResearchSplit, "original_fit_split_required")
    fit.__post_init__()
    require(fit_pin is not None and fit.identifier == sha(fit_pin), "training_split_pin_mismatch")
    require(
        access.training.start <= fit.training.start <= fit.training.end <= access.training.end
        and access.validation.start <= fit.validation.start <= fit.validation.end
        and fit.final_test.end <= access.validation.end,
        "fit_split_must_stay_inside_original_training_and_tuning",
    )


def _quiescent(path: Path, pin: str) -> None:
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024**3,
        "training_source_file_bound",
    )
    require(
        not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")),
        "training_source_must_be_quiescent",
    )
    require(_file_hash(path) == pin, "training_database_pin_mismatch")


class _VerifiedMarket(ReadOnlyMarketStore):
    """Minimum read adapter; original tables and bar payloads are never rewritten.

    Inherits the MarketDataStore read API. All inherited write methods remain
    forbidden by SQLite query_only. Only selected raw-source owners are replayed.
    """

    def __init__(self, request: RealMarketTrainingRequest) -> None:
        request.validate()
        self.request = request
        _quiescent(request.database, request.database_pin)
        self.connection = sqlite3.connect(
            request.database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        try:
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            self._verify()
        except BaseException:
            self.close()
            raise

    def _verify(self) -> None:
        r = self.request
        require(
            self.connection.execute("PRAGMA application_id").fetchone()[0]
            != OPERATIONAL_APPLICATION_ID,
            "research_source_schema_required",
        )
        start, end = r.start, r.end
        tables = {
            row[0]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        require({"bars", "bar_sources", "raw_pages"} <= tables, "research_source_schema_required")
        require(
            ("raw_capture_receipts" in tables) == (r.source_contract == "receipt_archive_v1"),
            "explicit_training_archive_contract_mismatch",
        )
        placeholders = ",".join("?" for _ in r.tickers)
        excluded = (
            tuple(d.isoformat() for d in r.research_calendar.incomplete_dates)
            if r.research_calendar
            else ()
        )
        exclusion = (
            " AND trading_date NOT IN (" + ",".join("?" for _ in excluded) + ")" if excluded else ""
        )
        params = (*r.tickers, start.isoformat(), end.isoformat(), *excluded)
        rows = self.connection.execute(
            "SELECT ticker,trading_date,substr(payload,1,16385),length(payload) FROM bars "
            f"WHERE ticker IN ({placeholders}) AND trading_date BETWEEN ? AND ? "
            + exclusion
            + " ORDER BY ticker,trading_date LIMIT ?",
            (*params, MAX_ROWS + 1),
        ).fetchall()
        require(len(rows) <= MAX_ROWS, "training_selected_row_bound")
        selected: dict[tuple[str, str], DailyBar] = {}
        for ticker, day, payload, size in rows:
            require(size <= 16384, "training_bar_payload_bound")
            bar = DailyBar.from_dict(FrozenJSON(payload).to_dict())
            require(
                bar.ticker == ticker
                and bar.trading_date.isoformat() == day
                and bar.provider == "krx"
                and bar.market == "KOSPI"
                and bar.adjustment == "unadjusted",
                "training_source_bar_identity",
            )
            selected[ticker, day] = bar
        require(len(selected) == len(rows), "training_duplicate_bar")
        links = self.connection.execute(
            "SELECT s.ticker,s.trading_date,s.raw_id,r.provider,r.ticker,r.source_date "
            "FROM bar_sources s LEFT JOIN raw_pages r ON r.id=s.raw_id "
            f"WHERE s.ticker IN ({placeholders}) AND s.trading_date BETWEEN ? AND ? "
            + exclusion.replace("trading_date", "s.trading_date")
            + " ORDER BY s.raw_id LIMIT ?",
            (*params, MAX_ROWS * 16 + 1),
        ).fetchall()
        require(len(links) <= MAX_ROWS * 16, "training_source_link_bound")
        grouped: dict[int, set[tuple[str, str]]] = {}
        for ticker, day, raw_id, provider, scope, raw_day in links:
            require(
                (ticker, day) in selected
                and provider == "krx"
                and raw_day == day
                and scope in (ticker, "@krx_market:KOSPI"),
                "training_raw_source_ownership",
            )
            grouped.setdefault(raw_id, set()).add((ticker, day))
        require(len(grouped) <= 4096, "training_raw_response_count_bound")
        parser = _OfflineKRXParser("KOSPI")
        matched: set[tuple[str, str]] = set()
        self.sources: list[dict[str, Any]] = []
        total = 0
        for raw_id, keys in grouped.items():
            # Check metadata and budgets before loading the BLOB. Receipt/source
            # integrity is owned by the unchanged raw-source custody implementation.
            size = self.connection.execute(
                "SELECT length(body) FROM raw_pages WHERE id=?", (raw_id,)
            ).fetchone()[0]
            require(type(size) is int and 0 < size <= 2 * 1024**2, "training_raw_byte_bound")
            total += size
            require(total <= MAX_RAW_BYTES and size <= 2 * 1024**2, "training_raw_byte_bound")
            if r.source_contract == "receipt_archive_v1":
                source = prepare_raw_source_custody(
                    self.connection, raw_id, archive_namespace="real-market-training"
                )
                require(source.source_date is not None, "training_source_date_missing")
                assert source.source_date is not None
                page = RawPage(
                    source.body,
                    timestamp(source.collected_at),
                    date.fromisoformat(source.source_date),
                )
                identity = {
                    "raw_id": raw_id,
                    "source_identifier": source.identifier,
                    "receipt_sha256": source.receipt_sha256,
                    "raw_sha256": source.raw_sha256,
                    "source_date": source.source_date,
                }
            else:
                # The old research archive has no receipt owner. Verify its
                # original BLOB/hash directly; never synthesize a receipt/contract.
                body, observed, source_day, fingerprint, kind = self.connection.execute(
                    "SELECT body,collected_at,source_date,sha256,typeof(body) "
                    "FROM raw_pages WHERE id=?",
                    (raw_id,),
                ).fetchone()
                require(
                    kind == "blob" and hashlib.sha256(body).hexdigest() == fingerprint,
                    "legacy_raw_source_fingerprint_mismatch",
                )
                page = RawPage(body, timestamp(observed), date.fromisoformat(source_day))
                identity = {
                    "raw_id": raw_id,
                    "source_identifier": None,
                    "receipt_sha256": None,
                    "raw_sha256": fingerprint,
                    "source_date": source_day,
                }
            try:
                parsed_rows = parser.parse_market(page)
            except ProviderError:
                raise AnalysisError(
                    f"training_original_raw_replay_failed:{page.source_date}:raw-{raw_id}"
                ) from None
            parsed = {(b.ticker, b.trading_date.isoformat()): b for b in parsed_rows}
            require(len(parsed) == len(parsed_rows), "training_duplicate_raw_observation")
            for key in keys:
                if key in parsed and parsed[key] == selected[key]:
                    matched.add(key)
            self.sources.append(identity)
            # No body or whole-market parsed-response cache survives this iteration.
        require(set(selected) == matched, "training_bar_not_reproduced_from_original")
        self.row_count = len(selected)
        self._bars = selected

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        require(
            ticker in self.request.tickers
            and self.request.start <= start <= end <= self.request.end,
            "training_read_outside_verified_scope",
        )
        return [
            bar
            for (symbol, _), bar in self._bars.items()
            if symbol == ticker and start <= bar.trading_date <= end
        ]

    def latest(self, ticker: str, start: date, end: date) -> date | None:
        bars = self.read(ticker, start, end)
        return bars[-1].trading_date if bars else None


def prepare_real_market_training(
    request: RealMarketTrainingRequest,
) -> tuple[FrozenJSON, FrozenJSON]:
    """Read-only source readiness with unchanged real-data sample requirements."""
    with closing(_VerifiedMarket(request)) as market:
        snapshot = capture(
            market,
            list(request.tickers),
            request.start,
            request.end,
            request.cutoff,
            "historical_research",
            acknowledge=True,
            research_calendar=request.research_calendar,
        )
        source = {
            "version": VERSION,
            "request": request.document.to_dict(),
            "request_pin": request.document.identifier,
            "sources": market.sources,
            "rows_verified": market.row_count,
            "applied_research_exclusions": [
                day.isoformat()
                for day in request.research_calendar.incomplete_dates
                if request.start <= day <= request.end
            ]
            if request.research_calendar
            else [],
            "selected_rows_pin": digest(snapshot["rows"]),
            "local_originals_verified": True,
            "issuer_authenticated": False,
            "protected_final_test_read": False,
            "original_receipts_verified": request.source_contract == "receipt_archive_v1",
            "raw_source_custody_contract_compatible": request.source_contract
            == "receipt_archive_v1",
            "source_classification": "local_archived_provider_claim_not_independent_authentication",
        }
    _quiescent(request.database, request.database_pin)
    snapshot["real_market_source"] = source
    samples, exclusions = build_dataset(snapshot)
    split = split_dataset(samples, "historical_research", request.fit_split or request.access_split)
    names: tuple[Direction, ...] = ("up", "down")
    directions = {d: requirements(samples, split, d, Minimums()) for d in names}
    can_train = request.fit_split is not None and any(
        not d["failures"] for d in directions.values()
    )
    inventory = {}
    for ticker, rows in snapshot["rows"].items():
        days = [r["trading_date"] for r in rows]
        inventory[ticker] = {
            "observations": len(days),
            "first_date": min(days, default=None),
            "last_date": max(days, default=None),
            "missing_panel_dates": sorted(
                {r["trading_date"] for series in snapshot["rows"].values() for r in series}
                - set(days)
            ),
        }
    report = FrozenJSON.freeze(
        {
            "version": "real_market_model_readiness_v1",
            "request_pin": request.document.identifier,
            "snapshot_pin": digest(snapshot),
            "status": "READY_FOR_RESEARCH_TRAINING" if can_train else "BLOCKED",
            "can_train": can_train,
            "blockers": ["development_split_not_supplied"]
            if request.fit_split is None
            else [
                d + "." + reason for d, info in directions.items() for reason in info["failures"]
            ],
            "directions": directions,
            "inventory": inventory,
            "exclusions": exclusions,
            "source": source,
            "real_minimums_unchanged": True,
            "global_feature_dates": len({sample.trading_date for sample in samples}),
            "global_feature_tickers": len({sample.ticker for sample in samples}),
            "counts_basis": "inner_development_split"
            if request.fit_split
            else "outer_training_tuning_only_protected_test_not_read",
            "quality_flags": snapshot["quality_flags"],
            "missing_exchange_sessions": None,
            "development_test_only": True,
            "future_prediction_recording_available": False,
            "independently_authenticated_custody": False,
            "outer_final_test_available": False,
            "pit_qualified": False,
            "performance_approved": False,
            "performance_metrics": None,
            "execution_authorized": False,
            "network_requests": 0,
        }
    )
    return FrozenJSON.freeze(snapshot), report


def train_real_market(request: RealMarketTrainingRequest, output: Path) -> FrozenJSON:
    """Train only verified prepared inputs into a new existing-schema artifact store."""
    snapshot, readiness = prepare_real_market_training(request)
    if not readiness.to_dict()["can_train"]:
        return readiness
    require(
        output.is_absolute()
        and output.resolve() == output
        and not output.exists()
        and output != request.database.resolve(),
        "new_training_output_required",
    )
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(output)) as store:
        snapshot_id = store.put("snapshot", snapshot.to_dict())
        training = TrainingService(store).train(snapshot_id, split_configuration=request.fit_split)
        if training["model_version"]:
            model = load_model(store, training["model_version"], "historical_research")
            validate_real_daily_model(store, model, None)
        result = {
            "version": "real_market_training_result_v1",
            "training": training,
            "readiness_pin": readiness.identifier,
            "readiness": readiness.to_dict(),
            "evaluation_recorded": False,
            "operational_eligible": False,
            "pit_qualified": False,
            "performance_approved": False,
            "performance_metrics": None,
            "execution_authorized": False,
            "future_prediction_recording_available": False,
        }
        result["artifact_id"] = store.put("real_market_training", result)
    return FrozenJSON.freeze(result)


def validate_real_daily_model(
    store: AnalysisStore,
    model: dict[str, Any],
    day: date | None,
) -> None:
    """Local frozen-input compatibility, never independent authority or publication."""
    snapshot = store.get("snapshot", model["snapshot_id"])
    source = snapshot.get("real_market_source")
    require(
        type(source) is dict and source.get("version") == VERSION,
        "real_model_verified_training_source_required",
    )
    assert isinstance(source, dict)
    require(source.get("local_originals_verified") is True, "real_model_source_not_verified")
    request = FrozenJSON.freeze(source["request"])
    require(request.identifier == source["request_pin"], "real_model_source_binding")
    d = request.to_dict()
    require(
        d.get("version") == VERSION
        and d.get("source_contract") in ("receipt_archive_v1", "legacy_research_v1"),
        "real_model_source_contract",
    )
    require(
        snapshot["cutoff"] == d["cutoff"]
        and sorted(snapshot["rows"]) == d["tickers"]
        and digest(snapshot["rows"]) == source["selected_rows_pin"],
        "real_model_snapshot_source_binding",
    )
    access = ExplicitResearchSplit.from_dict(d["access_split"])
    fit = ExplicitResearchSplit.from_dict(d["fit_split"])
    _splits(access, d["access_split_pin"], fit, d["fit_split_pin"])
    if day is not None:
        require(
            not access.final_test.start <= day <= access.final_test.end,
            "daily_protected_final_test_forbidden",
        )
        require(day > fit.final_test.end, "daily_model_training_overlap")
    require(
        model["data_origin"] == "real"
        and model["usage_restriction"] == "research_only"
        and model["profiles"] == [["krx", "unadjusted", ""]],
        "real_daily_model_incompatible",
    )
    dataset = store.get("dataset", model["dataset_id"])
    require(
        dataset["split"].get("configuration") == fit.to_dict()
        and snapshot["start"] == fit.training.start.isoformat()
        and snapshot["end"] == fit.final_test.end.isoformat(),
        "real_model_split_binding",
    )
