"""Offline verified-capture bridge. Never expands a real capture's instrument scope."""

from contextlib import closing
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.forward_capture import (
    readback,
    verify_capture,
    verify_capture_authorization,
    verify_operator_capture_authorization,
)
from donghak_stock_vision.storage.operational_market import OperationalMarketStore, _parse


def ingest_capture(
    storage_root: Path,
    capture_root: Path,
    plan: ForwardCapturePlan,
    plan_pin: str,
    attempt: str,
    capture_pin: str,
    *,
    sequence: int = 0,
    authorization: FrozenJSON | None = None,
    trust: FrozenJSON | None = None,
    authorization_policy: str = "independent_review_v1",
    allow_synthetic_fixture: bool = False,
) -> FrozenJSON:
    """Verify original owners before opening the destination for an atomic batch write.

    Legacy real plans authorize one ticker. Full-market provider ingestion requires
    the distinct recurring scope and original full-market operator review.
    Real captures with extra tickers fail closed, including the original operator pilot.
    Reinvocation independently re-verifies the original archive even on idempotent retries.
    """
    plan.__post_init__()  # Protected partitions rejected before any source I/O.
    require(plan.document.identifier == sha(plan_pin), "plan_pin_mismatch")
    require(plan.market == "KOSPI", "operational_capture_requires_kospi")
    source_root, destination = capture_root.resolve(), storage_root.resolve()
    require(
        not destination.is_relative_to(source_root) and not source_root.is_relative_to(destination),
        "separate_operational_storage_required",
    )
    require(
        authorization_policy
        in (
            "independent_review_v1",
            "operator_read_only_pilot_v1",
            "operator_full_market_recurring_v1",
        ),
        "unknown_authorization_policy",
    )
    require(
        (plan.full_market_scope is not None)
        == (authorization_policy == "operator_full_market_recurring_v1"),
        "full_market_requires_distinct_recurring_policy",
    )
    owner = verify_capture(capture_root, plan, attempt, capture_pin)
    doc = owner.to_dict()
    responses = doc["responses"]
    require(type(sequence) is int and 0 <= sequence < len(responses), "unknown_response")
    response = responses[sequence]
    metadata = response["metadata"]
    require(
        metadata["complete"] is True
        and metadata["http_status"] == 200
        and metadata["error"] is None,
        "complete_successful_http_response_required",
    )
    if plan.mode == "offline_fixture":
        require(
            allow_synthetic_fixture and authorization is None and trust is None,
            "explicit_synthetic_fixture_acknowledgement_required",
        )
        origin = "offline_fixture"
    else:
        require(not allow_synthetic_fixture, "provider_capture_cannot_be_relabelled_synthetic")
        require(authorization is not None, "original_authorization_required")
        assert authorization is not None
        at = utc(metadata["response_completed_at"])
        if authorization_policy in (
            "operator_read_only_pilot_v1",
            "operator_full_market_recurring_v1",
        ):
            require(trust is None, "operator_policy_cannot_use_reviewer_trust")
            origin = verify_operator_capture_authorization(
                capture_root, plan, attempt, capture_pin, authorization, at
            )
        else:
            origin = verify_capture_authorization(
                capture_root, plan, attempt, capture_pin, authorization, trust, at
            )
    raw = readback(capture_root, plan, attempt, capture_pin, sequence)
    page = RawPage(raw, utc(metadata["response_completed_at"]), plan.day)
    encoding = metadata["headers"].get("content-encoding", "")
    representation = metadata.get("body_representation", "wire")
    # Validate the WHOLE response before any writable destination access. No row filtering.
    bars, _, _ = _parse(page, plan.market, encoding, representation, 10000)
    require(bool(bars), "nonempty_market_response_required")
    if plan.mode == "authorized_provider" and plan.full_market_scope is None:
        require(
            all(bar.ticker == plan.ticker for bar in bars),
            "full_market_ingestion_not_authorized_by_single_ticker_plan",
        )
    reference = FrozenJSON.freeze(
        {
            "version": "operational_capture_reference_v1",
            "capture_id": doc["capture_id"],
            "capture_pin": sha(capture_pin),
            "plan_pin": sha(plan_pin),
            "authorization_pin": (authorization.to_dict()["grant_pin"] if authorization else None),
            "trust_pin": trust.identifier if trust else None,
            "sequence": sequence,
            "raw_id": response["raw_id"],
            "receipt_pin": FrozenJSON.freeze(response["receipt"]).identifier,
            "raw_sha256": response["receipt"]["rawSha256"],
            "market": plan.market,
            "day": plan.day.isoformat(),
            "response_at": metadata["response_completed_at"],
            "origin": origin,
        }
    )
    # Reference identity includes original receipt/manifest and scope, never filesystem paths.
    batch_id = reference.identifier
    with closing(OperationalMarketStore(storage_root, writable=True)) as store:
        batch = store.ingest(
            batch_id,
            plan.market,
            page,
            source_kind=(
                "synthetic_fixture" if origin == "offline_fixture" else "local_response_unqualified"
            ),
            content_encoding=encoding,
            representation=representation,
            source_capture=reference,
        )
    return FrozenJSON.freeze(
        {
            "version": "operational_capture_ingestion_v1",
            "batch_id": batch_id,
            "batch_pin": batch.identifier,
            "observation_count": batch.to_dict()["observation_count"],
            "source_origin": origin,
            "original_ingestion": doc["ingestion"],
            "original_parse_error": response["parse_error"],
            "original_capture_changed": False,
            "verification": (
                "original_capture_semantic_replay"
                if origin == "offline_fixture"
                else "original_capture_and_authorization_semantic_replay"
            ),
            "eligibility": batch.to_dict()["eligibility"],
            "network_requests": 0,
        }
    )
