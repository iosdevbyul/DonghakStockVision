"""Read-only, pinned derivation from a sealed operator capture, not original ingestion."""

from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.providers.krx_response import decode_response
from donghak_stock_vision.storage.forward_capture import (
    _RecordedProvider,
    readback,
    verify_capture,
    verify_operator_capture_authorization,
)


def recover_capture(
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    capture_pin: str,
    authorization: FrozenJSON,
) -> FrozenJSON:
    """Verify original source owners before decoding; never write to the source archive."""
    plan.__post_init__()
    require(plan.mode == "authorized_provider", "operator_capture_recovery_required")
    owner = verify_capture(root, plan, attempt, capture_pin)
    doc = owner.to_dict()
    require(len(doc["responses"]) == 1, "one_original_response_required")
    response = doc["responses"][0]
    metadata = response["metadata"]
    require(
        metadata["complete"] and metadata["http_status"] == 200 and metadata["error"] is None,
        "complete_successful_http_response_required",
    )
    origin = verify_operator_capture_authorization(
        root, plan, attempt, capture_pin, authorization, utc(metadata["response_completed_at"])
    )
    raw = readback(root, plan, attempt, capture_pin, 0)
    decoded = decode_response(
        raw,
        metadata["headers"].get("content-encoding", ""),
        metadata.get("body_representation", "wire"),
        plan.max_response_bytes,
    )
    provider = _RecordedProvider(
        plan, RawPage(decoded, utc(metadata["response_completed_at"]), plan.day)
    )
    bars = provider.parse_market(provider.page)
    selected = [b for b in bars if b.ticker == plan.ticker]
    require(len(selected) == 1, "exactly_one_scoped_observation_required")
    return FrozenJSON.freeze(
        {
            "version": "krx_capture_recovery_v1",
            "source_capture_pin": sha(capture_pin),
            "source_capture_id": doc["capture_id"],
            "source_plan_pin": plan.document.identifier,
            "source_policy_pin": authorization.to_dict()["grant_pin"],
            "source_receipt": {
                "raw_id": response["raw_id"],
                "raw_sha256": response["receipt"]["rawSha256"],
                "receipt_hash": FrozenJSON.freeze(response["receipt"]).identifier,
            },
            "source_origin": origin,
            "source_response_at": metadata["response_completed_at"],
            "original_parser": doc["parser"],
            "original_parse_error": response["parse_error"],
            "original_ingestion": doc["ingestion"],
            "original_capture_changed": False,
            "recovery_status": "derived_observation_only",
            "parser": "KRXProvider-daily-unadjusted-v2",
            "content_encoding": metadata["headers"].get("content-encoding", ""),
            "encoded_bytes": len(raw),
            "decoded_bytes": len(decoded),
            "market_row_count": len(bars),
            "observations": [selected[0].to_dict()],
            "network_requests": 0,
            "independent_authorization_verified": False,
            "forward_signal_eligible": False,
            "paper_execution_eligible": False,
            "pit_qualified": False,
            "execution_authorized": False,
        }
    )


def verify_recovery(
    artifact: FrozenJSON,
    artifact_pin: str,
    root: Path,
    plan: ForwardCapturePlan,
    attempt: str,
    capture_pin: str,
    authorization: FrozenJSON,
) -> None:
    require(artifact.identifier == sha(artifact_pin), "recovery_pin_mismatch")
    require(
        recover_capture(root, plan, attempt, capture_pin, authorization) == artifact,
        "recovery_semantic_mismatch",
    )
