"""Offline daily research over original verified collection completions.

No capture, prediction journal, model fitting, model registration or market writes.
The existing AnalysisStore owns immutable jobs and per-ticker attempt artifacts.
"""

import fcntl
import os
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import FEATURES, AnalysisError, canonical, digest, timestamp
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_ticker
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.inference import LinearInference
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.dataset import context
from donghak_stock_vision.storage.analysis import (
    AnalysisStore,
    ReadOnlyAnalysisStore,
)
from donghak_stock_vision.storage.krx_backlog import _external, _records
from donghak_stock_vision.storage.krx_recurring import POLICY
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_capture import verify_stored_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore

JOB = "daily_research_job"
RESULT = "daily_research_result"
VERSION = "daily_research_v1"


def _code_pin() -> str:
    from donghak_stock_vision.features import engine
    from donghak_stock_vision.models import inference, linear
    from donghak_stock_vision.signals import dataset

    modules = (__file__, engine.__file__, inference.__file__, linear.__file__, dataset.__file__)
    return digest([Path(p).read_text() for p in modules if p is not None])


def _inputs(
    config: SchedulerConfig,
    day: date,
    tickers: tuple[str, ...],
    lookback_days: int,
    as_of: datetime,
) -> tuple[dict[str, Any], dict[str, list[DailyBar]]]:
    require(type(lookback_days) is int and 1 <= lookback_days <= 90, "daily_history_bound")
    require(0 < len(tickers) <= 100 and tuple(sorted(set(tickers))) == tickers, "daily_universe")
    for ticker in tickers:
        validate_ticker(ticker)
    require(day < timestamp(as_of).astimezone(SEOUL).date(), "daily_completed_date_required")
    require(_external(config)["config_pin"] == config.document.identifier, "daily_registry_binding")
    start = day - timedelta(days=lookback_days - 1)
    records = [r for r in _records(config) if start.isoformat() <= r["day"] <= day.isoformat()]
    require(any(r["day"] == day.isoformat() for r in records), "daily_verified_completion_missing")
    proofs: list[dict[str, Any]] = []
    expected: set[date] = set()
    batch_ids: list[str] = []
    # Each original response is replayed once here, never once per selected ticker.
    for record in sorted(records, key=lambda r: r["day"]):
        plan = ForwardCapturePlan.from_dict(record["plan"])
        require(plan.full_market_scope is not None, "daily_full_market_source_required")
        assert plan.full_market_scope is not None
        expected.update(d for d in plan.full_market_scope.permitted_days if start <= d <= day)
        proof = verify_stored_capture(
            config.path("operational_root"),
            config.path("capture_root"),
            plan,
            record["plan_pin"],
            record["attempt"],
            record["capture_pin"],
            record["batch_pin"],
            authorization=FrozenJSON.freeze(record["authorization"]),
            authorization_policy=POLICY,
        ).to_dict()
        require(
            proof["day"] == record["day"]
            and proof["origin"] == record["origin"]
            and proof["observation_count"] == record["observation_count"],
            "daily_completion_mismatch",
        )
        batch_ids.append(proof["batch_id"])
        proofs.append({"completion": record, "proof": proof})
    origins = {p["proof"]["origin"] for p in proofs}
    require(
        len(origins) == 1
        and origins
        <= {
            "real_provider_https_local_capture",
            "authorized_provider_mock_fixture",
        },
        "daily_mixed_or_unsupported_source_origin",
    )
    histories: dict[str, list[DailyBar]] = {}
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        require(store.validate().to_dict()["valid"], "daily_storage_invalid")
        with store.connection:
            store.connection.execute("BEGIN")
            for proof in proofs:
                row = store.connection.execute(
                    "SELECT raw_id FROM ingestion_batches WHERE batch_id=?",
                    (proof["proof"]["batch_id"],),
                ).fetchone()
                require(row is not None, "daily_verified_batch_missing")
                batch = store._verify_batch(proof["proof"]["batch_id"], row[0])
                require(batch.identifier == proof["proof"]["batch_pin"], "daily_batch_changed")
            # Only source-linked selected rows; dates in an unverified batch cannot leak in.
            for ticker in tickers:
                rows = store.connection.execute(
                    "SELECT DISTINCT o.payload FROM market_observations o "
                    "JOIN observation_sources s USING(market,trading_date,ticker) "
                    "JOIN ingestion_batches b ON b.raw_id=s.raw_id "
                    "WHERE o.market='KOSPI' AND o.ticker=? AND o.trading_date BETWEEN ? AND ? "
                    "AND b.batch_id IN (" + ",".join("?" for _ in batch_ids) + ") "
                    "ORDER BY o.trading_date",
                    (ticker, start.isoformat(), day.isoformat(), *batch_ids),
                ).fetchall()
                histories[ticker] = [
                    DailyBar.from_dict(FrozenJSON(row[0]).to_dict()) for row in rows
                ]
    return {
        "sources": proofs,
        "expected_observation_dates": sorted(d.isoformat() for d in expected),
        "start": start.isoformat(),
        "day": day.isoformat(),
        "input_fingerprint": digest(
            {t: [b.to_dict() for b in bars] for t, bars in histories.items()}
        ),
        "data_origin": "real"
        if all(p["proof"]["origin"] == "real_provider_https_local_capture" for p in proofs)
        else "synthetic_fixture",
    }, histories


