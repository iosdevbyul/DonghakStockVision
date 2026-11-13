"""Versioned, read-only research/operational composition for the existing trainer.

Original outer roles are immutable. A new diagnostic test is strictly after the
sealed outer Final Test; it is not a fresh prospective or independent holdout.
No provider, scheduler, protected data write, new estimator or market DB owner.
"""

import hashlib
import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import FEATURES, Direction, Minimums, digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import EvaluationService, TrainingService, load_model
from donghak_stock_vision.signals.dataset import build_dataset, requirements, split_dataset
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.capture_raw_source_contract import prepare_raw_source_custody
from donghak_stock_vision.storage.krx_authorization import bounded
from donghak_stock_vision.storage.krx_backfill import (
    DB as BACKFILL_DB,
)
from donghak_stock_vision.storage.krx_backfill import (
    _reference,
    _source,
    context,
    load_plan,
    load_policy,
    read_pin,
)
from donghak_stock_vision.storage.operational_capture import verify_stored_capture
from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
from donghak_stock_vision.storage.real_market_training import (
    MAX_RAW_BYTES,
    MAX_ROWS,
    RealMarketTrainingRequest,
    _quiescent,
    _VerifiedMarket,
    validate_dataset_model,
)

REQUEST = "real_market_dataset_request_v1"
SOURCE = "real_market_dataset_source_v1"


def document(path: str, maximum: int = 1024 * 1024) -> dict[str, Any]:
    return FrozenJSON(bounded(Path(path), maximum).decode()).to_dict()


def _protocol(d: dict[str, Any]) -> tuple[dict[str, Any], ExplicitResearchSplit]:
    value = fields(d["protocol"], "path sha256")
    path = Path(value["path"])
    from donghak_stock_vision.storage.historical_readiness import _file_hash

    require(_file_hash(path) == sha(value["sha256"]), "dataset_original_protocol_pin_mismatch")
    protocol = document(str(path))
    require(
        protocol["features"] == list(FEATURES)
        and protocol["feature_version"] == "ohlcv_value_v1"
        and protocol["label_version"] == "reversal_barrier_v1"
        and protocol["horizon_sessions"] == 5,
        "dataset_original_feature_contract_mismatch",
    )
    return protocol, ExplicitResearchSplit.from_dict(protocol["split"])


def _roles(access: ExplicitResearchSplit, fit: ExplicitResearchSplit) -> None:
    require(
        fit.training == access.training
        and fit.validation == access.validation
        and fit.final_test.start > access.final_test.end,
        "dataset_original_roles_must_be_preserved",
    )


def _request(d: dict[str, Any]) -> tuple[RealMarketTrainingRequest, ExplicitResearchSplit]:
    fields(
        d,
        "version dataset_version research operational protocol tickers cutoff diagnostic_test "
        "known_missing_sessions",
    )
    require(d["version"] == REQUEST, "unsupported_real_dataset_version")
    require(
        type(d["dataset_version"]) is str and 0 < len(d["dataset_version"]) <= 100,
        "dataset_version_required",
    )
    protocol, access = _protocol(d)
    research = fields(d["research"], "database pin source_contract")
    test = fields(d["diagnostic_test"], "start end")
    fit = ExplicitResearchSplit(
        access.training,
        access.validation,
        ResearchDateRange(date.fromisoformat(test["start"]), date.fromisoformat(test["end"])),
    )
    _roles(access, fit)
    require(type(d["tickers"]) is list, "dataset_tickers_required")
    request = RealMarketTrainingRequest(
        Path(research["database"]),
        sha(research["pin"]),
        access,
        access.identifier,
        None,
        None,
        tuple(d["tickers"]),
        timestamp(d["cutoff"]),
        ResearchCalendarPolicy.from_dict(protocol["research_calendar"]),
        research["source_contract"],
    )
    request.validate()
    require(
        fit.final_test.end < timestamp(d["cutoff"]).astimezone(SEOUL).date(),
        "dataset_cutoff_after_test_required",
    )
    missing = d["known_missing_sessions"]
    require(
        type(missing) is list and len(missing) <= 64 and missing == sorted(set(missing)),
        "dataset_missing_session_bound",
    )
    for day in missing:
        parsed = date.fromisoformat(day)
        require(
            fit.training.start <= parsed <= fit.final_test.end,
            "dataset_missing_session_outside_window",
        )
    return request, fit


