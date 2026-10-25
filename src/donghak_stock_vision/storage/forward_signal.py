"""Read-only original-capture verification and executable forward reference analysis.

No historical DB fallback, network client, broker, or independent approval authority.
The separately pinned pilot DB supplies the original ingestion completion audit time
which capture manifest v1 does not include. Local timestamps remain unauthenticated.
"""

from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.storage.forward_capture import DB, _connection, verify_capture
from donghak_stock_vision.storage.historical_readiness import _file_hash
from donghak_stock_vision.strategy.forward_reference import calculate, parameters, reference
from donghak_stock_vision.trading.domain import Currency, Exchange, Instrument

POLICY = "forward_local_capture_observed_bars_v1"


def request_contract(request: FrozenJSON, trusted_pin: str) -> list[ForwardCapturePlan]:
    try:
        return _request_contract(request, trusted_pin)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("malformed_signal_request") from error


def _request_contract(request: FrozenJSON, trusted_pin: str) -> list[ForwardCapturePlan]:
    require(
        type(request) is FrozenJSON and len(request.payload_json.encode()) <= 256 * 1024,
        "bounded_signal_request_required",
    )
    require(request.identifier == sha(trusted_pin), "signal_request_pin_mismatch")
    d = request.to_dict()
    require(
        set(d)
        == {
            "version",
            "symbol",
            "market",
            "currency",
            "mode",
            "start",
            "end",
            "calculated_at",
            "fast",
            "slow",
            "policy",
            "max_latest_age_seconds",
            "database_pin",
            "sources",
        },
        "signal_request_fields",
    )
    require(
        d["version"] == "forward_signal_request_v1" and d["policy"] == POLICY,
        "unsupported_signal_request_or_policy",
    )
    instrument = Instrument(d["symbol"], Exchange(d["market"]), Currency(d["currency"]))
    require(
        instrument.symbol == "005930" and instrument.exchange is Exchange.KOSPI,
        "reference_signal_scope_is_kospi_005930",
    )
    require(
        d["mode"] in ("offline_fixture", "authorized_provider"), "explicit_signal_mode_required"
    )
    parameters(d["fast"], d["slow"])
    start, end = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
    calculated = timestamp(d["calculated_at"])
    require(d["calculated_at"] == calculated.isoformat(), "canonical_utc_calculation_time_required")
    require(
        start <= end < calculated.astimezone(SEOUL).date() and (end - start).days <= 366,
        "invalid_or_future_observation_window",
    )
    require(
        type(d["max_latest_age_seconds"]) is int and 1 <= d["max_latest_age_seconds"] <= 604800,
        "latest_age_budget_invalid",
    )
    sha(d["database_pin"])
    require(type(d["sources"]) is list and 0 <= len(d["sources"]) <= 32, "source_count_limit")
    plans: list[ForwardCapturePlan] = []
    for s in d["sources"]:
        require(
            type(s) is dict and set(s) == {"plan", "plan_pin", "attempt", "capture_pin"},
            "source_reference_fields",
        )
        plan = ForwardCapturePlan.from_dict(s["plan"])  # Partition guard before ANY source I/O.
        require(plan.document.identifier == sha(s["plan_pin"]), "source_plan_pin_mismatch")
        sha(s["capture_pin"])
        require(
            plan.mode == d["mode"]
            and plan.ticker == instrument.symbol
            and plan.market == instrument.exchange.value,
            "capture_origin_or_instrument_mismatch",
        )
        require(start <= plan.day <= end, "source_outside_requested_window")
        require(not plans or plans[-1].day < plan.day, "duplicate_or_unordered_source_dates")
        require(
            not plans
            or (plan.split_pin, plan.partition) == (plans[0].split_pin, plans[0].partition),
            "mixed_research_cohort",
        )
        plans.append(plan)
    return plans