def _analyze(
    model: dict[str, Any],
    bars: list[DailyBar],
    source: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    require(bool(bars) and bars[-1].trading_date.isoformat() == source["day"], "missing_anchor_bar")
    if [b.trading_date.isoformat() for b in bars] != source["expected_observation_dates"]:
        raise AnalysisError("missing_permitted_observation")
    if any(b.collected_at > timestamp(as_of) for b in bars):
        raise AnalysisError("input_not_available_as_of")
    if (timestamp(as_of).astimezone(SEOUL).date() - bars[-1].trading_date).days > 7:
        raise AnalysisError("stale_data")
    if source["data_origin"] == "real":
        require(model["data_origin"] == "real", "synthetic_model_on_real_data_forbidden")
        require(model["profiles"] == [["krx", "unadjusted", ""]], "daily_model_price_basis")
    else:
        require(model["data_origin"] == "synthetic", "fixture_requires_synthetic_model")
    metadata: dict[str, Any] = {
        "quality": None,
        "unavailable_dates": {},
        "session_basis": "observed_bars_unverified",
    }
    values = feature_vector(bars, metadata)
    direction = context(values)
    value = None
    if direction is not None:
        parameters = model["directions"][direction]["parameters"]
        if parameters is None:
            raise AnalysisError("insufficient_model_data")
        value = LinearInference(canonical(parameters)).score(FEATURES, values)
    return {
        "features": dict(zip(FEATURES, values, strict=True)),
        "context": direction,
        "research_score": value,
        "status": "completed",
    }


def _output_path(config: SchedulerConfig, output: Path, model_db: Path) -> None:
    require(
        output.is_absolute() and output.resolve() == output and not output.is_symlink(),
        "daily_canonical_output_required",
    )
    require(
        output != model_db.resolve()
        and not (output.exists() and model_db.exists() and output.samefile(model_db)),
        "daily_separate_model_source_required",
    )
    for name in ("capture_root", "operational_root", "policy_root", "runner_root"):
        root = config.path(name)
        require(
            not output.is_relative_to(root) and not root.is_relative_to(output),
            "daily_separate_output_required",
        )
    if output.exists():
        require(output.stat().st_mode & 0o077 == 0, "daily_private_output_required")
        with closing(ReadOnlyAnalysisStore(output)) as store:
            kinds = {
                r[0]
                for r in store.connection.execute("SELECT DISTINCT kind FROM analysis_artifacts")
            }
            require(kinds <= {JOB, RESULT}, "daily_unrelated_output_forbidden")
            require(
                store.connection.execute("SELECT COUNT(*) FROM operational_registry").fetchone()[0]
                == 0,
                "daily_unrelated_output_forbidden",
            )


@contextmanager
def _writer(output: Path) -> Iterator[AnalysisStore]:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(output) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AnalysisError("daily_analysis_busy") from exc
        if not output.exists():
            created = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(created)
        with closing(AnalysisStore(output)) as store:
            yield store
    finally:
        os.close(fd)


def _latest(store: AnalysisStore, job_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for _, row in store.all(RESULT):
        if row["job_id"] == job_id:
            old = latest.get(row["ticker"])
            if old is None or old["attempt"] < row["attempt"]:
                latest[row["ticker"]] = row
    return latest


def daily_status(output: Path, day: date, as_of: datetime) -> FrozenJSON:
    """Read-only status of retained analysis, not a fresh source authorization check."""
    with closing(ReadOnlyAnalysisStore(output)) as store:
        store.connection.execute("BEGIN")
        jobs = [(key, job) for key, job in store.all(JOB) if job["day"] == day.isoformat()]
        require(len(jobs) == 1, "daily_job_missing_or_ambiguous")
        job_id, job = jobs[0]
        latest = _latest(store, job_id)
        counts = {
            s: sum(r["status"] == s for r in latest.values())
            for s in ("completed", "blocked", "failed")
        }
        successful = []
        for key, previous in store.all(JOB):
            rows = _latest(store, key)
            if all(rows.get(t, {}).get("status") == "completed" for t in previous["tickers"]):
                successful.append(previous["day"])
        return FrozenJSON.freeze(
            {
                "version": VERSION,
                "day": day.isoformat(),
                "job_id": job_id,
                "collection_status": "verified_original_completion_at_analysis",
                "storage_status": "verified_at_analysis",
                "analysis_status": "completed"
                if counts["completed"] == len(job["tickers"])
                else "incomplete",
                "completed_tickers": counts["completed"],
                "failed_tickers": counts["failed"],
                "blocked_tickers": counts["blocked"],
                "pending_tickers": len(job["tickers"]) - len(latest),
                "results": latest,
                "input_latest_trading_date": day.isoformat(),
                "input_age_calendar_days": (timestamp(as_of).astimezone(SEOUL).date() - day).days,
                "last_successful_analysis_day": max(successful, default=None),
                "input_currently_fresh": 1
                <= (timestamp(as_of).astimezone(SEOUL).date() - day).days
                <= 7,
                "model_version": job["model_version"],
                "data_origin": job["source"]["data_origin"],
                "usage_restriction": "research_only"
                if job["source"]["data_origin"] == "real"
                else "synthetic_test_only",
                "future_prediction_recording_available": False,
                "future_prediction_blocker": "provider_backed_prospective_predictions_unsupported",
                "pit_qualified": False,
                "performance_approved": False,
                "performance_metrics": None,
                "execution_authorized": False,
                "paper_execution_eligible": False,
                "forward_signal_eligible": False,
                "independently_authenticated_chronology": False,
                "genuine_forward_evidence": False,
                "independently_authenticated_custody": False,
                "quality_flags": [
                    "calendar_unverified",
                    "corporate_actions_unverified",
                    "local_response_not_independent_market_authority",
                ],
                "network_requests": 0,
            }
        )


def run_daily(
    config: SchedulerConfig,
    output: Path,
    model_db: Path,
    model_version: str,
    day: date,
    tickers: tuple[str, ...],
    as_of: datetime,
    *,
    lookback_days: int = 60,
    allow_synthetic_fixture: bool = False,
) -> FrozenJSON:
    """Run after collection; commit each ticker, resume only non-completed work.

    Failed source verification raises before output creation. Collection state is
    never changed. A crash between ticker commits leaves pending work, not success.
    """
    _output_path(config, output, model_db)
    sha(model_version)
    source, histories = _inputs(config, day, tickers, lookback_days, as_of)
    require(
        source["data_origin"] == "real" or allow_synthetic_fixture,
        "daily_explicit_synthetic_fixture_required",
    )
    with closing(ReadOnlyAnalysisStore(model_db)) as models:
        model = load_model(models, model_version, "historical_research")
    # Timestamp isn't part of identity: local retries do not change frozen inputs.
    job = {
        "version": VERSION,
        "day": day.isoformat(),
        "tickers": list(tickers),
        "model_version": model_version,
        "config_pin": config.document.identifier,
        "lookback_days": lookback_days,
        "source": source,
        "implementation_pin": _code_pin(),
    }
    with _writer(output) as store:
        previous = [(key, row) for key, row in store.all(JOB) if row["day"] == day.isoformat()]
        require(
            not previous or (len(previous) == 1 and previous[0][1] == job),
            "daily_conflicting_frozen_job",
        )
        job_id = previous[0][0] if previous else store.put(JOB, job)
        latest = _latest(store, job_id)
        for ticker in tickers:
            old = latest.get(ticker)
            if old and old["status"] == "completed":
                continue
            require(old is None or old["attempt"] < 20, "daily_attempt_limit_reached")
            result: dict[str, Any] = {
                "job_id": job_id,
                "ticker": ticker,
                "attempt": old["attempt"] + 1 if old else 1,
                "checked_at": timestamp(as_of).isoformat(),
                "research_score": None,
            }
            try:
                result.update(_analyze(model, histories[ticker], source, as_of))
            except ValueError as exc:
                result.update(status="blocked", reason=str(exc))
            except Exception:
                # No private exception text, credentials or guessed scores in status.
                result.update(status="failed", reason="research_analysis_failed")
            store.put(RESULT, result)
    return daily_status(output, day, as_of)


def after_collection(
    config: SchedulerConfig,
    collection: FrozenJSON,
    output: Path,
    model_db: Path,
    model_version: str,
    tickers: tuple[str, ...],
    as_of: datetime,
    *,
    lookback_days: int = 60,
    allow_synthetic_fixture: bool = False,
) -> FrozenJSON:
    """Optional CLI post-step; collection remains successful if research fails.

    A collection summary only selects dates. run_daily replays original owners;
    the summary itself is never evidence that an archive or stored batch is valid.
    """
    report = collection.to_dict()
    require(
        report.get("version") in {"krx_scheduler_run_v1", "krx_collection_batch_v1"},
        "daily_collection_report_version",
    )
    rows = report["results"] if report["version"] == "krx_collection_batch_v1" else [report]
    research = []
    for row in rows:
        if row["outcome"] != "succeeded":
            continue
        day = date.fromisoformat(row["day"])
        try:
            result = run_daily(
                config,
                output,
                model_db,
                model_version,
                day,
                tickers,
                as_of,
                lookback_days=lookback_days,
                allow_synthetic_fixture=allow_synthetic_fixture,
            )
            research.append(result.to_dict())
        except Exception as exc:
            research.append(
                {
                    "day": day.isoformat(),
                    "analysis_status": "blocked",
                    "reason": "research_inputs_or_output_not_ready",
                    "error_type": type(exc).__name__,
                    "network_requests": 0,
                }
            )
    return FrozenJSON.freeze(
        {
            "version": "krx_daily_pipeline_v1",
            "collection": report,
            "research": research,
            "future_prediction_recording_available": False,
        }
    )
