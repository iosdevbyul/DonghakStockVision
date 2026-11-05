"""Read-only future-observation evidence, independent of prospective journal versions.

Caller-supplied time windows are not proof of an original prediction. The later
journal-binding layer must supply its frozen window and independently trusted pins.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_signal import generate_signal, request_contract


def resolve_observation(
    root: Path | None,
    request: FrozenJSON | None,
    request_pin: str | None,
    *,
    recorded_at: datetime,
    target_day: date,
    not_before: datetime,
    available_by: datetime,
    as_of: datetime,
) -> FrozenJSON:
    """Verify a single exact-date offline capture; never load prices before maturity."""
    recorded, due, deadline, at = map(timestamp, (recorded_at, not_before, available_by, as_of))
    require(type(target_day) is date, "explicit_target_calendar_date_required")
    require(
        recorded.astimezone(SEOUL).date() < target_day < due.astimezone(SEOUL).date(),
        "future_target_window_required",
    )
    require(recorded < due < deadline and at >= recorded, "future_observation_clock_order")
    require(
        (deadline - due).total_seconds() <= 604800
        and (deadline - recorded).total_seconds() <= 31 * 86400,
        "future_observation_window_budget",
    )
    base = {
        "version": "future_observation_resolution_v1",
        "mode": "offline_fixture_resolution",
        "window": {
            "recorded_at": recorded.isoformat(),
            "target_trading_date": target_day.isoformat(),
            "not_before": due.isoformat(),
            "available_by": deadline.isoformat(),
            "as_of": at.isoformat(),
        },
        "window_assurance": "caller_supplied_not_original_prediction_proof",
        "prediction_binding_verified": False,
        "independent_timestamp_verified": False,
        "genuine_forward_evidence": False,
        "execution_authorized": False,
    }

    def result(status: str, reason: str, observation: dict[str, Any] | None = None) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                **base,
                "status": status,
                "reason": reason,
                "observation": observation,
                "request_pin": request_pin if observation is not None else None,
                "source_origin": "offline_fixture" if observation is not None else None,
            }
        )

    if at < due:
        return result("pending", "evaluation_gate_not_elapsed")
    require(
        all(v is None for v in (root, request, request_pin))
        or all(v is not None for v in (root, request, request_pin)),
        "future_capture_references_required_together",
    )
    if request is None or root is None or request_pin is None:
        return result(
            "unavailable",
            "outcome_deadline_elapsed" if at > deadline else "future_capture_not_supplied",
        )
    plans = request_contract(request, request_pin)  # Protected partition guard before capture I/O.
    require(len(plans) == 1 and plans[0].day == target_day, "exact_target_observation_required")
    require(
        request.to_dict()["mode"] == "offline_fixture",
        "authenticated_prospective_source_path_unavailable",
    )
    require(timestamp(request.to_dict()["calculated_at"]) <= at, "future_evidence_after_as_of")
    if not (root / DB).is_file():
        return result("unavailable", "future_capture_not_available")
    verified = generate_signal(root, request, request_pin).to_dict()
    require(verified["origin"] == "offline_fixture", "future_source_origin_mismatch")
    observations = verified["inputs"]["observations"]
    require(len(observations) == 1, "exact_target_observation_required")
    observation = observations[0]
    captured, ingested = (
        timestamp(observation["local_capture_at"]),
        timestamp(observation["local_ingested_at"]),
    )
    require(recorded < captured <= ingested <= at, "future_observation_chronology_mismatch")
    require(
        observation["bar"]["trading_date"] == target_day.isoformat(),
        "future_observation_target_mismatch",
    )
    if ingested > deadline:
        return result("unavailable", "observation_not_available_by_deadline")
    return result("available", "original_capture_and_local_chronology_verified", observation)


def verify_resolution(
    result: FrozenJSON,
    result_pin: str,
    root: Path | None,
    request: FrozenJSON | None,
    request_pin: str | None,
    *,
    recorded_at: datetime,
    target_day: date,
    not_before: datetime,
    available_by: datetime,
    as_of: datetime,
) -> None:
    require(result.identifier == sha(result_pin), "future_resolution_pin_mismatch")
    replay = resolve_observation(
        root,
        request,
        request_pin,
        recorded_at=recorded_at,
        target_day=target_day,
        not_before=not_before,
        available_by=available_by,
        as_of=as_of,
    )
    require(replay == result, "future_resolution_semantic_mismatch")
