"""Versioned future direction envelopes, never legacy reversal-event evidence."""

import math
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.data.schema import SEOUL, validate_ticker

VERSION = "direction_shadow_event_v3"


def contract(h: int) -> dict[str, Any]:
    require(type(h) is int and h in (1, 5, 20), "direction_horizon")
    return dict(
        version="future_delayed_direction_v3",
        horizon=h,
        training_label_version=f"delayed_close_direction_h{h}_v2",
        feature_version="causal_daily_closed_observed_periods_v2",
        input="T_verified_before_event_start_midnight",
        base="first_verified_session_after_T_close",
        target="last_close_of_H_sessions_after_base",
        neutral_band_bps=20,
        threshold=0.5,
        calendar="verified_complete_session_calendar_no_missing_session_compression",
        score_semantics="uncalibrated_model_score_not_price_rise_probability",
        evaluation=dict(
            version="calendar_evidence_v1",
            minimum_calendar_anchors=20,
            minimum_class_rows=10,
            anchor_rule="union_dates_purged_through_maximum_closed_outcome_interval",
        ),
    )


def identifier(kind: str, value: Any) -> str:
    return digest(dict(version=VERSION, kind=kind, value=value))


def validate_event(owner: FrozenJSON) -> dict[str, Any]:
    require(len(owner.payload_json.encode()) <= 120 * 1024, "direction_event_bound")
    d = fields(
        owner.to_dict(),
        "version kind event_id recorded_at payload pit_qualified "
        "performance_approved genuine_forward_evidence execution_authorized",
    )
    require(
        d["version"] == VERSION and d["kind"] in ("prediction", "outcome"),
        "direction_event_version",
    )
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
        "direction_approval_forbidden",
    )
    sha(d["event_id"])
    require(
        timestamp(d["recorded_at"]).isoformat() == d["recorded_at"], "direction_canonical_clock"
    )
    p = d["payload"]
    if d["kind"] == "prediction":
        fields(
            p,
            "prediction_id slot_id registration_id ticker horizon contract "
            "input_trading_date context_window calendar_pin base_session "
            "outcome_sessions outcome_not_before generated_at model features "
            "feature_hash feature_version input_source input_source_pin source_kind "
            "mode score predicted_class threshold actual_outcome baseline_score",
        )
        validate_ticker(p["ticker"])
        require(p["contract"] == contract(p["horizon"]), "direction_contract_binding")
        require(
            p["prediction_id"] == d["event_id"] == identifier("prediction", p["slot_id"]),
            "direction_prediction_identity",
        )
        require(
            p["slot_id"]
            == identifier(
                "slot",
                dict(
                    registration=p["registration_id"],
                    ticker=p["ticker"],
                    horizon=p["horizon"],
                    input=p["input_trading_date"],
                    model=p["model"]["experiment_id"],
                ),
            ),
            "direction_slot_identity",
        )
        for key in ("registration_id", "feature_hash", "input_source_pin", "calendar_pin"):
            sha(p[key])
        require(
            p["input_source_pin"] == digest(p["input_source"])
            and p["feature_hash"] == digest(p["features"]),
            "direction_input_binding",
        )
        require(
            p["feature_version"] == p["contract"]["feature_version"]
            and len(p["features"]) == 46
            and all(math.isfinite(v) for v in p["features"]),
            "direction_features",
        )
        sessions = p["outcome_sessions"]
        require(
            len(sessions) == p["horizon"]
            and sessions == sorted(set(sessions))
            and p["input_trading_date"] < p["base_session"] < sessions[0],
            "direction_sessions",
        )
        require(
            p["score"] is not None
            and type(p["score"]) in (int, float)
            and math.isfinite(p["score"])
            and 0 <= p["score"] <= 1
            and p["threshold"] == 0.5
            and p["predicted_class"] == int(p["score"] >= 0.5)
            and 0 <= p["baseline_score"] <= 1,
            "direction_score",
        )
        require(
            p["generated_at"] == d["recorded_at"]
            and p["actual_outcome"] is None
            and p["mode"] in ("research_forward_candidate", "historical_replay")
            and p["source_kind"] in ("local_provider_capture", "offline_fixture"),
            "direction_initial_state",
        )
        at = timestamp(p["generated_at"])
        require(
            timestamp(p["model"]["created_at"]) <= at
            and all(
                timestamp(b["collected_at"]) <= at and b["trading_date"] <= p["input_trading_date"]
                for b in p["input_source"]["bars"]
            ),
            "direction_future_input",
        )
        if p["mode"] == "research_forward_candidate":
            require(
                date.fromisoformat(sessions[0]) > at.astimezone(SEOUL).date(),
                "direction_deadline_passed",
            )
    else:
        fields(p, "prediction_id state reason actual_label future future_pin checked_at correct")
        sha(p["prediction_id"])
        require(
            p["state"] in ("pending", "evaluated", "unavailable")
            and p["checked_at"] == d["recorded_at"],
            "direction_outcome_state",
        )
        require(
            (p["state"] == "evaluated")
            == (type(p["actual_label"]) is int and p["actual_label"] in (0, 1)),
            "direction_outcome_label",
        )
        require(
            p["future"] is not None or (p["state"] == "pending" and p["correct"] is None),
            "direction_missing_terminal_sources",
        )
        require(
            (p["future"] is None and p["future_pin"] is None)
            or digest(p["future"]) == p["future_pin"],
            "direction_future_pin",
        )
        require(
            d["event_id"]
            == identifier("outcome", {k: v for k, v in p.items() if k != "checked_at"}),
            "direction_outcome_identity",
        )
    return d


def event(kind: str, payload: dict[str, Any], at: str) -> FrozenJSON:
    key = (
        payload["prediction_id"]
        if kind == "prediction"
        else identifier("outcome", {k: v for k, v in payload.items() if k != "checked_at"})
    )
    value = FrozenJSON.freeze(
        dict(
            version=VERSION,
            kind=kind,
            event_id=key,
            recorded_at=at,
            payload=payload,
            pit_qualified=False,
            performance_approved=False,
            genuine_forward_evidence=False,
            execution_authorized=False,
        )
    )
    validate_event(value)
    return value
