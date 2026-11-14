"""Offline projection of existing capture/receipt/storage times, never a publication oracle."""

from contextlib import closing
from datetime import datetime, time
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import ProviderError
from donghak_stock_vision.storage.forward_capture import (
    _connection,
    _RecordedProvider,
    _response_page,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.forward_raw_source_custody import link_forward_raw_source
from donghak_stock_vision.storage.krx_backlog import _records
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_capture import verify_stored_capture
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource
from donghak_stock_vision.storage.shadow_prediction import _calendar, _horizon, _model, _request


def _timing(
    received: datetime, stored: datetime | None, checked: datetime, deadline: datetime | None
) -> dict[str, Any]:
    require(
        received <= checked and (stored is None or received <= stored <= checked),
        "availability_clock_order",
    )
    return dict(
        response_before_deadline=received < deadline if deadline else None,
        storage_completion_before_deadline=stored < deadline if deadline and stored else None,
        execution_window_open=checked < deadline if deadline else None,
    )


def assess_availability(
    config: SchedulerConfig,
    capture: CaptureSource,
    at: datetime,
    *,
    batch_pin: str | None = None,
    research_request: dict[str, Any] | None = None,
) -> FrozenJSON:
    """Read original evidence only. Successful HTTP status alone never proves availability.

    Capture times and scheduler completion timestamps are local unauthenticated
    records. The latter is an upper bound after ingestion and validation, not
    SQLite's exact commit instant. An empty original remains empty/unavailable.
    """
    checked = timestamp(at)
    plan = capture.plan
    require(
        capture.root == config.path("capture_root")
        and plan.full_market_scope is not None
        and plan.market == "KOSPI"
        and plan.mode == "authorized_provider"
        and plan.max_requests == 1
        and capture.sequence == 0,
        "availability_single_original_full_market_capture_required",
    )
    deadline = None
    target = None
    model_identifier = None
    if research_request is not None:
        _request(research_request)
        require(
            research_request["mode"] == "research_forward_candidate",
            "availability_forward_request_required",
        )
        require(
            research_request["operational"]["root"] == str(config.path("operational_root")),
            "availability_research_source_owner",
        )
        _, model = _model(research_request)
        model_identifier = model["identifier"]
        require(timestamp(model["created_at"]) <= checked, "availability_model_after_execution")
        days, _ = _calendar(research_request)
        history, future = _horizon(research_request, model, days)
        require(plan.day.isoformat() == history[-1], "availability_event_input_mismatch")
        target = future[0]
        deadline = datetime.combine(datetime.fromisoformat(target).date(), time(), SEOUL)

    linked = link_forward_raw_source(capture, archive_namespace="krx-kospi-forward")
    binding, raw = linked.binding.to_dict(), linked.source
    # The original link owner has already replayed this manifest, receipt and
    # exact raw bytes. Read its pinned document to project metadata, not replay
    # another response or construct another source representation.
    with closing(_connection(capture.root)) as connection:
        row = connection.execute(
            "SELECT document,pin FROM forward_manifests WHERE capture_id=?",
            (binding["capture_id"],),
        ).fetchone()
        require(row is not None, "availability_original_manifest_missing")
        manifest = FrozenJSON(row[0])
        require(
            manifest.identifier == row[1] == capture.capture_pin,
            "availability_original_manifest_changed",
        )
    responses = manifest.to_dict()["responses"]
    require(len(responses) == 1, "availability_original_response_count")
    response = responses[0]
    metadata = response["metadata"]
    started, received = (
        timestamp(metadata["request_started_at"]),
        timestamp(metadata["response_completed_at"]),
    )
    require(started <= received <= checked, "availability_clock_order")
    origin = verify_operator_capture_authorization(
        capture.root, plan, capture.attempt, capture.capture_pin, config.reference, received
    )
    count = None
    schema_valid = False
    state = "http_failure"
    if not metadata["complete"] or metadata["error"] is not None:
        state = "incomplete_or_failed_response"
    elif metadata["http_status"] == 200:
        try:
            page = _response_page(plan, raw.body, metadata)
            bars = _RecordedProvider(plan, page).parse_market(page)
            require(
                len(bars) <= 10000 and len({b.ticker for b in bars}) == len(bars),
                "availability_invalid_membership",
            )
            count = len(bars)
            schema_valid = True
            state = "available_not_stored" if count else "empty_response"
            if count:
                require(response["parse_error"] is None, "availability_original_parser_conflict")
        except (ValueError, ProviderError):
            state, count, schema_valid = "invalid_response", None, False
    stored_at = None
    stored = None
    if batch_pin is not None:
        require(
            state == "available_not_stored", "availability_nonempty_source_required_for_storage"
        )
        stored = verify_stored_capture(
            config.path("operational_root"),
            capture.root,
            plan,
            capture.plan_pin,
            capture.attempt,
            capture.capture_pin,
            batch_pin,
            authorization=config.reference,
            authorization_policy="operator_full_market_recurring_v1",
        ).to_dict()
        require(stored["observation_count"] == count, "availability_stored_count_mismatch")
        records = [
            r
            for r in _records(config)
            if r["day"] == plan.day.isoformat()
            and r["plan_pin"] == capture.plan_pin
            and r["capture_pin"] == capture.capture_pin
            and r["batch_pin"] == batch_pin
        ]
        require(len(records) <= 1, "availability_completion_conflict")
        stored_at = timestamp(records[0]["at"]) if records else None
        state = "available_and_stored"
    timing = _timing(received, stored_at, checked, deadline)
    return FrozenJSON.freeze(
        dict(
            version="krx_capture_availability_v1",
            api="stk_bydd_trd",
            trading_date=plan.day.isoformat(),
            origin=origin,
            verified_at=checked.isoformat(),
            clock_source=manifest.to_dict()["clock_source"],
            availability=state,
            request_started_at=started.isoformat(),
            request_ended_at=received.isoformat(),
            request_end_basis="original_response_completed_at",
            response_received_at=received.isoformat() if metadata["complete"] else None,
            response_received_basis="complete_body_observation_not_header_arrival",
            capture_finished_at=received.isoformat(),
            http_status=metadata["http_status"],
            response_complete=metadata["complete"],
            schema_valid=schema_valid,
            observation_count=count,
            raw_sha256=raw.raw_sha256,
            source_pin=raw.identifier,
            source_pin_contract="capture_raw_source_custody_v1",
            capture_pin=capture.capture_pin,
            sqlite_batch_pin=batch_pin,
            sqlite_batch_id=stored["batch_id"] if stored else None,
            sqlite_storage_verified_at=checked.isoformat() if stored else None,
            sqlite_storage_completion_recorded_at=stored_at.isoformat() if stored_at else None,
            storage_completion_basis="local_scheduler_record_after_ingestion_and_integrity_validation",
            sqlite_commit_at=None,
            independent_storage_time_verified=False,
            sqlite_commit_time_reason="original_store_does_not_retain_commit_timestamp",
            successful_response_observed_at=received.isoformat()
            if state.startswith("available_")
            else None,
            provider_data_observed=origin == "real_provider_https_local_capture"
            and state.startswith("available_"),
            observation_is_first_provider_publication=False,
            official_publication_at=None,
            official_publication_time="UNKNOWN",
            independent_publication_time_verified=False,
            prediction_target_date=target,
            research_request_pin=digest(research_request) if research_request is not None else None,
            research_model_identifier=model_identifier,
            generation_deadline_exclusive=deadline.isoformat() if deadline else None,
            deadline_basis="unchanged_shadow_midnight_guard" if deadline else None,
            target_alignment_verified=research_request is not None,
            **timing,
            data_available_before_deadline=(
                state.startswith("available_") and received < deadline
                if deadline is not None
                else None
            ),
            full_input_and_inference_verification_required=True,
            prediction_created=False,
            account_remaining_quota="UNKNOWN",
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
            network_requests=0,
        )
    )
