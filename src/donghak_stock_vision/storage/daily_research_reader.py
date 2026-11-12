"""Pinned, read-only public projection of PR #227's retained research artifacts.

No models, capture credentials, raw replay, artifact writes or publication approval.
Private paths/pins and arbitrary failure messages never enter response models.
"""

import hashlib
import math
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from donghak_stock_vision.storage.daily_research_public_models import (
    DailyResearchReaderError,
    DailyResearchRecord,
    DailyResearchSeries,
    ResearchPeriod,
    ResearchSourceReference,
)
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import FEATURES, THRESHOLD, digest, timestamp
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.signals.dataset import context
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.daily_research import JOB, RESULT, VERSION

MAX_DB_BYTES = 256 * 1024**2
MAX_RESULTS = 100000
SAFE_REASONS = frozenset(
    {
        "missing_anchor_bar",
        "missing_permitted_observation",
        "input_not_available_as_of",
        "stale_data",
        "synthetic_model_on_real_data_forbidden",
        "daily_model_price_basis",
        "fixture_requires_synthetic_model",
        "insufficient_history",
        "no_trade_bar",
        "suspected_gap",
        "suspected_discontinuity",
        "invalid_market_order_or_duplicates",
        "nonfinite_features",
        "constant_volatility",
        "known_incomplete_trading_date",
        "excluded_corporate_action",
        "insufficient_point_in_time_data",
        "missing_verified_session",
        "insufficient_model_data",
        "invalid_features",
        "invalid_model",
        "research_analysis_failed",
    }
)


def _day(value: Any) -> date:
    require(type(value) is str, "daily_public_date")
    day = date.fromisoformat(value)
    require(day.isoformat() == value, "daily_public_date")
    return day


def _reference(kind: str, value: Any) -> str:
    # Domain-separated public references are not the private retained owner PINs.
    return digest(["daily-research-public-v1", kind, value])


def _job(key: str, job: dict[str, Any]) -> None:
    require(job["version"] == VERSION, "daily_public_owner_version")
    day = _day(job["day"])
    tickers = job["tickers"]
    require(
        type(tickers) is list
        and 0 < len(tickers) <= 100
        and tickers == sorted(set(tickers))
        and all(type(t) is str and len(t) == 6 and t.isascii() and t.isdigit() for t in tickers),
        "daily_public_universe",
    )
    for field in ("model_version", "config_pin", "implementation_pin"):
        sha(job[field])
    require(
        type(job["lookback_days"]) is int and 1 <= job["lookback_days"] <= 90, "daily_public_window"
    )
    source = job["source"]
    require(
        source["day"] == job["day"]
        and _day(source["start"]) == day - timedelta(days=job["lookback_days"] - 1),
        "daily_public_window",
    )
    sha(source["input_fingerprint"])
    expected = source["expected_observation_dates"]
    require(
        type(expected) is list and bool(expected) and expected == sorted(set(expected)),
        "daily_public_expected_dates",
    )
    require(
        all(_day(source["start"]) <= _day(d) <= day for d in expected)
        and expected[-1] == job["day"],
        "daily_public_expected_dates",
    )
    sources = source["sources"]
    require(type(sources) is list and 0 < len(sources) <= 366, "daily_public_source_bound")
    origins, days = set(), []
    expected_from_plans: set[str] = set()
    for row in sources:
        c, p = row["completion"], row["proof"]
        plan = ForwardCapturePlan.from_dict(c["plan"])
        require(
            plan.full_market_scope is not None
            and plan.mode == "authorized_provider"
            and plan.document.identifier == sha(c["plan_pin"]),
            "daily_public_plan_binding",
        )
        assert plan.full_market_scope is not None
        expected_from_plans.update(
            d.isoformat()
            for d in plan.full_market_scope.permitted_days
            if _day(source["start"]) <= d <= day
        )
        require(
            c["day"] == plan.day.isoformat() == p["day"] and c["day"] in expected,
            "daily_public_source_day",
        )
        require(
            p["version"] == "verified_operational_completion_v1"
            and c["batch_pin"] == p["batch_pin"]
            and c["observation_count"] == p["observation_count"]
            and type(p["observation_count"]) is int
            and 0 < p["observation_count"] <= 10000
            and c["origin"] == p["origin"],
            "daily_public_completion_binding",
        )
        for value in (c["capture_pin"], c["batch_pin"], p["batch_id"]):
            sha(value)
        require(
            c["authorization"]["grant_pin"] == plan.authorization_reference,
            "daily_public_authorization_binding",
        )
        timestamp(c["at"])
        origins.add(p["origin"])
        days.append(c["day"])
    require(days == sorted(set(days)), "daily_public_duplicate_source_day")
    require(set(expected) == expected_from_plans, "daily_public_expected_plan_scope")
    require(
        origins in ({"real_provider_https_local_capture"}, {"authorized_provider_mock_fixture"}),
        "daily_public_source_origin",
    )
    origin = "real" if origins == {"real_provider_https_local_capture"} else "synthetic_fixture"
    require(source["data_origin"] == origin, "daily_public_source_origin")
    sha(key)


