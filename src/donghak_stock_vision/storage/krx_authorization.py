"""Narrow KRX permission review, using existing pinned reviewer-key wire format.

A signed human review binds original permission/terms bytes to exact bounded plans.
It authenticates that review, not KRX publication times or operational trading approval.
No signing, key generation, fetching, credential storage or approval issuance here.
"""

import base64
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, text, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.krx import ENDPOINTS

DOMAIN = b"DonghakStockVision:krx-forward-permission-review:v1\x00"


def bounded(path: Path, maximum: int) -> bytes:
    require(
        path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= maximum,
        "authorization_original_file_missing_or_budget",
    )
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(0 < len(raw) <= maximum, "authorization_original_file_changed_or_budget")
    return raw


def scope_pin(plan: ForwardCapturePlan) -> str:
    plan.__post_init__()  # Partition exclusion before all filesystem access.
    return FrozenJSON.freeze(
        plan.document.to_dict() | {"authorization_reference": None, "terms_reference": None}
    ).identifier


def endpoint(plan: ForwardCapturePlan) -> str:
    return f"https://data-dbg.krx.co.kr/svc/apis/sto/{ENDPOINTS[plan.market]}"


def _official(value: str) -> None:
    url = urlsplit(text(value))
    require(
        url.scheme == "https"
        and url.hostname is not None
        and (url.hostname == "krx.co.kr" or url.hostname.endswith(".krx.co.kr"))
        and url.username is None
        and url.password is None,
        "official_krx_evidence_reference_required",
    )


