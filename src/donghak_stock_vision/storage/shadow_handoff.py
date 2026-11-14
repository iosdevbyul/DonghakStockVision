"""Optional research continuation; original collection and journal owners remain authoritative."""

import hashlib
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.forward_capture import FullMarketScope
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage.krx_authorization import bounded
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.krx_backlog import _records, _verify
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, _policy
from donghak_stock_vision.storage.operational_market import DB
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.shadow_prediction import (
    Clock,
    _calendar,
    _horizon,
    _model,
    _paths,
    _request,
    _rows,
    now,
    record,
)


def _retain(path: Path, value: str) -> None:
    if path.exists():
        require(
            bounded(path, 128 * 1024).decode() == value + "\n", "handoff_retained_input_conflict"
        )
    else:
        write_new(path, value)


def _derived_request(
    config: SchedulerConfig,
    d: dict[str, Any],
    completion: dict[str, Any],
    intents: Path,
    pins: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze a new request after commit; never repin the original template.

    Call only after the existing completion owner verifies the original capture
    and stored batch. Newly retained references are local checkpoints, not new
    independent custody/publication authority. The recorder verifies them again.
    """
    db = Path(d["operational"]["root"]) / DB
    with db.open("rb") as stream:
        database_pin = hashlib.file_digest(stream, "sha256").hexdigest()
    key = digest(dict(template=digest(d), completion=completion, database_pin=database_pin))
    root, held = intents / "handoff-inputs" / key, pins / "handoff-inputs" / key
    for directory in (
        intents,
        pins,
        intents / "handoff-inputs",
        pins / "handoff-inputs",
        root,
        held,
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(
            directory.resolve() == directory and directory.stat().st_mode & 0o077 == 0,
            "handoff_private_input_directory",
        )
    plan_path = root / "plan.json"
    _retain(root / "template.json", FrozenJSON.freeze(d).payload_json)
    _retain(held / "template.pin", digest(d))
    _retain(root / "completion.json", FrozenJSON.freeze(completion).payload_json)
    _retain(held / "completion.pin", digest(completion))
    _retain(plan_path, FrozenJSON.freeze(completion["plan"]).payload_json)
    for name in ("plan", "capture", "batch"):
        _retain(held / (name + ".pin"), completion[name + "_pin"])
    evidence = list(d["operational"]["evidence"])
    # A template can already retain this capture. Never replace contradictory
    # ownership to force compatibility, nor add a duplicate source representation.
    matches = [
        e
        for e in evidence
        if e["kind"] == "forward" and document(e["plan"])["day"] == completion["day"]
    ]
    require(len(matches) <= 1, "handoff_duplicate_template_capture")
    if matches:
        old = matches[0]
        require(
            digest(document(old["plan"])) == completion["plan_pin"]
            and read_pin(Path(old["capture_pin_file"])) == completion["capture_pin"]
            and read_pin(Path(old["batch_pin_file"])) == completion["batch_pin"],
            "handoff_template_capture_conflict",
        )
    else:
        evidence.append(
            dict(
                kind="forward",
                capture_root=str(config.path("capture_root")),
                plan=str(plan_path),
                plan_pin_file=str(held / "plan.pin"),
                attempt=completion["attempt"],
                capture_pin_file=str(held / "capture.pin"),
                batch_pin_file=str(held / "batch.pin"),
                authorization=completion["authorization"],
                authorization_policy="operator_full_market_recurring_v1",
            )
        )
    derived = d | {"operational": d["operational"] | {"pin": database_pin, "evidence": evidence}}
    _retain(root / "request.json", FrozenJSON.freeze(derived).payload_json)
    _retain(held / "request.pin", digest(derived))
    lineage = dict(
        version="research_shadow_handoff_input_v1",
        template_pin=digest(d),
        template=str(root / "template.json"),
        completion=str(root / "completion.json"),
        scheduler_config_pin=config.document.identifier,
        completion_pin=digest(completion),
        request_pin=digest(derived),
        request=str(root / "request.json"),
        request_pin_file=str(held / "request.pin"),
        independently_authenticated=False,
    )
    _retain(root / "lineage.json", FrozenJSON.freeze(lineage).payload_json)
    _retain(held / "lineage.pin", digest(lineage))
    return derived, lineage


def schedule_alignment(
    config: SchedulerConfig, d: dict[str, Any], at: datetime, *, explain: bool = False
) -> FrozenJSON:
    """Compare existing weekday wall-clock scheduling with the unmodified event.

    A policy lower bound is not an assertion that KRX has published the data.
    No schedule, policy or PIN is installed, created or changed by this consumer.
    """
    _request(d)
    require(d["mode"] == "research_forward_candidate", "handoff_forward_mode_required")
    days, calendar_pin = _calendar(d)
    target = d["target_trading_date"]
    require(days.get(target) == "session", "shadow_target_session_unverified")
    prior = sorted(day for day, state in days.items() if state == "session" and day < target)
    require(bool(prior), "handoff_input_session_missing")
    input_day = datetime.fromisoformat(prior[-1]).date()
    policy = _policy(config)
    scope = FullMarketScope.from_dict(policy["full_market_scope"])
    checked = timestamp(at).astimezone(SEOUL)
    earliest = datetime.combine(input_day + timedelta(days=1), time(), SEOUL)
    settings = config.document.to_dict()
    candidate = max(checked, earliest, timestamp(policy["valid_from"]).astimezone(SEOUL))
    slot = datetime.combine(candidate.date(), time(settings["hour"], settings["minute"]), SEOUL)
    if slot < candidate:
        slot += timedelta(days=1)
    while slot.weekday() >= 5:
        slot += timedelta(days=1)
    deadline = datetime.combine(datetime.fromisoformat(target).date(), time(), SEOUL)
    in_scope = (
        input_day in scope.permitted_days
        and slot < timestamp(policy["valid_until"])
        and 1 <= (slot.date() - input_day).days <= policy["max_observation_lag_days"]
    )
    result = dict(
        version="research_shadow_schedule_alignment_v1",
        input_trading_date=input_day.isoformat(),
        target_trading_date=target,
        policy_delay_lower_bound=earliest.isoformat(),
        next_configured_weekday_slot=slot.isoformat(),
        slot_is_in_existing_policy_scope=in_scope,
        generation_deadline_exclusive=deadline.isoformat(),
        configured_slot_before_prediction_deadline=slot < deadline,
        configured_schedule_aligned=in_scope and slot < deadline,
        official_api_publication_time="UNKNOWN",
        provider_response_still_requires_validation=True,
        scheduler_enabled_not_assessed=True,
        policy_changed=False,
        schedule_changed=False,
        pit_qualified=False,
        performance_approved=False,
        network_requests=0,
    )
    if explain:
        lag = policy["max_observation_lag_days"]
        require(type(lag) is int and 1 <= lag <= 7, "recurring_observation_lag_invalid")
        lower = max(earliest, timestamp(policy["valid_from"]).astimezone(SEOUL))
        upper = min(
            deadline,
            timestamp(policy["valid_until"]).astimezone(SEOUL),
            datetime.combine(input_day + timedelta(days=lag + 1), time(), SEOUL),
        )
        blockers = []
        if input_day not in scope.permitted_days:
            blockers.append("input_not_in_policy_scope")
        if lower >= upper:
            blockers.append("no_policy_prediction_time_window")
        current = list(blockers)
        if checked < lower:
            current.append("capture_not_yet_allowed")
        elif checked >= upper:
            current.append("policy_prediction_window_elapsed")
        result.update(
            version="research_shadow_schedule_alignment_v2",
            calendar_pin=calendar_pin,
            minimum_lag_basis="seoul_calendar_date_difference",
            minimum_calendar_day_lag=1,
            requires_24_elapsed_hours=False,
            counts_trading_sessions_for_lag=False,
            input_to_target_calendar_days=(deadline.date() - input_day).days,
            policy_window_start=lower.isoformat(),
            policy_window_end_exclusive=upper.isoformat(),
            structural_blockers=blockers,
            current_time_blockers=current,
            policy_prediction_window_exists=not blockers,
            current_time_in_policy_window=not blockers and lower <= checked < upper,
            configured_slot_in_policy_prediction_window=(not blockers and lower <= slot < upper),
            publication_time_is_policy_bound=False,
            model_event_changed=False,
            automatic_execution_authorized=False,
        )
    return FrozenJSON.freeze(result)


def alignment(d: dict[str, Any], *, clock: Clock = now) -> FrozenJSON:
    """Read-only source replay; no forecast, journal, request or scheduling side effects.

    Calendar-day midnight is the existing conservative prediction deadline, not
    an assertion about an exchange's actual opening or publication time.
    """
    _request(d)
    require(d["mode"] == "research_forward_candidate", "handoff_forward_mode_required")
    started = timestamp(clock())
    _, model = _model(d)
    days, _ = _calendar(d)
    history, future = _horizon(d, model, days)
    rows, _ = _rows(d, history[0], history[-1])
    checked = timestamp(clock())
    require(started <= checked, "handoff_clock_order")
    deadline = datetime.fromisoformat(future[0]).replace(tzinfo=SEOUL)
    results = []
    for ticker in d["tickers"]:
        bars = rows[ticker]
        missing = sorted(set(history) - {b.trading_date.isoformat() for b in bars})
        reasons = []
        if missing:
            reasons.append("shadow_missing_required_input_session")
        if checked >= deadline:
            reasons.append("shadow_target_not_future")
        if any(b.collected_at > checked for b in bars):
            reasons.append("shadow_input_collected_after_execution")
        if timestamp(model["created_at"]) > checked:
            reasons.append("shadow_model_created_after_execution")
        results.append(dict(ticker=ticker, missing_input_dates=missing, reasons=reasons))
    return FrozenJSON.freeze(
        dict(
            version="research_shadow_alignment_v1",
            checked_at=checked.isoformat(),
            input_latest_trading_date=history[-1],
            target_trading_date=future[0],
            outcome_sessions=future,
            event_rule="reversal_barrier_v1",
            generation_deadline_exclusive=deadline.isoformat(),
            deadline_basis="existing_shadow_calendar_day_guard_not_exchange_open_time",
            input_collection_latest_at=max(
                (b.collected_at.isoformat() for bars in rows.values() for b in bars), default=None
            ),
            input_ingestion_time=None,
            input_ingestion_time_reason="original_storage_does_not_retain_commit_timestamp",
            source_publication_time="UNKNOWN",
            independent_publication_time_verified=False,
            model_created_at=model["created_at"],
            feature_generated_at=None,
            prediction_generated_at=None,
            result_available_at=None,
            results=results,
            # Passing this assessment alone does not check directional context,
            # inference or commit a prediction. record remains the final gate.
            input_timing_ready=all(not r["reasons"] for r in results),
            prediction_created=False,
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
            network_requests=0,
        )
    )


def after_collection(
    config: SchedulerConfig,
    collection: FrozenJSON,
    request: Path,
    request_pin_file: Path,
    journal: Path,
    head_pin_file: Path,
    intents: Path,
    pins: Path,
    *,
    clock: Clock = now,
) -> FrozenJSON:
    """Fail isolated, opt-in post-step. Summary selects a date; owners prove it.

    Existing verified completion, original capture and stored batch are required.
    This does not make a collection receipt into an authenticated time authority.
    record replays the bounded input window once and owns per-ticker recovery.
    """
    original = collection.to_dict()
    result: dict[str, Any] = dict(status="skipped", reason="no_successful_collection")
    lineage = None
    try:
        require(
            original.get("version") in ("krx_scheduler_run_v1", "krx_collection_batch_v1"),
            "handoff_collection_report_version",
        )
        rows = (
            original["results"] if original["version"] == "krx_collection_batch_v1" else [original]
        )
        require(type(rows) is list and len(rows) <= 366, "handoff_collection_bound")
        successes = [r for r in rows if r["outcome"] == "succeeded"]
        if successes:
            started = timestamp(clock())
            d = document(str(request))
            require(digest(d) == read_pin(request_pin_file), "shadow_request_pin_mismatch")
            _request(d)
            _paths(d, journal, intents, pins)
            require(
                all(
                    not output.is_relative_to(config.path(name))
                    for output in (journal, intents, pins)
                    for name in ("capture_root", "policy_root", "runner_root")
                ),
                "handoff_output_inside_operational_evidence",
            )
            require(d["mode"] == "research_forward_candidate", "handoff_forward_mode_required")
            require(
                Path(d["operational"]["root"]).resolve()
                == config.path("operational_root").resolve(),
                "handoff_operational_owner_mismatch",
            )
            days, _ = _calendar(d)
            target = d["target_trading_date"]
            # Preserve the original sealed-window guard before replaying even
            # the latest collection source, not just before feature generation.
            _, info = _model(d)
            history, _ = _horizon(d, info, days)
            day = history[-1]
            require(any(r["day"] == day for r in successes), "handoff_collection_day_mismatch")
            completion_at = None
            completion = None
            if d["source_kind"] == "local_provider_capture":
                completed = [r for r in _records(config) if r["day"] == day]
                require(len(completed) == 1, "handoff_verified_completion_missing")
                proof = _verify(config, completed[0])
                completion = completed[0]
                completion_at = proof["at"]
            # Explicit fixture requests stay synthetic. record verifies their
            # committed original batches without asserting real collection.
            checked = timestamp(clock())
            require(started <= checked, "handoff_clock_order")
            require(
                completion_at is None or timestamp(completion_at) <= checked,
                "handoff_completion_after_execution",
            )
            require(
                checked.astimezone(SEOUL).date().isoformat() < target,
                "shadow_target_not_future",
            )
            if completion is not None:
                d, lineage = _derived_request(config, d, completion, intents, pins)

            def ordered_clock() -> datetime:
                nonlocal checked
                value = timestamp(clock())
                require(checked <= value, "handoff_clock_order")
                checked = value
                return value

            recorded = record(
                d, journal, read_pin(head_pin_file), intents, pins, clock=ordered_clock
            ).to_dict()
            result = dict(
                status="blocked" if recorded["blocked"] else "completed",
                input_day=day,
                source_classification=d["source_kind"],
                real_collection_completion_verified=completion_at is not None,
                completion_recorded_at=completion_at,
                completion_time_independently_authenticated=False,
                derived_input=lineage,
                recording=recorded,
            )
    except Exception as error:
        # Collection is already committed. Never change/retry it on a research
        # error, and never claim that a partial journal write did not happen.
        reason = str(error)
        result = dict(
            status="blocked",
            reason=reason if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", reason) else "handoff_failed",
            error_type=type(error).__name__,
            journal_write_outcome="inspect_retained_checkpoints_and_intents",
            derived_input=lineage,
        )
    return FrozenJSON.freeze(
        dict(
            version="krx_shadow_handoff_v1",
            collection=original,
            research=result,
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
            network_requests=0,
        )
    )
