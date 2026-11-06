"""Actual signatures and public RFC8032 vector; ephemeral synthetic signing keys only."""

import base64
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prospective_external_receipt import (
    DOMAIN,
    MAX_DOCUMENT,
    ReceiptOwner,
    _verify_signature,
    verify_receipts,
    verify_report,
)

AT = datetime(2025, 7, 14, tzinfo=UTC)
OUTCOME = datetime(2025, 7, 13, tzinfo=UTC)


def fixture() -> tuple[tuple[ReceiptOwner, ...], FrozenJSON, Ed25519PrivateKey]:
    # Generated in memory for each test. No private key is stored or embedded.
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    anchor = FrozenJSON.freeze(
        {
            "version": "prospective_receipt_trust_anchor_v1",
            "issuer": "synthetic_test_issuer",
            "key_id": "a" * 64,
            "public_key_base64": public,
            "valid_from": "2025-07-01T00:00:00+00:00",
            "valid_until": "2025-08-01T00:00:00+00:00",
            "status": "active",
            "revoked_receipt_ids": [],
        }
    )
    owners = []
    for index, (role, version) in enumerate(
        (
            ("policy", "prospective_evaluation_policy_v1"),
            ("prediction", "forward_prediction_evidence_v1"),
        )
    ):
        artifact = FrozenJSON.freeze({"version": version, "origin": "synthetic_test_artifact"})
        payload = {
            "receipt_id": str(index + 1) * 64,
            "issuer": "synthetic_test_issuer",
            "key_id": "a" * 64,
            "artifact_role": role,
            "artifact_id": artifact.identifier,
            "artifact_digest": artifact.identifier,
            "artifact_encoding": "dsv_canonical_json_sha256_v1",
            "issued_at": f"2025-07-{10 + index:02d}T00:00:00+00:00",
        }
        receipt = signed(payload, key)
        owners.append(
            ReceiptOwner(
                role,
                artifact,
                artifact.identifier,
                payload["receipt_id"],
                receipt,
                receipt.identifier,
            )
        )
    return tuple(owners), anchor, key


def signed(payload: dict[str, Any], key: Ed25519PrivateKey) -> FrozenJSON:
    sig = key.sign(DOMAIN + FrozenJSON.freeze(payload).payload_json.encode())
    return FrozenJSON.freeze(
        {
            "version": "prospective_ed25519_receipt_v1",
            "algorithm": "Ed25519",
            "payload": payload,
            "signature_base64": base64.b64encode(sig).decode(),
        }
    )


def test_rfc8032_section_7_1_test_1_public_vector() -> None:
    # https://www.rfc-editor.org/rfc/rfc8032.html#section-7.1
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    _verify_signature(public, signature, b"")
    from cryptography.exceptions import InvalidSignature

    with pytest.raises(InvalidSignature):
        _verify_signature(public, signature, b"altered")


def test_real_crypto_success_never_authenticates_independence_and_no_io() -> None:
    owners, anchor, _ = fixture()
    with (
        patch("builtins.open", side_effect=AssertionError("no I/O")),
        patch("httpx.Client.send", side_effect=AssertionError("no network")),
    ):
        report = verify_receipts(owners, anchor, anchor.identifier, AT, outcome_observed_at=OUTCOME)
        d = report.to_dict()
        verify_report(
            report,
            report.identifier,
            owners,
            anchor,
            anchor.identifier,
            AT,
            outcome_observed_at=OUTCOME,
        )
    assert all(row["state"] == "verified_cryptographic_structure" for row in d["receipts"])
    assert d["signed_time_order_consistent"] is True and d["state"] == "chronology_unresolved"
    assert all(
        d[k] is False
        for k in (
            "issuer_independence_verified",
            "independently_authenticated_chronology",
            "genuine_forward_evidence",
            "pit_qualified",
            "performance_approved",
            "execution_authorized",
        )
    )
    assert (
        verify_receipts(
            tuple(owners),
            FrozenJSON(anchor.payload_json),
            anchor.identifier,
            AT,
            outcome_observed_at=OUTCOME,
        )
        == report
    )