def review(reference: FrozenJSON, trust: FrozenJSON | None) -> FrozenJSON:
    """Trust registry/pin come from operator configuration, never the grant itself."""
    require(trust is not None, "independent_authorization_reviewer_registry_required")
    assert trust is not None
    t = fields(trust.to_dict(), "registry registry_sha256 operator_id")
    ref = fields(reference.to_dict(), "root grant_pin")
    root = Path(ref["root"])
    require(
        not root.is_symlink() and root.is_dir() and root.stat().st_mode & 0o077 == 0,
        "private_authorization_root_required",
    )
    registry_bytes = bounded(Path(t["registry"]), 65536)
    require(
        hashlib.sha256(registry_bytes).hexdigest() == sha(t["registry_sha256"]),
        "independent_reviewer_registry_pin_mismatch",
    )
    registry = fields(FrozenJSON(registry_bytes.decode()).to_dict(), "schemaVersion keys")
    require(
        registry["schemaVersion"] == "trusted_reviewer_keys_v1"
        and type(registry["keys"]) is list
        and 0 < len(registry["keys"]) <= 32,
        "trusted_reviewer_keys_contract",
    )
    keys = [fields(k, "keyId reviewer publicKeyBase64 status") for k in registry["keys"]]
    for record in keys:
        text(record["keyId"])
        text(record["reviewer"])
        require(record["status"] in ("active", "revoked"), "reviewer_key_status")
        raw_key = base64.b64decode(record["publicKeyBase64"], validate=True)
        require(
            len(raw_key) == 32 and base64.b64encode(raw_key).decode() == record["publicKeyBase64"],
            "invalid_reviewer_public_key",
        )
    require(len({k["keyId"] for k in keys}) == len(keys), "duplicate_reviewer_key_id")
    grant = FrozenJSON(bounded(root / "grant.json", 128 * 1024).decode())
    require(grant.identifier == sha(ref["grant_pin"]), "authorization_grant_pin_mismatch")
    d = fields(
        grant.to_dict(),
        "version reviewer operator_id reviewed_at valid_from valid_until "
        "permission terms captures endpoint transport max_observation_lag_days "
        "uses credential_sha256 capture_root",
    )
    require(d["version"] == "krx_forward_permission_review_v1", "authorization_review_version")
    require(
        d["operator_id"] == text(t["operator_id"]) and text(d["reviewer"]) != d["operator_id"],
        "independent_permission_reviewer_required",
    )
    envelope = fields(
        FrozenJSON(bounded(root / "signature.json", 4096).decode()).to_dict(),
        "version keyId signatureBase64",
    )
    require(
        envelope["version"] == "signed_krx_forward_permission_review_v1",
        "permission_signature_version",
    )
    matching = [k for k in keys if k["keyId"] == envelope["keyId"]]
    require(
        len(matching) == 1
        and matching[0]["status"] == "active"
        and matching[0]["reviewer"] == d["reviewer"],
        "reviewer_untrusted_revoked_or_mismatched",
    )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as error:
        raise ValueError("authorization_verification_requires_existing_api_extra") from error
    try:
        key = base64.b64decode(matching[0]["publicKeyBase64"], validate=True)
        signature = base64.b64decode(envelope["signatureBase64"], validate=True)
        require(len(key) == 32 and len(signature) == 64, "invalid_permission_signature_encoding")
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature, DOMAIN + grant.payload_json.encode()
        )
    except (ValueError, InvalidSignature) as error:
        raise ValueError("invalid_permission_review_signature") from error
    require(
        Path(d["capture_root"]).is_absolute()
        and str(Path(d["capture_root"]).resolve()) == d["capture_root"],
        "canonical_reviewed_capture_root_required",
    )
    sha(d["credential_sha256"])
    require(d["transport"] in ("provider_https", "mock_fixture"), "explicit_origin_review_required")
    require(
        d["uses"] == {"private_capture": True, "forward_signal": True, "paper": True},
        "reviewed_terms_do_not_permit_requested_use",
    )
    for name in ("permission", "terms"):
        evidence = fields(d[name], "issuer source_url sha256")
        require(evidence["issuer"] == "KRX", "permission_issuer_mismatch")
        _official(evidence["source_url"])
        raw = bounded(root / f"{name}.bin", 2 * 1024 * 1024)
        require(
            hashlib.sha256(raw).hexdigest() == sha(evidence["sha256"]),
            f"original_{name}_hash_mismatch",
        )
    start, end, reviewed = (utc(d[n]) for n in ("valid_from", "valid_until", "reviewed_at"))
    require(
        start <= reviewed < end
        and all(
            utc(d[n]).isoformat() == d[n] for n in ("valid_from", "valid_until", "reviewed_at")
        ),
        "invalid_permission_validity",
    )
    require(
        type(d["captures"]) is list and 0 < len(d["captures"]) <= 32,
        "reviewed_capture_count_budget",
    )
    require(
        type(d["max_observation_lag_days"]) is int and 1 <= d["max_observation_lag_days"] <= 7,
        "forward_lag_budget",
    )
    identities = []
    for candidate in d["captures"]:
        candidate = fields(candidate, "plan attempt")
        p = ForwardCapturePlan.from_dict(candidate["plan"])
        require(
            p.mode == "authorized_provider"
            and p.authorization_reference is None
            and p.terms_reference is None,
            "review_template_must_not_embed_grant_identity",
        )
        require(p.day >= reviewed.astimezone(SEOUL).date(), "retrospective_capture_not_forward")
        require(d["endpoint"] == endpoint(p), "reviewed_endpoint_mismatch")
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text(candidate["attempt"])) is not None,
            "invalid_reviewed_attempt_identity",
        )
        identities.append((scope_pin(p), text(candidate["attempt"])))
    require(len(identities) == len(set(identities)), "duplicate_reviewed_capture")
    return grant


def verify_authorization(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON,
    trust: FrozenJSON | None,
    at: datetime,
) -> FrozenJSON:
    plan.__post_init__()
    require(plan.mode == "authorized_provider", "provider_plan_required")
    grant = review(reference, trust)
    d = grant.to_dict()
    require(
        plan.authorization_reference == grant.identifier
        and plan.terms_reference == d["terms"]["sha256"],
        "plan_permission_binding_mismatch",
    )
    require(
        any(
            scope_pin(ForwardCapturePlan.from_dict(c["plan"])) == scope_pin(plan)
            and c["attempt"] == attempt
            for c in d["captures"]
        ),
        "capture_not_in_reviewed_request_budget",
    )
    instant = utc(at)
    review_time(grant, instant)
    require(
        1 <= (instant.astimezone(SEOUL).date() - plan.day).days <= d["max_observation_lag_days"],
        "outside_reviewed_forward_observation_lag",
    )
    return grant