def _operational(
    d: dict[str, Any], request: RealMarketTrainingRequest, fit: ExplicitResearchSplit
) -> tuple[list[DailyBar], list[dict[str, Any]]]:
    value = fields(d["operational"], "root pin evidence")
    root = Path(value["root"])
    _quiescent(root / DB, sha(value["pin"]))
    require(
        type(value["evidence"]) is list and len(value["evidence"]) <= 100,
        "dataset_operational_evidence_bound",
    )
    proofs: list[dict[str, Any]] = []
    with closing(OperationalMarketStore(root)) as store:
        store.connection.execute("BEGIN")
        for entry in value["evidence"]:
            if entry["kind"] == "backfill":
                e = fields(entry, "kind policy policy_pin_file plan plan_pin_file")
                policy = load_policy(Path(e["policy"]), read_pin(Path(e["policy_pin_file"])))
                plan = load_plan(Path(e["plan"]), read_pin(Path(e["plan_pin_file"])))
                archive = Path(policy.values["archive_root"]) / BACKFILL_DB
                with closing(sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True)) as db:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("BEGIN")
                    for day in plan.document.to_dict()["dates"]:
                        parsed = date.fromisoformat(day)
                        # Restrict before original response replay, including the protected test.
                        if not fit.final_test.start <= parsed <= fit.final_test.end:
                            continue
                        page, meta, owner = _source(db, policy, plan, day)
                        _, original = context(policy, page.collected_at)
                        require(
                            meta["http_status"] == 200
                            and meta["complete"] is True
                            and meta["error"] is None,
                            "dataset_complete_http_required",
                        )
                        reference = _reference(policy, plan, owner, original)
                        proofs.append(_batch(store, reference))
            elif entry["kind"] == "forward":
                e = fields(
                    entry,
                    "kind capture_root plan plan_pin_file attempt capture_pin_file "
                    "batch_pin_file authorization authorization_policy",
                )
                plan_doc = ForwardCapturePlan.from_dict(document(e["plan"]))
                require(
                    fit.final_test.start <= plan_doc.day <= fit.final_test.end,
                    "dataset_forward_source_outside_diagnostic_test",
                )
                proof = verify_stored_capture(
                    root,
                    Path(e["capture_root"]),
                    plan_doc,
                    read_pin(Path(e["plan_pin_file"])),
                    e["attempt"],
                    read_pin(Path(e["capture_pin_file"])),
                    read_pin(Path(e["batch_pin_file"])),
                    authorization=FrozenJSON.freeze(e["authorization"]),
                    authorization_policy=e["authorization_policy"],
                ).to_dict()
                require(
                    proof["origin"] == "real_provider_https_local_capture",
                    "dataset_fixture_is_not_real_source",
                )
                proofs.append(proof)
            else:
                raise ValueError("unsupported_dataset_operational_evidence")
        ids = [p["batch_id"] for p in proofs]
        require(len(ids) == len(set(ids)) and len(ids) <= 100, "dataset_duplicate_batch")
        bars: list[DailyBar] = []
        for batch_id in ids:
            rows = store.connection.execute(
                "SELECT o.payload FROM market_observations o JOIN observation_sources s "
                "USING(market,trading_date,ticker) JOIN ingestion_batches b ON b.raw_id=s.raw_id "
                "WHERE b.batch_id=? AND o.ticker IN ("
                + ",".join("?" for _ in request.tickers)
                + ") ORDER BY o.ticker,o.trading_date LIMIT ?",
                (batch_id, *request.tickers, MAX_ROWS + 1),
            ).fetchall()
            require(len(bars) + len(rows) <= MAX_ROWS, "dataset_operational_row_bound")
            bars.extend(DailyBar.from_dict(FrozenJSON(row[0]).to_dict()) for row in rows)
    _quiescent(root / DB, value["pin"])
    return bars, proofs


def _batch(store: OperationalMarketStore, reference: FrozenJSON) -> dict[str, Any]:
    r = reference.to_dict()
    require(
        r["origin"] == "real_provider_https_local_capture", "dataset_fixture_is_not_real_source"
    )
    row = store.connection.execute(
        "SELECT raw_id FROM ingestion_batches WHERE batch_id=?", (reference.identifier,)
    ).fetchone()
    require(row is not None, "dataset_original_batch_missing")
    batch = store._verify_batch(reference.identifier, row[0])
    require(batch.to_dict()["request"]["source_capture"] == r, "dataset_source_reference_mismatch")
    return dict(
        batch_id=reference.identifier, batch_pin=batch.identifier, day=r["day"], source_capture=r
    )