def _result(row: dict[str, Any], job: dict[str, Any]) -> None:
    require(
        row["ticker"] in job["tickers"]
        and type(row["attempt"]) is int
        and 1 <= row["attempt"] <= 20,
        "daily_public_result_owner",
    )
    at = timestamp(row["checked_at"])
    require(at.astimezone(SEOUL).date() > _day(job["day"]), "daily_public_result_time")
    state = row["status"]
    require(state in ("completed", "blocked", "failed"), "daily_public_result_status")
    value = row["research_score"]
    if state != "completed":
        require(value is None, "daily_public_unfinished_score")
        return
    require(
        (at.astimezone(SEOUL).date() - _day(job["day"])).days <= 7,
        "daily_public_completed_input_age",
    )
    require(len(job["source"]["expected_observation_dates"]) >= 11, "daily_public_feature_history")
    features = row["features"]
    require(
        set(features) == set(FEATURES)
        and all(type(v) in (float, int) and math.isfinite(v) for v in features.values()),
        "daily_public_features",
    )
    require(
        row["context"] == context(tuple(features[name] for name in FEATURES)),
        "daily_public_context",
    )
    require(
        (row["context"] is None and value is None)
        or (
            row["context"] in ("up", "down")
            and type(value) in (float, int)
            and math.isfinite(value)
            and 0 <= value <= 1
        ),
        "daily_public_score",
    )
    require(
        set(job["source"]["expected_observation_dates"])
        == {r["completion"]["day"] for r in job["source"]["sources"]},
        "daily_public_completed_coverage",
    )


def _record(
    job_id: str, job: dict[str, Any], ticker: str, latest: tuple[str, dict[str, Any]] | None
) -> DailyResearchRecord:
    result_id, row = latest if latest else (None, {})
    state = row.get("status", "pending")
    completed = state == "completed"
    source = job["source"]
    days = source["expected_observation_dates"]
    expected_period = ResearchPeriod(start=_day(days[0]), end=_day(days[-1]))
    value, direction = row.get("research_score"), row.get("context")
    reason = None if completed else row.get("reason")
    if not completed:
        reason = (
            reason
            if reason in SAFE_REASONS
            else ("analysis_not_recorded" if state == "pending" else "analysis_" + state)
        )
    signal: Literal["up", "down", "neutral", "unavailable"] = "unavailable"
    if completed:
        signal = "neutral"
        if value is not None and value >= THRESHOLD:
            signal = "up" if direction == "up" else "down"
    return DailyResearchRecord(
        analysisId=result_id,
        jobReference=_reference("job", job_id),
        ticker=ticker,
        tradingDate=_day(job["day"]),
        analyzedAt=timestamp(row["checked_at"]) if row else None,
        modelId=job["model_version"],
        modelVersion=job["model_version"],
        status=state,
        reasonCode=reason,
        attempt=row.get("attempt"),
        dataOrigin=source["data_origin"],
        usageRestriction="research_only"
        if source["data_origin"] == "real"
        else "synthetic_test_only",
        requestedPeriod=ResearchPeriod(start=_day(source["start"]), end=_day(job["day"])),
        observedPeriod=expected_period if completed else None,
        inputFingerprint=source["input_fingerprint"],
        sources=tuple(
            ResearchSourceReference(
                referenceId=_reference("source", [job_id, r["proof"]["batch_id"]]),
                tradingDate=_day(r["proof"]["day"]),
                origin=r["proof"]["origin"],
            )
            for r in source["sources"]
        ),
        researchScore=value,
        researchContext=(
            "prior_decline"
            if direction == "up"
            else "prior_rise"
            if direction == "down"
            else "flat"
        )
        if completed
        else None,
        researchSignal=signal,
    )