def readiness(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON | None,
    trust: FrozenJSON | None,
    at: datetime,
    credential_present: bool,
    *,
    credential: str | None = None,
    capture_root: Path | None = None,
) -> FrozenJSON:
    plan.__post_init__()
    require(type(credential_present) is bool, "credential_presence_boolean_required")
    blockers = plan.blockers(credential_present)
    grant = None
    try:
        require(reference is not None, "original_permission_terms_and_signed_review_required")
        assert reference is not None
        grant = verify_authorization(plan, attempt, reference, trust, at)
        require(capture_root is not None, "explicit_reviewed_capture_root_required")
        assert capture_root is not None
        capture_root_check(grant, capture_root)
    except (ValueError, OSError, KeyError, TypeError) as error:
        grant = None
        blockers.append(
            str(error) if isinstance(error, ValueError) else "authorization_evidence_unreadable"
        )
    credential_checked = False
    if grant is not None:
        try:
            require(credential is not None, "credential_binding_not_checked")
            assert credential is not None
            verify_credential(grant, credential)
            credential_checked = True
        except ValueError as error:
            blockers.append(str(error))
    return FrozenJSON.freeze(
        {
            "version": "krx_forward_authorization_readiness_v1",
            "plan_pin": plan.document.identifier,
            "endpoint": endpoint(plan),
            "parameters": {"basDd": plan.day.strftime("%Y%m%d")},
            "requests_maximum": plan.max_requests,
            "budgets": {
                n: plan.document.to_dict()[n]
                for n in (
                    "max_requests",
                    "max_response_bytes",
                    "max_seconds",
                    "interval_seconds",
                    "daily_limit",
                )
            },
            "valid_from": grant.to_dict()["valid_from"] if grant else None,
            "valid_until": grant.to_dict()["valid_until"] if grant else None,
            "explicit_network_opt_in_still_required": True,
            "concurrency": 1,
            "authorization_verified": grant is not None,
            "authorization_basis": "pinned_independent_original_document_review",
            "provider_signature_verified": False,
            "credential_present": credential_present,
            "credential_binding_verified": credential_checked,
            "grant_pin": grant.identifier if grant else None,
            "network_requests": 0,
            "origin": grant.to_dict()["transport"] if grant else None,
            "blockers": list(dict.fromkeys(blockers)),
            "ready_for_explicit_capture": not blockers,
            "live_provider_collection_eligible": not blockers
            and grant is not None
            and grant.to_dict()["transport"] == "provider_https",
            "pit_qualified": False,
            "live_orders_authorized": False,
        }
    )


def review_time(grant: FrozenJSON, at: datetime) -> None:
    d = grant.to_dict()
    require(
        utc(d["reviewed_at"]) <= utc(at)
        and utc(d["valid_from"]) <= utc(at) < utc(d["valid_until"]),
        "provider_permission_expired_or_not_yet_valid",
    )


def cli_trust(args: Any) -> FrozenJSON | None:
    values = (args.reviewer_registry, args.reviewer_registry_pin, args.operator_id)
    if all(v is None for v in values):
        return None
    require(
        all(v is not None for v in values), "explicit_reviewer_registry_pin_and_operator_required"
    )
    return FrozenJSON.freeze(
        {
            "registry": str(args.reviewer_registry),
            "registry_sha256": sha(args.reviewer_registry_pin),
            "operator_id": text(args.operator_id),
        }
    )


def add_trust_arguments(cmd: Any) -> None:
    cmd.add_argument("--reviewer-registry", type=Path)
    cmd.add_argument("--reviewer-registry-pin")
    cmd.add_argument("--operator-id")


def current_operation(origin: str, declared_at: datetime) -> None:
    """New real-origin operations cannot masquerade as old recorded forward actions.

    Historical verification uses original timestamps and does not call this gate.
    Mock fixtures have their own explicitly synthetic clock.
    """
    if origin == "real_provider_https_local_capture":
        from datetime import UTC

        require(
            abs((datetime.now(UTC) - utc(declared_at)).total_seconds()) <= 60,
            "new_provider_operation_requires_current_local_utc_clock",
        )


def verify_credential(grant: FrozenJSON, secret: str) -> None:
    require(
        bool(secret)
        and secret == secret.strip()
        and hashlib.sha256(
            b"DonghakStockVision:krx-credential-binding:v1\x00" + secret.encode()
        ).hexdigest()
        == grant.to_dict()["credential_sha256"],
        "credential_not_bound_to_reviewed_service_permission",
    )


def capture_root_check(grant: FrozenJSON, root: Path) -> None:
    require(
        not root.is_symlink() and str(root.resolve()) == grant.to_dict()["capture_root"],
        "capture_root_outside_reviewed_request_budget",
    )


