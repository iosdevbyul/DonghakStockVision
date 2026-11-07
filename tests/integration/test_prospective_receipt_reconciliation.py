"""Original captured fixtures plus real ephemeral synthetic signatures, no trust promotion."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.integration.test_prediction_binding import AS_OF, make_original
from tests.integration.test_prospective_evaluation_consumer import sources
from tests.integration.test_prospective_external_receipt import fixture, signed
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prediction_binding import inspect_prediction
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase
from donghak_stock_vision.storage.prospective_external_receipt import ReceiptOwner
from donghak_stock_vision.storage.prospective_receipt_reconciliation import (
    reconcile_receipts,
    verify_reconciliation,
)


def setup(
    root: Path, future: bool = True
) -> tuple[EvaluationSources, tuple[ReceiptOwner, ...], FrozenJSON, Ed25519PrivateKey]:
    s = sources(root, future=future)
    templates, anchor, key = fixture()
    prediction = FrozenJSON.freeze(
        inspect_prediction(s.cases[0].prediction).to_dict()["prediction"]
    )
    receipts = []
    for template, artifact in zip(templates, (s.policy, prediction), strict=True):
        assert template.receipt is not None
        payload = template.receipt.to_dict()["payload"]
        payload["artifact_id"] = payload["artifact_digest"] = artifact.identifier
        receipt = signed(payload, key)
        receipts.append(
            replace(
                template,
                artifact=artifact,
                artifact_pin=artifact.identifier,
                receipt=receipt,
                receipt_pin=receipt.identifier,
            )
        )
    return s, tuple(receipts), anchor, key


def test_reconcile_original_pair_and_restart_without_source_writes(tmp_path: Path) -> None:
    s, receipts, anchor, _ = setup(tmp_path)
    identity = s.cases[0].prediction.prediction_id
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        report = reconcile_receipts(s, identity, receipts, anchor, anchor.identifier, AS_OF)
        d = report.to_dict()
        verify_reconciliation(
            report,
            report.identifier,
            d["original_references_pin"],
            replace(s),
            identity,
            tuple(receipts),
            FrozenJSON(anchor.payload_json),
            anchor.identifier,
            AS_OF,
        )
    assert d["findings"]["original_source_replay_completed"]
    assert d["findings"]["selected_receipt_signatures_verified"]
    assert d["findings"]["selected_artifact_references_match_originals"]
    assert d["findings"]["selected_signed_and_local_chronology_consistent"]
    assert d["state"] == "unresolved" and d["performance_metrics"] is None
    assert d["cohort"]["selected_receipt_cases"] == 1
    assert not d["cohort"]["cohort_wide_receipt_verification_performed"]
    for flag in (
        "genuine_forward_evidence",
        "pit_qualified",
        "prospective_performance_approved",
        "execution_authorized",
    ):
        assert d[flag] is False
    assert not d["findings"]["issuer_independence_verified"]
    assert not d["findings"]["historical_trust_anchor_validity_verified"]
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("role", [0, 1])
def test_substituted_signed_artifact_is_rejected(tmp_path: Path, role: int) -> None:
    s, receipts, anchor, key = setup(tmp_path)
    owner = receipts[role]
    d = owner.artifact.to_dict()
    d["substituted"] = True
    artifact = FrozenJSON.freeze(d)
    assert owner.receipt is not None
    payload = owner.receipt.to_dict()["payload"]
    payload["artifact_id"] = payload["artifact_digest"] = artifact.identifier
    receipt = signed(payload, key)
    changed = replace(
        owner,
        artifact=artifact,
        artifact_pin=artifact.identifier,
        receipt=receipt,
        receipt_pin=receipt.identifier,
    )
    pair = tuple(changed if i == role else r for i, r in enumerate(receipts))
    with pytest.raises(ValueError, match="original_.*_mismatch"):
        reconcile_receipts(
            s, s.cases[0].prediction.prediction_id, pair, anchor, anchor.identifier, AS_OF
        )


@pytest.mark.parametrize("role", [0, 1])
def test_signed_receipt_for_wrong_content_is_invalid(tmp_path: Path, role: int) -> None:
    s, receipts, anchor, key = setup(tmp_path)
    owner = receipts[role]
    assert owner.receipt is not None
    payload = owner.receipt.to_dict()["payload"]
    payload["artifact_digest"] = "f" * 64
    receipt = signed(payload, key)
    pair = tuple(
        replace(r, receipt=receipt, receipt_pin=receipt.identifier) if i == role else r
        for i, r in enumerate(receipts)
    )
    d = reconcile_receipts(
        s, s.cases[0].prediction.prediction_id, pair, anchor, anchor.identifier, AS_OF
    ).to_dict()
    assert d["state"] == "invalid" and not d["findings"]["selected_receipt_signatures_verified"]


@pytest.mark.parametrize(
    "kind",
    [
        "receipt_missing",
        "anchor_missing",
        "revoked",
        "bad_key",
        "receipt_pin",
        "anchor_pin",
        "signed_order",
        "local_order",
    ],
)
def test_unresolved_and_invalid_receipt_states(tmp_path: Path, kind: str) -> None:
    s, receipts, anchor, key = setup(tmp_path)
    pair = receipts
    pin: str | None = anchor.identifier
    trust: FrozenJSON | None = anchor
    if kind == "receipt_missing":
        pair = (replace(receipts[0], receipt=None, receipt_pin=None), receipts[1])
    elif kind == "anchor_missing":
        trust = None
        pin = None
    elif kind in {"revoked", "bad_key"}:
        d = anchor.to_dict()
        if kind == "revoked":
            d["status"] = "revoked"
        else:
            import base64

            d["public_key_base64"] = base64.b64encode(
                Ed25519PrivateKey.generate().public_key().public_bytes_raw()
            ).decode()
        trust = FrozenJSON.freeze(d)
        pin = trust.identifier
    elif kind == "receipt_pin":
        pair = (replace(receipts[0], receipt_pin="0" * 64), receipts[1])
    elif kind == "anchor_pin":
        pin = "0" * 64
    else:
        owner = receipts[0]
        assert owner.receipt is not None
        payload = owner.receipt.to_dict()["payload"]
        payload["issued_at"] = (
            "2025-07-12T00:00:00+00:00" if kind == "signed_order" else "2025-07-10T00:00:04+00:00"
        )
        receipt = signed(payload, key)
        pair = (replace(owner, receipt=receipt, receipt_pin=receipt.identifier), receipts[1])
    d = reconcile_receipts(
        s, s.cases[0].prediction.prediction_id, pair, trust, pin, AS_OF
    ).to_dict()
    assert d["state"] == (
        "unresolved" if kind in {"receipt_missing", "anchor_missing"} else "invalid"
    )
    assert not d["genuine_forward_evidence"]


def test_cohort_context_not_claimed_as_multi_case_receipt_verification(tmp_path: Path) -> None:
    s, receipts, anchor, key = setup(tmp_path)
    root = tmp_path / "second"
    root.mkdir()
    second = make_original(root, (101, 91, 81, 121))
    cases = (*s.cases, EvidenceCase(second))
    p = s.policy.to_dict()
    p["expected_prediction_ids"] = sorted(c.prediction.prediction_id for c in cases)
    policy = FrozenJSON.freeze(p)
    s = replace(s, policy=policy, policy_pin=policy.identifier, cases=cases)
    owner = receipts[0]
    assert owner.receipt is not None
    payload = owner.receipt.to_dict()["payload"]
    payload["artifact_id"] = payload["artifact_digest"] = policy.identifier
    receipt = signed(payload, key)
    pair = (
        replace(
            owner,
            artifact=policy,
            artifact_pin=policy.identifier,
            receipt=receipt,
            receipt_pin=receipt.identifier,
        ),
        receipts[1],
    )
    d = reconcile_receipts(
        s, s.cases[0].prediction.prediction_id, pair, anchor, anchor.identifier, AS_OF
    ).to_dict()
    assert d["cohort"]["declared_predictions"] == 2
    assert d["cohort"]["other_prediction_receipt_cases_not_examined"] == 1
    assert d["custody_report"]["counts"]["future_unavailable"] == 1
    assert d["findings"]["selected_receipt_signatures_verified"]


@pytest.mark.parametrize("pending", [True, False])
def test_pending_unavailable_observations_preserved(tmp_path: Path, pending: bool) -> None:
    s, receipts, anchor, _ = setup(tmp_path, future=False)
    # At recording time the prediction receipt is still future-dated and remains unverified.
    d = reconcile_receipts(
        s,
        s.cases[0].prediction.prediction_id,
        receipts,
        anchor,
        anchor.identifier,
        RECORDED if pending else AS_OF,
    ).to_dict()
    assert d["custody_report"]["counts"]["future_pending" if pending else "future_unavailable"] == 1
    assert d["receipt_verification"]["outcome_time_context"] is None
    assert d["state"] in {"unresolved", "invalid"}


def test_duplicate_batch_membership_and_wrong_pins(tmp_path: Path) -> None:
    s, receipts, anchor, _ = setup(tmp_path)
    identity = s.cases[0].prediction.prediction_id
    with patch(
        "donghak_stock_vision.storage.prospective_receipt_reconciliation.audit_custody",
        side_effect=AssertionError("no replay"),
    ):
        with pytest.raises(ValueError, match="single_case_only"):
            reconcile_receipts(
                s, identity, (*receipts, receipts[1]), anchor, anchor.identifier, AS_OF
            )
        with pytest.raises(ValueError, match="membership_mismatch"):
            reconcile_receipts(s, "0" * 64, receipts, anchor, anchor.identifier, AS_OF)
        with pytest.raises(ValueError, match="cohort_budget"):
            reconcile_receipts(
                replace(s, cases=s.cases * 33), identity, receipts, anchor, anchor.identifier, AS_OF
            )
    with pytest.raises(ValueError, match="duplicate_receipt_identity"):
        reconcile_receipts(
            s,
            identity,
            (receipts[0], replace(receipts[1], receipt_id=receipts[0].receipt_id)),
            anchor,
            anchor.identifier,
            AS_OF,
        )
    with pytest.raises(ValueError):
        reconcile_receipts(
            replace(s, cohort_pin="0" * 64), identity, receipts, anchor, anchor.identifier, AS_OF
        )


def test_semantic_tampering_retained_pins_and_output_bounds(tmp_path: Path) -> None:
    s, receipts, anchor, _ = setup(tmp_path)
    identity = s.cases[0].prediction.prediction_id
    report = reconcile_receipts(s, identity, receipts, anchor, anchor.identifier, AS_OF)
    d = report.to_dict()
    d["findings"]["issuer_independence_verified"] = True
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_reconciliation(
            forged,
            forged.identifier,
            d["original_references_pin"],
            s,
            identity,
            receipts,
            anchor,
            anchor.identifier,
            AS_OF,
        )
    with pytest.raises(ValueError, match="original_references_pin_mismatch"):
        verify_reconciliation(
            report,
            report.identifier,
            "0" * 64,
            s,
            identity,
            receipts,
            anchor,
            anchor.identifier,
            AS_OF,
        )
    large = FrozenJSON.freeze({"large": "x" * (256 * 1024)})
    with pytest.raises(ValueError, match="output_budget"):
        verify_reconciliation(
            large,
            large.identifier,
            "0" * 64,
            s,
            identity,
            receipts,
            anchor,
            anchor.identifier,
            AS_OF,
        )


@pytest.mark.parametrize("kind", ["receipt", "anchor"])
def test_receipt_input_bounds_remain_enforced(tmp_path: Path, kind: str) -> None:
    s, receipts, anchor, _ = setup(tmp_path)
    large = FrozenJSON.freeze({"large": "x" * 8192})
    if kind == "receipt":
        receipts = (replace(receipts[0], receipt=large, receipt_pin=large.identifier), receipts[1])
    else:
        anchor = large
    with pytest.raises(ValueError, match="budget"):
        reconcile_receipts(
            s, s.cases[0].prediction.prediction_id, receipts, anchor, anchor.identifier, AS_OF
        )


def test_invalid_future_does_not_become_verified_source(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.forward_capture import DB

    s, receipts, anchor, _ = setup(tmp_path)
    f = s.cases[0].future
    assert f is not None
    with (f.capture_root / DB).open("ab") as stream:
        stream.write(b"modified")
    d = reconcile_receipts(
        s, s.cases[0].prediction.prediction_id, receipts, anchor, anchor.identifier, AS_OF
    ).to_dict()
    assert d["custody_report"]["counts"]["future_invalid"] == 1
    assert not d["findings"]["original_source_replay_completed"]
    assert d["findings"]["original_prediction_policy_replay_completed"]
    assert d["findings"]["selected_receipt_signatures_verified"]
    assert not d["genuine_forward_evidence"]