def generate_signal(root: Path, request: FrozenJSON, trusted_pin: str) -> FrozenJSON:
    plans = request_contract(request, trusted_pin)
    d = request.to_dict()
    # Existing operator reference strings cannot authenticate provider service approval.
    require(d["mode"] == "offline_fixture", "provider_authorization_not_independently_verified")
    path = root / DB
    require(
        not root.is_symlink() and path.is_file() and not path.is_symlink(), "pilot_archive_missing"
    )
    for suffix in ("-wal", "-journal"):
        require(not Path(str(path) + suffix).exists(), "active_capture_writer_unsupported")
    require(path.stat().st_size <= 256 * 1024 * 1024, "pilot_archive_byte_budget")
    require(_file_hash(path) == d["database_pin"], "capture_database_pin_mismatch")
    calculated = timestamp(d["calculated_at"])
    observations: list[dict[str, Any]] = []
    for plan, source in zip(plans, d["sources"], strict=True):
        owner = verify_capture(root, plan, source["attempt"], source["capture_pin"])
        manifest = owner.to_dict()
        require(
            manifest["ingestion"] == [["success", 1, None]] and manifest["stop_reason"] is None,
            "capture_not_successfully_ingested",
        )
        successes = [r for r in manifest["responses"] if r["parsed_bars"] is not None]
        require(len(successes) == 1, "ambiguous_capture_observation")
        response = successes[0]
        bar = DailyBar.from_dict(response["parsed_bars"][0])
        started = timestamp(response["metadata"]["request_started_at"])
        captured = timestamp(response["metadata"]["response_completed_at"])
        require(
            plan.day < started.astimezone(SEOUL).date() and started <= captured <= calculated,
            "future_or_inconsistent_capture_time",
        )
        require(
            bar.adjustment == "unadjusted"
            and bar.provider == "krx"
            and bar.collected_at == captured,
            "source_price_semantics_mismatch",
        )
        with closing(_connection(root)) as connection:
            rows = connection.execute(
                "SELECT finished_at FROM collection_runs WHERE run_id=?", (manifest["capture_id"],)
            ).fetchall()
        require(len(rows) == 1, "ingestion_timestamp_missing_or_ambiguous")
        ingested = timestamp(rows[0][0])
        require(captured <= ingested <= calculated, "future_or_inconsistent_ingestion_time")
        observation = {
            "capture_id": manifest["capture_id"],
            "capture_pin": owner.identifier,
            "plan_pin": plan.document.identifier,
            "raw_receipt": response["receipt"],
            "raw_id": response["raw_id"],
            "bar": bar.to_dict(),
            "request_started_at": started.isoformat(),
            "local_capture_at": captured.isoformat(),
            "local_ingested_at": ingested.isoformat(),
            "exchange_publication_at": None,
            "authenticated_at": None,
            "historical_decision_safe_at": None,
            "clock_assurance": "locally_recorded_not_independently_authenticated",
        }
        observation["observation_id"] = FrozenJSON.freeze(observation).identifier
        observations.append(observation)
    require(
        all(
            timestamp(a["local_capture_at"]) <= timestamp(b["local_capture_at"])
            and timestamp(a["local_ingested_at"]) <= timestamp(b["local_ingested_at"])
            for a, b in zip(observations, observations[1:], strict=False)
        ),
        "nonmonotonic_observation_timestamps",
    )
    if observations:
        age = (calculated - timestamp(observations[-1]["local_capture_at"])).total_seconds()
        require(0 <= age <= d["max_latest_age_seconds"], "latest_capture_stale")
    calculation = calculate(tuple(o["bar"]["close"] for o in observations), d["fast"], d["slow"])
    inputs = FrozenJSON.freeze({"observations": observations, "database_pin": d["database_pin"]})
    # Detect writes throughout independent source verification; never alter the pilot DB.
    require(_file_hash(path) == d["database_pin"], "capture_changed_during_signal_generation")
    identity = FrozenJSON.freeze({"request_pin": trusted_pin, "strategy": reference()}).identifier
    return FrozenJSON.freeze(
        {
            "version": "forward_signal_decision_v1",
            "signal_id": identity,
            "request_pin": trusted_pin,
            "instrument": {"symbol": d["symbol"], "market": d["market"], "currency": d["currency"]},
            "strategy": reference(),
            "parameters": {"fast": d["fast"], "slow": d["slow"]},
            "calculated_at": calculated.isoformat(),
            "latest_trading_date": observations[-1]["bar"]["trading_date"]
            if observations
            else None,
            "ordered_observation_ids": [o["observation_id"] for o in observations],
            "input_window": {"start": d["start"], "end": d["end"]},
            "input_fingerprint": inputs.identifier,
            "inputs": inputs.to_dict(),
            "eligibility": {
                "policy": POLICY,
                "eligible": True,
                "assurance": "synthetic_functional_validation_only",
                "session_completeness": "unverified_observed_bars_only",
                "historical_pit_qualified": False,
                "execution_authorized": False,
            },
            "origin": d["mode"],
            "calculation": calculation.to_dict(),
            "usage": "research_signal_only_no_order_or_profitability_claim",
        }
    )


def verify_signal(
    root: Path, request: FrozenJSON, request_pin: str, decision: FrozenJSON, decision_pin: str
) -> None:
    require(decision.identifier == sha(decision_pin), "decision_pin_mismatch")
    require(generate_signal(root, request, request_pin) == decision, "signal_semantic_mismatch")