INDEPENDENT = "independent_review_v1"
OPERATOR = "operator_read_only_pilot_v1"
OPERATOR_ACK = "private_operator_review_not_independent_authorization"


def verify_operator_authorization(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON,
    trust: FrozenJSON | None,
    at: datetime,
) -> FrozenJSON:
    """Owner-local permission review; never independent/PIT/signal/PAPER authority.

    The operator is trusted to examine originals. Hashes prove integrity, not issuer
    authenticity. This deliberately separate policy is limited to one read-only call.
    """
    from datetime import date, timedelta

    plan.__post_init__()  # Exclude protected partitions before reading even policy files.
    require(plan.mode == "authorized_provider", "operator_policy_requires_provider_plan")
    require(trust is None, "operator_policy_cannot_replace_independent_reviewer_trust")
    require(
        plan.market == "KOSPI"
        and plan.ticker == "005930"
        and plan.max_requests == 1
        and plan.daily_limit == 1
        and plan.max_seconds <= 30,
        "operator_one_request_kospi_005930_budget_required",
    )
    ref = fields(reference.to_dict(), "root grant_pin")
    root = Path(ref["root"])
    require(
        root.is_dir() and not root.is_symlink() and root.stat().st_mode & 0o077 == 0,
        "private_authorization_root_required",
    )
    policy = FrozenJSON(bounded(root / "operator-policy.json", 32768).decode())
    require(policy.identifier == sha(ref["grant_pin"]), "operator_policy_pin_mismatch")
    d = fields(
        policy.to_dict(),
        "version operator_id acknowledgement reviewed_at valid_from valid_until "
        "approved_from approved_through service_id method permission terms specification "
        "captures endpoint capture_root transport document_origin max_observation_lag_days "
        "credential_sha256 uses",
    )
    require(d["version"] == "krx_operator_read_only_pilot_v1", "operator_policy_version")
    text(d["operator_id"])
    require(d["acknowledgement"] == OPERATOR_ACK, "explicit_owner_local_review_required")
    require(d["service_id"] == "stk_bydd_trd" and d["method"] == "GET", "wrong_operator_service")
    require(d["endpoint"] == endpoint(plan), "operator_endpoint_mismatch")
    require(
        d["uses"] == {"private_capture": True, "forward_signal": False, "paper": False}
        and all(type(v) is bool for v in d["uses"].values()),
        "operator_policy_read_only_no_signal_or_paper",
    )
    require(
        (d["transport"], d["document_origin"])
        in (
            ("provider_https", "operator_supplied_provider_originals"),
            ("mock_fixture", "synthetic_test_fixture"),
        ),
        "explicit_operator_document_origin_required",
    )
    start, end, reviewed = (utc(d[n]) for n in ("valid_from", "valid_until", "reviewed_at"))
    require(
        start <= reviewed < end
        and end - start <= timedelta(hours=24)
        and all(
            utc(d[n]).isoformat() == d[n] for n in ("valid_from", "valid_until", "reviewed_at")
        ),
        "operator_pilot_window_invalid_or_excessive",
    )
    approved_from = date.fromisoformat(d["approved_from"])
    approved_through = date.fromisoformat(d["approved_through"])
    require(
        approved_from <= approved_through
        and approved_from <= start.astimezone(SEOUL).date() <= approved_through
        and approved_from
        <= (end - timedelta(microseconds=1)).astimezone(SEOUL).date()
        <= approved_through
        and approved_from <= plan.day <= approved_through,
        "operator_service_approval_outside_validity",
    )
    require(
        type(d["captures"]) is list and len(d["captures"]) == 1,
        "operator_exactly_one_capture_required",
    )
    c = fields(d["captures"][0], "plan attempt")
    template = ForwardCapturePlan.from_dict(c["plan"])
    require(
        template.authorization_reference is None
        and template.terms_reference is None
        and scope_pin(template) == scope_pin(plan)
        and c["attempt"] == attempt
        and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text(attempt)) is not None,
        "operator_capture_scope_mismatch",
    )
    require(
        type(d["max_observation_lag_days"]) is int and 1 <= d["max_observation_lag_days"] <= 7,
        "operator_observation_lag_budget",
    )
    for name in ("permission", "terms", "specification"):
        evidence = fields(d[name], "issuer source_url sha256")
        require(evidence["issuer"] == "KRX", "operator_document_issuer_mismatch")
        _official(evidence["source_url"])
        raw = bounded(root / f"{name}.bin", 2 * 1024 * 1024)
        require(
            hashlib.sha256(raw).hexdigest() == sha(evidence["sha256"]),
            f"operator_{name}_hash_mismatch",
        )
        require(
            len(raw) >= 32
            and evidence["sha256"]
            != "120f225f7f86042485433a7af2f941736eea2314f56b4417a3c4a219447955c4"
            and not re.search(rb"<title[^>]*>[^<]*(?:403|error|forbidden|access denied)", raw, re.I)
            and "에러페이지".encode() not in raw,
            "failed_or_incomplete_page_is_not_operator_permission_evidence",
        )
    require(
        plan.authorization_reference == policy.identifier
        and plan.terms_reference == d["terms"]["sha256"],
        "operator_plan_policy_binding_mismatch",
    )
    require(
        Path(d["capture_root"]).is_absolute()
        and str(Path(d["capture_root"]).resolve()) == d["capture_root"],
        "canonical_reviewed_capture_root_required",
    )
    sha(d["credential_sha256"])
    review_time(policy, at)
    require(
        1 <= (utc(at).astimezone(SEOUL).date() - plan.day).days <= d["max_observation_lag_days"],
        "outside_operator_read_only_observation_lag",
    )
    return policy


