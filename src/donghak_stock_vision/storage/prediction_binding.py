"""Resolve original prediction owners before any future-outcome evidence is used.

The source containers confer no trust: every call replays the original journal
and captures. Synthetic local chronology is never authenticated forward custody.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.forward_signal import request_contract, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import MAX_ENTRIES, history
from donghak_stock_vision.storage.prospective_prediction import authorize_horizon


@dataclass(frozen=True)
class PredictionSource:
    journal: Path
    journal_head: str
    entry_pin: str
    prediction_id: str
    capture_root: Path
    request: FrozenJSON
    request_pin: str
    policy_pin: str


@dataclass(frozen=True)
class FutureSource:
    capture_root: Path
    request: FrozenJSON
    request_pin: str


def inspect_prediction(source: PredictionSource) -> FrozenJSON:
    """Verify original identity, frozen horizon, implementation and capture lineage."""
    request_contract(source.request, source.request_pin)  # Partition guard before source I/O.
    for pin in (source.journal_head, source.entry_pin, source.prediction_id, source.policy_pin):
        sha(pin)
    records = history(source.journal, source.journal_head, limit=MAX_ENTRIES).to_dict()
    entries = [e for e in records["entries"] if e["entry_pin"] == source.entry_pin]
    require(len(entries) == 1, "original_prediction_entry_not_found")
    entry = entries[0]["entry"]
    require(
        entry["version"] == "forward_signal_entry_v2", "legacy_entry_has_no_predeclared_prediction"
    )
    prediction = entry["prediction"]
    require(prediction["prediction_id"] == source.prediction_id, "original_prediction_id_mismatch")
    require(
        prediction["policy_pin"] == source.policy_pin, "original_prediction_policy_pin_mismatch"
    )
    require(
        entry["decision"]["request_pin"] == source.request_pin,
        "original_prediction_request_pin_mismatch",
    )
    authorize_horizon(
        source.request,
        source.request_pin,
        FrozenJSON.freeze(prediction["policy"]),
        source.policy_pin,
    )
    verify_signal(
        source.capture_root,
        source.request,
        source.request_pin,
        FrozenJSON.freeze(entry["decision"]),
        entry["decision_pin"],
    )
    return FrozenJSON.freeze(
        {
            "version": "original_prediction_verification_v1",
            "journal_head": source.journal_head,
            "entry_pin": source.entry_pin,
            "prediction": prediction,
            "decision_pin": entry["decision_pin"],
            "instrument": entry["decision"]["instrument"],
            "assurance": "original_capture_replay_and_local_journal_integrity_only",
            "independent_creation_time_verified": False,
            "genuine_forward_evidence": False,
        }
    )


def bind_outcome(
    source: PredictionSource, as_of: datetime, future: FutureSource | None = None
) -> FrozenJSON:
    """Only a frozen original journal window may control future resolution."""
    base: dict[str, object] = {
        "version": "prediction_outcome_binding_v1",
        "binding_rule": "capture_started_and_ingested_within_frozen_window_v1",
        "as_of": None,
        "prediction_binding_verified": False,
        "pair_verified": False,
        "evidence_class": "synthetic_offline_only",
        "independent_creation_time_verified": False,
        "genuine_forward_evidence": False,
        "prospective_evaluation_eligible": False,
        "offline_evaluation_eligible": False,
        "execution_authorized": False,
    }

    def result(status: str, reason: str, outcome: dict[str, object] | None = None) -> FrozenJSON:
        return FrozenJSON.freeze({**base, "status": status, "reason": reason, "outcome": outcome})

    try:
        at = timestamp(as_of)
        base["as_of"] = at.isoformat()
        original = inspect_prediction(source).to_dict()
        p = original["prediction"]
        policy = p["policy"]
        require(at >= timestamp(p["recorded_at"]), "evaluation_before_prediction_recording")
        base.update(
            {
                "prediction_binding_verified": True,
                "prediction_id": source.prediction_id,
                "entry_pin": source.entry_pin,
                "journal_head": source.journal_head,
                "request_pin": source.request_pin,
                "policy_pin": source.policy_pin,
                "predictor": p["predictor"],
                "parameters": p["parameters"],
                "input_fingerprint": p["input_fingerprint"],
                "instrument": original["instrument"],
                "recorded_at": p["recorded_at"],
                "target_trading_date": policy["target_trading_date"],
                "evaluate_not_before": policy["evaluate_not_before"],
                "outcome_deadline": policy["outcome_deadline"],
                "outcome_rule": p["outcome_rule"],
            }
        )
        due, deadline = (
            timestamp(policy["evaluate_not_before"]),
            timestamp(policy["outcome_deadline"]),
        )
        if at < due:
            return result("pending", "frozen_evaluation_gate_not_elapsed")
        if future is None:
            return result(
                "unavailable",
                "outcome_deadline_elapsed" if at > deadline else "future_observation_missing",
            )
        # Future-source integration uses the original resolver, never a second parser.
        from donghak_stock_vision.storage.future_observation import resolve_observation

        originals = request_contract(source.request, source.request_pin)
        plans = request_contract(future.request, future.request_pin)
        require(len(plans) == 1, "exact_frozen_target_required")
        require(
            plans[0].day.isoformat() == policy["target_trading_date"], "future_target_date_mismatch"
        )
        require(
            (plans[0].split_pin, plans[0].partition)
            == (originals[0].split_pin, originals[0].partition),
            "future_research_cohort_mismatch",
        )
        require(
            future.capture_root.resolve() != source.capture_root.resolve(),
            "separate_frozen_future_archive_required",
        )
        resolved = resolve_observation(
            future.capture_root,
            future.request,
            future.request_pin,
            recorded_at=timestamp(p["recorded_at"]),
            target_day=date.fromisoformat(policy["target_trading_date"]),
            not_before=due,
            available_by=deadline,
            as_of=at,
        )
        r = resolved.to_dict()
        if r["status"] != "available":
            return result("unavailable", r["reason"])
        observation = r["observation"]
        require(
            (observation["bar"]["ticker"], observation["bar"]["market"])
            == (original["instrument"]["symbol"], original["instrument"]["market"]),
            "future_instrument_mismatch",
        )
        if any(
            timestamp(observation[key]) < due
            for key in ("request_started_at", "local_capture_at", "local_ingested_at")
        ):
            return result("unavailable", "observation_precedes_frozen_gate")
        if timestamp(observation["local_ingested_at"]) > deadline:
            return result("unavailable", "observation_after_frozen_deadline")
        require(
            p["outcome_rule"] == "strict_directional_close_change_v1",
            "unsupported_frozen_outcome_rule",
        )
        reference, close = p["reference_close"], observation["bar"]["close"]
        direction = "up" if close > reference else "down" if close < reference else "flat"
        base.update(
            {
                "pair_verified": True,
                "offline_evaluation_eligible": True,
                "future_resolution_pin": resolved.identifier,
                "future_request_pin": future.request_pin,
                "future_observation_id": observation["observation_id"],
            }
        )
        return result(
            "verified",
            "original_prediction_and_future_observation_bound",
            {
                "predicted_direction": p["direction"],
                "observed_direction": direction,
                "correct": p["direction"] == direction,
                "reference_close": reference,
                "target_close": close,
                "price_change_fraction": {"numerator": close - reference, "denominator": reference},
                "observation": observation,
            },
        )
    except (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error) as error:
        base.update({"pair_verified": False, "offline_evaluation_eligible": False})
        code = str(error)
        reason = (
            code if re.fullmatch(r"[a-z][a-z0-9_]{0,120}", code) else "evidence_validation_failed"
        )
        return result("invalid", reason)


def verify_binding(
    result: FrozenJSON,
    result_pin: str,
    source: PredictionSource,
    as_of: datetime,
    future: FutureSource | None = None,
) -> None:
    require(result.identifier == sha(result_pin), "binding_pin_mismatch")
    require(bind_outcome(source, as_of, future) == result, "binding_semantic_mismatch")
