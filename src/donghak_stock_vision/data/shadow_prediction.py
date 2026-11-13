"""Research-shadow envelopes, separate from authorized prospective signal owners."""

import math
import re
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import FEATURES, digest, timestamp
from donghak_stock_vision.data.schema import SEOUL, validate_ticker

VERSION = "research_shadow_event_v1"
MODES = ("historical_replay", "research_forward_candidate")


def identifier(kind: str, identity: Any) -> str:
    return digest(dict(version=VERSION, kind=kind, identity=identity))


def validate_event(owner: FrozenJSON) -> dict[str, Any]:
    require(len(owner.payload_json.encode()) <= 120 * 1024, "shadow_event_byte_bound")
    d = fields(
        owner.to_dict(),
        "version kind event_id recorded_at payload pit_qualified performance_approved "
        "genuine_forward_evidence execution_authorized",
    )
    require(
        d["version"] == VERSION and d["kind"] in ("prediction", "outcome", "failure", "correction"),
        "shadow_event_version_or_kind",
    )
    sha(d["event_id"])
    require(timestamp(d["recorded_at"]).isoformat() == d["recorded_at"], "shadow_canonical_time")
    require(
        all(
            d[k] is False
            for k in (
                "pit_qualified",
                "performance_approved",
                "genuine_forward_evidence",
                "execution_authorized",
            )
        ),
        "shadow_approval_forbidden",
    )
    p = d["payload"]
    if d["kind"] == "prediction":
        fields(
            p,
            "prediction_id slot_id ticker mode source_classification event_rule "
            "horizon_observations target_trading_date "
            "outcome_sessions outcome_not_before input_latest_trading_date input_source_pin inputs "
            "source_database_pin_at_recording calendar_pin model feature_version feature_names "
            "features "
            "feature_hash direction score threshold generated_at inputs_verified_available_at "
            "feature_generated_at input_ingestion_time input_ingestion_time_reason actual_outcome "
            "initial_evaluation_state pit",
        )
        sha(p["slot_id"])
        require(
            len(p["inputs"]["bars"]) == 12 and len(p["inputs"]["batches"]) <= 100,
            "shadow_source_bounds",
        )
        require(
            all(
                timestamp(b["collected_at"]) <= timestamp(p["inputs_verified_available_at"])
                for b in p["inputs"]["bars"]
            ),
            "shadow_future_input",
        )
        require(
            p["mode"] in MODES
            and d["event_id"] == p["prediction_id"] == identifier("prediction", p["slot_id"]),
            "shadow_prediction_identity",
        )
        validate_ticker(p["ticker"])
        require(
            p["event_rule"] == "reversal_barrier_v1"
            and p["horizon_observations"] == 5
            and p["feature_version"] == "ohlcv_value_v1"
            and p["feature_names"] == list(FEATURES),
            "shadow_model_event_contract",
        )
        require(
            p["direction"] in ("up", "down")
            and p["threshold"] == 0.5
            and type(p["score"]) in (int, float)
            and math.isfinite(p["score"])
            and 0 <= p["score"] <= 1,
            "shadow_score_contract",
        )
        require(
            digest(p["features"]) == p["feature_hash"]
            and len(p["features"]) == len(FEATURES)
            and all(type(x) in (int, float) and math.isfinite(x) for x in p["features"]),
            "shadow_feature_binding",
        )
        require(
            digest(p["inputs"]) == p["input_source_pin"]
            and digest(p["model"]["parameters"]) == p["model"]["parameters_pin"],
            "shadow_input_model_binding",
        )
        require(
            p["generated_at"] == d["recorded_at"]
            and p["actual_outcome"] is None
            and p["initial_evaluation_state"] == "pending",
            "shadow_unknown_outcome_required",
        )
        target = date.fromisoformat(p["target_trading_date"])
        require(
            p["outcome_sessions"][0] == p["target_trading_date"]
            and len(p["outcome_sessions"]) == 5
            and p["outcome_sessions"] == sorted(set(p["outcome_sessions"]))
            and date.fromisoformat(p["input_latest_trading_date"]) < target,
            "shadow_target_or_horizon",
        )
        require(
            timestamp(p["model"]["created_at"]) <= timestamp(p["generated_at"])
            and timestamp(p["feature_generated_at"]) <= timestamp(p["generated_at"])
            and timestamp(p["inputs_verified_available_at"])
            <= timestamp(p["feature_generated_at"]),
            "shadow_local_timing_order",
        )
        if p["mode"] == "research_forward_candidate":
            require(
                target > timestamp(p["generated_at"]).astimezone(SEOUL).date(),
                "shadow_target_not_future",
            )
        else:
            require(
                target <= timestamp(p["generated_at"]).astimezone(SEOUL).date(),
                "shadow_replay_target_must_be_past",
            )
        require(
            p["pit"]["independent_publication_time_verified"] is False
            and p["pit"]["pit_qualified"] is False,
            "shadow_pit_promotion_forbidden",
        )
    elif d["kind"] == "outcome":
        fields(
            p,
            "prediction_id mode state reason actual_label future future_source_pin label_start "
            "label_end result_raw_observed_at result_available_at checked_at correct",
        )
        require(p["mode"] in MODES and p["checked_at"] == d["recorded_at"], "shadow_outcome_clock")
        if p["future"] is not None:
            require(digest(p["future"]) == p["future_source_pin"], "shadow_outcome_source_pin")
        sha(p["prediction_id"])
        require(p["state"] in ("pending", "evaluated", "unavailable"), "shadow_outcome_state")
        require(
            (p["state"] == "evaluated")
            == (type(p["actual_label"]) is int and p["actual_label"] in (0, 1)),
            "shadow_outcome_label_state",
        )
        if p["state"] != "evaluated":
            require(p["actual_label"] is None, "shadow_no_fabricated_outcome")
        require(
            d["event_id"]
            == identifier(
                "outcome",
                {k: v for k, v in p.items() if k not in ("checked_at", "result_available_at")},
            ),
            "shadow_outcome_identity",
        )
    elif d["kind"] == "failure":
        validate_ticker(p["ticker"])
        require(
            p["mode"] in MODES and re.fullmatch(r"[a-z][a-z0-9_]{0,159}", p["reason"]) is not None,
            "shadow_failure_code",
        )
        require(d["event_id"] == identifier("failure", p), "shadow_failure_identity")
    else:
        sha(p["prediction_id"])
        require(
            type(p["reason"]) is str and 1 <= len(p["reason"]) <= 120, "shadow_correction_reason"
        )
        require(d["event_id"] == identifier("correction", p), "shadow_correction_identity")
    return d