def operator_readiness(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON | None,
    trust: FrozenJSON | None,
    at: datetime,
    credential_present: bool,
    *,
    credential: str | None = None,
    capture_root: Path | None = None,
) -> FrozenJSON:
    plan.__post_init__()
    blockers = plan.blockers(credential_present)
    policy = None
    credential_checked = False
    try:
        require(
            reference is not None,
            "original_permission_terms_specification_and_operator_policy_required",
        )
        assert reference is not None
        policy = verify_operator_authorization(plan, attempt, reference, trust, at)
        require(capture_root is not None, "explicit_reviewed_capture_root_required")
        assert capture_root is not None
        capture_root_check(policy, capture_root)
    except (ValueError, OSError, KeyError, TypeError) as error:
        policy = None
        blockers.append(
            str(error) if isinstance(error, ValueError) else "operator_evidence_unreadable"
        )
    if policy is not None:
        try:
            require(credential is not None, "credential_binding_not_checked")
            assert credential is not None
            require(
                len(credential) <= 1024 and credential.isascii() and credential.isprintable(),
                "invalid_operator_credential_header",
            )
            verify_credential(policy, credential)
            credential_checked = True
        except ValueError as error:
            blockers.append(str(error))
    return FrozenJSON.freeze(
        {
            "version": "krx_operator_capture_readiness_v1",
            "policy": OPERATOR,
            "plan_pin": plan.document.identifier,
            "endpoint": endpoint(plan),
            "method": "GET",
            "parameters": {"basDd": plan.day.strftime("%Y%m%d")},
            "requests_maximum": plan.max_requests,
            "budgets": {
                n: plan.document.to_dict()[n]
                for n in (
                    "max_requests",
                    "max_response_bytes",
                    "max_seconds",
                    "interval_seconds",
                    "daily_limit",
                )
            },
            "concurrency": 1,
            "valid_from": policy.to_dict()["valid_from"] if policy else None,
            "valid_until": policy.to_dict()["valid_until"] if policy else None,
            "approved_from": policy.to_dict()["approved_from"] if policy else None,
            "approved_through": policy.to_dict()["approved_through"] if policy else None,
            "operator_id": policy.to_dict()["operator_id"] if policy else None,
            "operator_policy_integrity_verified": policy is not None,
            "authorization_basis": "explicit_owner_local_original_document_review",
            "independent_authorization_verified": False,
            "provider_signature_verified": False,
            "credential_present": credential_present,
            "credential_binding_verified": credential_checked,
            "policy_pin": policy.identifier if policy else None,
            "origin": policy.to_dict()["transport"] if policy else None,
            "blockers": list(dict.fromkeys(blockers)),
            "ready_for_explicit_read_only_capture": not blockers,
            "explicit_operator_confirmation_still_required": True,
            "explicit_network_opt_in_still_required": True,
            "network_requests": 0,
            "forward_signal_eligible": False,
            "paper_execution_eligible": False,
            "pit_qualified": False,
            "live_orders_authorized": False,
        }
    )