class _ImmutableAnalysisStore(ReadOnlyAnalysisStore):
    """Reuse artifact checks, with immutable SQLite to avoid WAL/SHM creation."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        self.connection.execute("PRAGMA query_only=ON")


class DailyResearchReader:
    """Startup-loaded immutable projection. Provision a quiescent pinned snapshot.

    Only safe DTOs survive loading; no model execution or private source path is
    retained. This is integrity of retained findings, not fresh source verification.
    """

    def __init__(self, path: Path, *, expected_sha256: str) -> None:
        try:
            self._load(path, expected_sha256)
        except Exception:
            raise DailyResearchReaderError("daily_research_snapshot_invalid") from None

    def _load(self, path: Path, pin: str) -> None:
        sha(pin)

        def check_file() -> None:
            require(
                path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_DB_BYTES,
                "daily_public_file_bound",
            )
            require(
                not any(Path(str(path) + s).exists() for s in ("-wal", "-journal")),
                "daily_public_active_sidecar",
            )
            with path.open("rb") as stream:
                require(
                    hashlib.file_digest(stream, "sha256").hexdigest() == pin,
                    "daily_public_snapshot_pin",
                )

        check_file()
        with closing(_ImmutableAnalysisStore(path)) as store:
            store.connection.execute("BEGIN")
            require(
                store.connection.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                "daily_public_sqlite_integrity",
            )
            jobs: dict[str, dict[str, Any]] = {}
            for key, length in store.connection.execute(
                "SELECT id,length(payload) FROM analysis_artifacts WHERE kind=? LIMIT 367", (JOB,)
            ):
                require(len(jobs) < 366 and length <= 2 * 1024**2, "daily_public_job_bound")
                job = store.get(JOB, key)
                _job(key, job)
                require(
                    all(old["day"] != job["day"] for old in jobs.values()),
                    "daily_public_conflicting_job",
                )
                jobs[key] = job
            attempts: dict[tuple[str, str], dict[int, tuple[str, dict[str, Any]]]] = {}
            count = 0
            for key, length in store.connection.execute(
                "SELECT id,length(payload) FROM analysis_artifacts WHERE kind=? LIMIT ?",
                (RESULT, MAX_RESULTS + 1),
            ):
                count += 1
                require(count <= MAX_RESULTS and length <= 65536, "daily_public_result_bound")
                row = store.get(RESULT, key)
                require(row["job_id"] in jobs, "daily_public_orphan_result")
                _result(row, jobs[row["job_id"]])
                series = attempts.setdefault((row["job_id"], row["ticker"]), {})
                require(row["attempt"] not in series, "daily_public_conflicting_attempt")
                series[row["attempt"]] = key, row
            public: dict[str, list[DailyResearchRecord]] = {}
            for key, job in jobs.items():
                for ticker in job["tickers"]:
                    rows = attempts.get((key, ticker), {})
                    order = sorted(rows)
                    require(order == list(range(1, len(order) + 1)), "daily_public_missing_attempt")
                    require(
                        all(rows[i][1]["status"] != "completed" for i in order[:-1]),
                        "daily_public_completed_retry",
                    )
                    record = _record(key, job, ticker, rows[order[-1]] if order else None)
                    public.setdefault(ticker, []).append(record)
        check_file()
        self._by_ticker = {
            ticker: tuple(sorted(records, key=lambda r: r.tradingDate, reverse=True))
            for ticker, records in public.items()
        }

    def series(
        self,
        ticker: str,
        *,
        trading_date: date | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DailyResearchSeries:
        if (trading_date is not None and (start is not None or end is not None)) or (
            start is not None and end is not None and start > end
        ):
            raise DailyResearchReaderError("invalid_research_date_filter")
        if not 1 <= limit <= 200 or not 0 <= offset <= 100000:
            raise DailyResearchReaderError("invalid_research_pagination")
        all_rows = self._by_ticker.get(ticker, ())
        selected = [
            r
            for r in all_rows
            if (trading_date is None or r.tradingDate == trading_date)
            and (start is None or r.tradingDate >= start)
            and (end is None or r.tradingDate <= end)
        ]
        success = [
            (r.analyzedAt, r)
            for r in all_rows
            if r.status == "completed" and r.analyzedAt is not None
        ]
        latest = max(success, key=lambda pair: (pair[0], pair[1].tradingDate), default=None)
        return DailyResearchSeries(
            ticker=ticker,
            availability="recorded" if selected else "no_recorded_analysis",
            records=tuple(selected[offset : offset + limit]),
            totalRecords=len(selected),
            offset=offset,
            limit=limit,
            lastSuccessfulAnalyzedAt=latest[0] if latest else None,
            lastSuccessfulTradingDate=latest[1].tradingDate if latest else None,
            lastSuccessfulDataOrigin=latest[1].dataOrigin if latest else None,
        )