class _Panel:
    def __init__(self, rows: dict[str, list[DailyBar]]) -> None:
        self.rows = rows

    def read(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        return [b for b in self.rows[ticker] if start <= b.trading_date <= end]

    def latest(self, ticker: str, start: date, end: date) -> date | None:
        rows = self.read(ticker, start, end)
        return rows[-1].trading_date if rows else None


def _missing_sources(
    request: RealMarketTrainingRequest, missing: list[str]
) -> list[dict[str, Any]]:
    evidence = []
    with closing(
        sqlite3.connect(request.database.as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        for day in missing:
            require(
                not request.access_split.final_test.start.isoformat()
                <= day
                <= request.access_split.final_test.end.isoformat(),
                "dataset_missing_source_protected",
            )
            rows = db.execute(
                "SELECT id,substr(body,1,2097153),sha256,collected_at,provider,ticker,length(body) "
                "FROM raw_pages "
                "WHERE source_date=? LIMIT 9",
                (day,),
            ).fetchall()
            require(0 < len(rows) <= 8, "dataset_missing_original_required")
            for raw_id, body, fingerprint, observed, provider, scope, size in rows:
                require(
                    provider == "krx"
                    and scope == "@krx_market:KOSPI"
                    and 0 < size <= 2 * 1024**2
                    and type(body) is bytes
                    and hashlib.sha256(body).hexdigest() == fingerprint,
                    "dataset_missing_original_integrity",
                )
                if request.source_contract == "receipt_archive_v1":
                    original = prepare_raw_source_custody(
                        db, raw_id, archive_namespace="real-market-training"
                    )
                    require(original.raw_sha256 == fingerprint, "dataset_missing_receipt_mismatch")
                require(
                    FrozenJSON(body.decode()).to_dict().get("OutBlock_1") == [],
                    "dataset_missing_original_not_empty",
                )
                evidence.append(
                    dict(
                        raw_id=raw_id,
                        raw_sha256=fingerprint,
                        source_date=day,
                        collected_at=observed,
                        classification="retained_empty_original_cause_unknown",
                    )
                )
    return evidence


def _merge_bars(bars: dict[tuple[str, date], DailyBar], operational: list[DailyBar]) -> int:
    duplicate_count = 0
    for bar in operational:
        key = bar.ticker, bar.trading_date
        if key in bars:
            require(bars[key].content() == bar.content(), "dataset_conflicting_duplicate")
            duplicate_count += 1
            # Earliest retained collection time is deterministic; both source owners retained.
            bar = min((bar, bars[key]), key=lambda b: b.collected_at)
        bars[key] = bar
    return duplicate_count


def prepare_dataset(d: dict[str, Any]) -> tuple[FrozenJSON, FrozenJSON]:
    request, fit = _request(d)
    bars: dict[tuple[str, date], DailyBar] = {}
    research_proofs: list[dict[str, Any]] = []
    raw_bytes = raw_responses = 0
    for window in (fit.training, fit.validation, fit.final_test):
        with closing(_VerifiedMarket(request, source_window=window)) as market:
            raw_bytes += market.raw_bytes_verified
            raw_responses += market.raw_responses_verified
            require(
                raw_bytes <= MAX_RAW_BYTES and raw_responses <= 4096,
                "dataset_aggregate_research_source_bound",
            )
            for ticker in request.tickers:
                for bar in market.read(ticker, window.start, window.end):
                    bars[ticker, bar.trading_date] = bar
            research_proofs.append(
                dict(
                    window=window.to_dict(), sources=market.sources, rows_verified=market.row_count
                )
            )
    operational, proofs = _operational(d, request, fit)
    duplicate_count = _merge_bars(bars, operational)
    require(len(bars) <= MAX_ROWS, "dataset_selected_row_bound")
    rows = {
        t: sorted(
            (b for (ticker, _), b in bars.items() if ticker == t), key=lambda b: b.trading_date
        )
        for t in request.tickers
    }
    snapshot = capture(
        _Panel(rows),
        list(request.tickers),
        fit.training.start,
        fit.final_test.end,
        request.cutoff,
        "historical_research",
        acknowledge=True,
        research_calendar=request.research_calendar,
    )
    require(not any(snapshot["unavailable_dates"].values()), "dataset_source_after_cutoff")
    # Missing sessions are NOT removed from the calendar. Existing feature/label
    # check_segment rejects every window spanning them (including the sealed gap).
    known_missing = d["known_missing_sessions"]
    for ticker in request.tickers:
        require(
            not any(row["trading_date"] in known_missing for row in snapshot["rows"][ticker]),
            "dataset_missing_session_has_observation",
        )
        snapshot["unavailable_dates"][ticker] = list(known_missing)
    public_request = dict(
        version=REQUEST,
        dataset_version=d["dataset_version"],
        research_pin=d["research"]["pin"],
        operational_pin=d["operational"]["pin"],
        original_protocol_sha256=d["protocol"]["sha256"],
        original_source_pin=_protocol(d)[0]["source_market_sha256"],
        access_split=request.access_split.to_dict(),
        fit_split=fit.to_dict(),
        tickers=list(request.tickers),
        cutoff=request.cutoff.isoformat(),
        known_missing_sessions=known_missing,
        research_calendar=request.research_calendar.to_dict()
        if request.research_calendar
        else None,
    )
    source = dict(
        missing_originals=_missing_sources(request, known_missing),
        version=SOURCE,
        request=public_request,
        request_pin=digest(public_request),
        input_request_pin=digest(d),
        research=research_proofs,
        operational=proofs,
        local_originals_verified=True,
        protected_final_test_read=False,
        diagnostic_test_only=True,
        selected_rows_pin=digest(snapshot["rows"]),
    )
    snapshot["real_market_source"] = source
    # Hard barrier for labels/features across the sealed, deliberately unread interval.
    protected = request.access_split.final_test
    for ticker in request.tickers:
        snapshot["unavailable_dates"][ticker] += [
            protected.start.isoformat(),
            protected.end.isoformat(),
        ]
    samples, exclusions = build_dataset(snapshot)
    split = split_dataset(samples, "historical_research", fit)
    directions: tuple[Direction, ...] = ("up", "down")
    checks = {
        direction: requirements(samples, split, direction, Minimums()) for direction in directions
    }
    manifest = dict(
        version="real_market_dataset_manifest_v1",
        dataset_version=d["dataset_version"],
        snapshot_pin=digest(snapshot),
        source_pin=digest(source),
        input_request_pin=digest(d),
        source=source,
        observations=sum(len(v) for v in snapshot["rows"].values()),
        trading_dates=len({b["trading_date"] for v in snapshot["rows"].values() for b in v}),
        tickers=len(snapshot["rows"]),
        inventory={
            t: dict(
                observations=len(v),
                first_date=v[0]["trading_date"] if v else None,
                last_date=v[-1]["trading_date"] if v else None,
                no_trade_rows=sum(
                    min(b[k] for k in ("open", "high", "low", "close", "volume", "trading_value"))
                    <= 0
                    for b in v
                ),
            )
            for t, v in snapshot["rows"].items()
        },
        missing_observations={
            t: sorted(
                {b["trading_date"] for v in snapshot["rows"].values() for b in v}
                - {b["trading_date"] for b in v}
            )
            for t, v in snapshot["rows"].items()
        },
        missing_reason="unknown_not_inferred_listing_delisting_or_suspension",
        source_classification="local_archived_provider_claim_not_independent_authentication",
        issuer_authenticated=False,
        independently_authenticated_custody=False,
        publication_time_authenticated=False,
        session_continuity_verified=False,
        known_missing_sessions=known_missing,
        duplicate_rows_deduplicated=duplicate_count,
        feature_samples=len(samples),
        exclusions=exclusions,
        directions=checks,
        can_train=any(not c["failures"] for c in checks.values()),
        protected_final_test_read=False,
        performance_approved=False,
        performance_metrics=None,
        pit_qualified=False,
        execution_authorized=False,
        network_requests=0,
    )
    _quiescent(request.database, request.database_pin)
    _protocol(d)
    return FrozenJSON.freeze(snapshot), FrozenJSON.freeze(manifest)


def train_dataset(d: dict[str, Any], output: Path) -> FrozenJSON:
    require(
        output.is_absolute() and output.resolve() == output and not output.exists(),
        "new_training_output_required",
    )
    require(
        not output.is_relative_to(Path(d["operational"]["root"])),
        "dataset_output_inside_operational_storage",
    )
    snapshot, manifest = prepare_dataset(d)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(fd)
    fit = ExplicitResearchSplit.from_dict(
        snapshot.to_dict()["real_market_source"]["request"]["fit_split"]
    )
    with closing(AnalysisStore(output)) as store:
        ids = store.put_many(
            [("snapshot", snapshot.to_dict()), ("real_market_dataset", manifest.to_dict())]
        )
        training = TrainingService(store).train(ids[0], split_configuration=fit)
        evaluation_id = None
        if training["model_version"]:
            model = load_model(store, training["model_version"], "historical_research")
            validate_dataset_model(store, model, snapshot.to_dict(), None)
            evaluation_id = EvaluationService(store).evaluate(
                training["model_version"], "historical_research"
            )["evaluation_id"]
        result = dict(
            version="real_market_dataset_training_v1",
            evaluation_id=evaluation_id,
            model_validation_completed=evaluation_id is not None,
            training=training,
            manifest_id=ids[1],
            snapshot_id=ids[0],
            diagnostic_test_only=True,
            protected_final_test_read=False,
            performance_approved=False,
            performance_metrics=None,
            pit_qualified=False,
            execution_authorized=False,
            future_prediction_recording_available=False,
            network_requests=0,
        )
        result["artifact_id"] = store.put("real_market_dataset_training", result)
    return FrozenJSON.freeze(result)