@pytest.mark.parametrize(
    "kind",
    [
        "signature",
        "payload",
        "artifact",
        "issuer",
        "algorithm",
        "format",
        "approval",
        "missing",
        "receipt_identity",
        "pin",
        "bad_base64",
    ],
)
def test_alterations_and_unsupported_receipts(kind: str) -> None:
    owners, anchor, key = fixture()
    first = owners[0]
    assert first.receipt is not None
    d = first.receipt.to_dict()
    expected = "invalid"
    if kind == "signature":
        d["signature_base64"] = base64.b64encode(b"x" * 64).decode()
        expected = "invalid_signature"
    elif kind == "payload":
        d["payload"]["issued_at"] = "2025-07-09T00:00:00+00:00"
        expected = "invalid_signature"
    elif kind == "artifact":
        changed = FrozenJSON.freeze(
            {"version": "prospective_evaluation_policy_v1", "changed": True}
        )
        first = replace(first, artifact=changed, artifact_pin=changed.identifier)
        expected = "artifact_mismatch"
    elif kind == "issuer":
        d["payload"]["issuer"] = "wrong_issuer"
    elif kind == "algorithm":
        d["algorithm"] = "none"
        expected = "unsupported_receipt"
    elif kind == "format":
        d["version"] = "rfc3161"
        expected = "unsupported_receipt"
    elif kind == "approval":
        d["approved"] = True
    elif kind == "missing":
        first = replace(first, receipt=None, receipt_pin=None)
        expected = "unsupported_receipt"
    elif kind == "receipt_identity":
        d["payload"]["receipt_id"] = "f" * 64
    elif kind == "pin":
        first = replace(first, receipt_pin="0" * 64)
    elif kind == "bad_base64":
        d["signature_base64"] = "!"
    if kind not in {"artifact", "missing", "pin"}:
        receipt = FrozenJSON.freeze(d)
        first = replace(first, receipt=receipt, receipt_pin=receipt.identifier)
    row = verify_receipts((first, owners[1]), anchor, anchor.identifier, AT).to_dict()["receipts"][
        0
    ]
    assert row["state"] == expected and not row["signature_verified"]


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "wrong_pin",
        "wrong_key",
        "revoked",
        "receipt_revoked",
        "expired",
        "version",
        "duplicate_revocations",
    ],
)
def test_explicit_trust_configuration(kind: str) -> None:
    owners, anchor, _ = fixture()
    if kind == "missing":
        d = verify_receipts(owners, None, None, AT).to_dict()
        assert all(row["state"] == "missing_trust_anchor" for row in d["receipts"])
        return
    a = anchor.to_dict()
    if kind == "wrong_key":
        a["public_key_base64"] = base64.b64encode(
            Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        ).decode()
    elif kind == "revoked":
        a["status"] = "revoked"
    elif kind == "receipt_revoked":
        a["revoked_receipt_ids"] = [owners[0].receipt_id, owners[1].receipt_id]
    elif kind == "expired":
        a["valid_until"] = "2025-07-02T00:00:00+00:00"
    elif kind == "version":
        a["version"] = "unknown"
    elif kind == "duplicate_revocations":
        a["revoked_receipt_ids"] = ["f" * 64, "f" * 64]
    anchor = FrozenJSON.freeze(a)
    pin = "0" * 64 if kind == "wrong_pin" else anchor.identifier
    assert all(
        not row["signature_verified"]
        for row in verify_receipts(owners, anchor, pin, AT).to_dict()["receipts"]
    )


def test_signed_inconsistent_time_and_missing_outcome() -> None:
    owners, anchor, key = fixture()
    first = owners[0]
    assert first.receipt is not None
    p = first.receipt.to_dict()["payload"]
    p["issued_at"] = "2025-07-12T00:00:00+00:00"
    receipt = signed(p, key)
    bad = (replace(first, receipt=receipt, receipt_pin=receipt.identifier), owners[1])
    report = verify_receipts(
        bad, anchor, anchor.identifier, AT, outcome_observed_at=OUTCOME
    ).to_dict()
    assert report["state"] == "invalid" and report["signed_time_order_consistent"] is False
    assert (
        verify_receipts(owners, anchor, anchor.identifier, AT).to_dict()[
            "outcome_time_authenticated"
        ]
        is False
    )


def test_duplicate_bounds_and_rehashed_report() -> None:
    owners, anchor, _ = fixture()
    with pytest.raises(ValueError):
        verify_receipts((owners[0], owners[0]), anchor, anchor.identifier, AT)
    huge = FrozenJSON.freeze({"large": "x" * MAX_DOCUMENT})
    with pytest.raises(ValueError, match="budget"):
        verify_receipts(
            (replace(owners[0], receipt=huge), owners[1]), anchor, anchor.identifier, AT
        )
    report = verify_receipts(owners, anchor, anchor.identifier, AT)
    d = report.to_dict()
    d["genuine_forward_evidence"] = True
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_report(forged, forged.identifier, owners, anchor, anchor.identifier, AT)


@pytest.mark.parametrize(
    "issued", ["2025-07-10T00:00:00", "2025-09-01T00:00:00+00:00", "2025-07-10T09:00:00+09:00"]
)
def test_invalid_issued_instants_never_become_signed_time(issued: str) -> None:
    owners, anchor, key = fixture()
    first = owners[0]
    assert first.receipt is not None
    payload = first.receipt.to_dict()["payload"]
    payload["issued_at"] = issued
    receipt = signed(payload, key)
    row = verify_receipts(
        (replace(first, receipt=receipt, receipt_pin=receipt.identifier), owners[1]),
        anchor,
        anchor.identifier,
        AT,
    ).to_dict()["receipts"][0]
    assert not row["signature_verified"] and row["signed_issuer_time"] is None


def test_outcome_context_and_duplicate_expected_identities() -> None:
    owners, anchor, _ = fixture()
    early = datetime(2025, 7, 10, tzinfo=UTC)
    report = verify_receipts(owners, anchor, anchor.identifier, AT, outcome_observed_at=early)
    assert report.to_dict()["state"] == "invalid"
    with pytest.raises(ValueError, match="duplicate_receipt_identity"):
        verify_receipts(
            (owners[0], replace(owners[1], receipt_id=owners[0].receipt_id)),
            anchor,
            anchor.identifier,
            AT,
        )


def test_unavailable_crypto_backend_is_fail_closed() -> None:
    owners, anchor, _ = fixture()
    with patch(
        "donghak_stock_vision.storage.prospective_external_receipt._verify_signature",
        side_effect=ImportError,
    ):
        report = verify_receipts(owners, anchor, anchor.identifier, AT).to_dict()
    assert all(
        row["state"] == "unsupported_receipt" and not row["signature_verified"]
        for row in report["receipts"]
    )
