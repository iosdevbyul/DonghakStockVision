"""Frozen, synthetic prospective horizons for the existing forward signal journal.

Local clock consistency is not independently authenticated prediction custody.
No provider-backed forward claim is admitted by this initial offline path.
"""

from datetime import date, datetime

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.data.schema import SEOUL


def authorize_horizon(request: FrozenJSON, request_pin: str, owner: FrozenJSON, pin: str) -> None:
    """Check original partition ownership before capture or journal I/O."""
    from donghak_stock_vision.storage.forward_signal import request_contract

    policy(owner, pin)
    plans = request_contract(request, request_pin)
    require(
        request.to_dict()["mode"] == "offline_fixture",
        "independent_prediction_timestamp_evidence_unavailable",
    )
    require(bool(plans), "prediction_reference_missing")
    plan = plans[0]
    if plan.split is None:
        raise ValueError("prediction_original_partition_required")
    span = plan.split.training if plan.partition == "training" else plan.split.validation
    target = date.fromisoformat(owner.to_dict()["target_trading_date"])
    require(
        span.start <= target <= span.end, "prediction_target_outside_authorized_research_partition"
    )


def policy(owner: FrozenJSON, pin: str) -> None:
    require(
        type(owner) is FrozenJSON and len(owner.payload_json.encode()) <= 4096,
        "prediction_policy_budget",
    )
    require(owner.identifier == sha(pin), "prediction_policy_pin_mismatch")
    d = owner.to_dict()
    require(
        set(d)
        == {
            "version",
            "target_trading_date",
            "evaluate_not_before",
            "outcome_deadline",
            "max_recording_delay_seconds",
        },
        "prediction_policy_fields",
    )
    require(d["version"] == "forward_prediction_policy_v1", "prediction_policy_version")
    target = date.fromisoformat(d["target_trading_date"])
    require(target.isoformat() == d["target_trading_date"], "canonical_prediction_target_date")
    due, deadline = timestamp(d["evaluate_not_before"]), timestamp(d["outcome_deadline"])
    require(
        due.isoformat() == d["evaluate_not_before"]
        and deadline.isoformat() == d["outcome_deadline"],
        "prediction_policy_utc_required",
    )
    require(target < due.astimezone(SEOUL).date() and due < deadline, "prediction_horizon_order")
    require((deadline - due).total_seconds() <= 604800, "prediction_outcome_wait_budget")
    require(
        type(d["max_recording_delay_seconds"]) is int
        and 1 <= d["max_recording_delay_seconds"] <= 300,
        "prediction_recording_delay_budget",
    )


def evidence(
    decision: FrozenJSON, owner: FrozenJSON, pin: str, recorded_at: datetime
) -> FrozenJSON:
    policy(owner, pin)
    d, p = decision.to_dict(), owner.to_dict()
    require(
        d["version"] == "forward_signal_decision_v1" and d["origin"] == "offline_fixture",
        "independent_prediction_timestamp_evidence_unavailable",
    )
    require(d["calculation"]["signal"] in {"BUY", "SELL"}, "directional_prediction_required")
    recorded = timestamp(recorded_at)
    require(
        0
        <= (recorded - timestamp(d["calculated_at"])).total_seconds()
        <= p["max_recording_delay_seconds"],
        "prediction_recording_clock_or_staleness",
    )
    target = date.fromisoformat(p["target_trading_date"])
    require(target > recorded.astimezone(SEOUL).date(), "prediction_target_must_be_future")
    require(
        recorded < timestamp(p["evaluate_not_before"])
        and (timestamp(p["outcome_deadline"]) - recorded).total_seconds() <= 31 * 86400,
        "prediction_horizon_budget",
    )
    observations = d["inputs"]["observations"]
    require(bool(observations), "prediction_reference_missing")
    latest = observations[-1]
    require(timestamp(latest["local_ingested_at"]) <= recorded, "prediction_future_input")
    return FrozenJSON.freeze(
        {
            "version": "forward_prediction_evidence_v1",
            "prediction_id": FrozenJSON.freeze(
                {"decision_pin": decision.identifier, "policy_pin": pin}
            ).identifier,
            "decision_pin": decision.identifier,
            "policy": p,
            "policy_pin": pin,
            "predictor": d["strategy"],
            "parameters": d["parameters"],
            "input_fingerprint": d["input_fingerprint"],
            "reference_observation_id": latest["observation_id"],
            "reference_close": latest["bar"]["close"],
            "direction": "up" if d["calculation"]["signal"] == "BUY" else "down",
            "outcome_rule": "strict_directional_close_change_v1",
            "recorded_at": recorded.isoformat(),
            "origin": "offline_fixture",
            "creation_time_assurance": "synthetic_local_clock_consistency_only",
            "independent_creation_time_verified": False,
            "genuine_forward_evidence": False,
            "execution_authorized": False,
        }
    )


def state(prediction: FrozenJSON, as_of: datetime) -> FrozenJSON:
    d = prediction.to_dict()
    require(d["version"] == "forward_prediction_evidence_v1", "prediction_evidence_version")
    at = timestamp(as_of)
    require(at >= timestamp(d["recorded_at"]), "status_before_prediction_recording")
    p = d["policy"]
    if at < timestamp(p["evaluate_not_before"]):
        lifecycle, reason = "pending", "horizon_not_elapsed"
    elif at <= timestamp(p["outcome_deadline"]):
        lifecycle, reason = "matured", "awaiting_verified_target_observation"
    else:
        lifecycle, reason = "unavailable", "outcome_deadline_elapsed"
    return FrozenJSON.freeze(
        {
            "version": "forward_prediction_state_v1",
            "prediction_id": d["prediction_id"],
            "as_of": at.isoformat(),
            "state": lifecycle,
            "reason": reason,
            "outcome": None,
            "independent_creation_time_verified": False,
            "genuine_forward_evidence": False,
            "execution_authorized": False,
        }
    )
