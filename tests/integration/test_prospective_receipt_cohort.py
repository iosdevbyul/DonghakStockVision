"""Actual ephemeral signatures, complete original cohorts and no trust promotion."""

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.integration.test_prediction_binding import AS_OF, future_capture, make_original
from tests.integration.test_prospective_evaluation_consumer import sources
from tests.integration.test_prospective_external_receipt import fixture, signed
from tests.integration.test_prospective_prediction import RECORDED

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.storage.prediction_binding import inspect_prediction
from donghak_stock_vision.storage.prospective_evaluation_consumer import EvaluationSources
from donghak_stock_vision.storage.prospective_evidence_audit import EvidenceCase
from donghak_stock_vision.storage.prospective_external_receipt import ReceiptOwner
from donghak_stock_vision.storage.prospective_receipt_cohort import reconcile_cohort, verify_cohort


def cohort(
    root: Path, n: int = 2, future: bool = False
) -> tuple[
    EvaluationSources,
    ReceiptOwner,
    dict[str, ReceiptOwner],
    FrozenJSON,
    FrozenJSON,
    Ed25519PrivateKey,
]:
    s = sources(root, future=future)
    cases = list(s.cases)
    for i in range(1, n):
        directory = root / f"case-{i}"
        directory.mkdir()
        original = make_original(directory, (100 + i, 90 + i, 80 + i, 120 + i))
        f = future_capture(original, directory / "future", close=130 + i) if future else None
        cases.append(EvidenceCase(original, f))
    p = s.policy.to_dict()
    p["expected_prediction_ids"] = sorted(c.prediction.prediction_id for c in cases)
    policy = FrozenJSON.freeze(p)
    s = replace(s, policy=policy, policy_pin=policy.identifier, cases=tuple(cases))
    templates, anchor, key = fixture()
    template = templates[0]
    assert template.receipt is not None
    payload = template.receipt.to_dict()["payload"]
    payload["artifact_id"] = payload["artifact_digest"] = policy.identifier
    body = signed(payload, key)
    shared = replace(
        template,
        artifact=policy,
        artifact_pin=policy.identifier,
        receipt=body,
        receipt_pin=body.identifier,
    )
    owners = {}
    expected = {}
    template = templates[1]
    assert template.receipt is not None
    for i, case in enumerate(cases):
        identity = case.prediction.prediction_id
        artifact = FrozenJSON.freeze(inspect_prediction(case.prediction).to_dict()["prediction"])
        payload = template.receipt.to_dict()["payload"]
        payload["artifact_id"] = payload["artifact_digest"] = artifact.identifier
        payload["receipt_id"] = f"{i + 1000:064x}"
        body = signed(payload, key)
        owners[identity] = replace(
            template,
            artifact=artifact,
            artifact_pin=artifact.identifier,
            receipt_id=payload["receipt_id"],
            receipt=body,
            receipt_pin=body.identifier,
        )
        expected[identity] = payload["receipt_id"]
    expectations = FrozenJSON.freeze(
        {
            "version": "prospective_receipt_cohort_expectations_v1",
            "prediction_receipt_ids": expected,
        }
    )
    return s, shared, owners, expectations, anchor, key


def run(
    data: tuple[
        EvaluationSources,
        ReceiptOwner,
        dict[str, ReceiptOwner],
        FrozenJSON,
        FrozenJSON,
        Ed25519PrivateKey,
    ],
    *,
    owners: dict[str, ReceiptOwner] | None = None,
) -> FrozenJSON:
    s, shared, original, e, anchor, _ = data
    return reconcile_cohort(
        s,
        shared,
        original if owners is None else owners,
        e,
        e.identifier,
        anchor,
        anchor.identifier,
        AS_OF,
    )


@pytest.mark.parametrize("n", [1, 3])
def test_complete_valid_cohort_is_still_unresolved(tmp_path: Path, n: int) -> None:
    data = cohort(tmp_path, n, future=True)
    s, shared, owners, e, anchor, _ = data
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch("httpx.Client.send", side_effect=AssertionError("no network")):
        report = run(data)
        d = report.to_dict()
        verify_cohort(
            report,
            report.identifier,
            d["original_references_pin"],
            s,
            shared,
            owners,
            e,
            e.identifier,
            anchor,
            anchor.identifier,
            AS_OF,
        )
    assert d["counts"]["policy_prediction_pairs_verified"] == n
    assert d["counts"]["not_cryptographically_verified"] == 0
    assert d["cryptographic_coverage_diagnostic"] == {
        "numerator": n,
        "denominator": n,
        "all_declared_pairs_have_valid_signatures": True,
    }
    assert d["state"] == "unresolved" and d["performance_metrics"] is None
    assert all(
        d[k] is False
        for k in (
            "genuine_forward_evidence",
            "pit_qualified",
            "prospective_performance_approved",
            "execution_authorized",
            "issuer_independence_verified",
            "independently_authenticated_chronology",
        )
    )
    assert all(
        next(r for r in c["receipt_verification"]["receipts"] if r["role"] == "policy")[
            "receipt_pin"
        ]
        == shared.receipt_pin
        for c in d["cases"]
    )
    assert [c["prediction_id"] for c in d["cases"]] == sorted(owners)
    assert run(data) == FrozenJSON(report.payload_json)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("all_missing", [False, True])
def test_absent_bodies_keep_complete_denominator(tmp_path: Path, all_missing: bool) -> None:
    data = cohort(tmp_path)
    owners = {} if all_missing else dict(data[2])
    if owners:
        owners.pop(next(iter(owners)))
    d = run(data, owners=owners).to_dict()
    missing = 2 if all_missing else 1
    assert d["counts"]["missing_receipt_references"] == missing
    assert d["counts"]["total_declared_predictions"] == 2
    assert d["counts"]["not_cryptographically_verified"] == missing
    assert d["cryptographic_coverage_diagnostic"]["denominator"] == 2
    assert d["counts"]["local_future_unavailable"] == 2
    assert all(
        r["expected_receipt_id"] == data[3].to_dict()["prediction_receipt_ids"][r["prediction_id"]]
        for r in d["cases"]
    )


@pytest.mark.parametrize(
    "kind", ["signature", "revoked_receipt", "revoked_key", "chronology", "anchor_pin"]
)
def test_per_case_failure_and_shared_trust_failures(tmp_path: Path, kind: str) -> None:
    data = cohort(tmp_path)
    s, shared, owners, e, anchor, key = data
    identity = next(iter(owners))
    owner = owners[identity]
    assert owner.receipt is not None
    pin = anchor.identifier
    if kind in {"signature", "chronology"}:
        body = owner.receipt.to_dict()
        if kind == "signature":
            body["signature_base64"] = "A" * 86 + "=="
        else:
            body["payload"]["issued_at"] = "2025-07-09T00:00:00+00:00"
            body = signed(body["payload"], key).to_dict()
        receipt = FrozenJSON.freeze(body)
        owners[identity] = replace(owner, receipt=receipt, receipt_pin=receipt.identifier)
    elif kind in {"revoked_receipt", "revoked_key"}:
        a = anchor.to_dict()
        if kind == "revoked_key":
            a["status"] = "revoked"
        else:
            a["revoked_receipt_ids"] = [owner.receipt_id]
        anchor = FrozenJSON.freeze(a)
        pin = anchor.identifier
    else:
        pin = "0" * 64
    d = reconcile_cohort(s, shared, owners, e, e.identifier, anchor, pin, AS_OF).to_dict()
    expected_failures = 2 if kind in {"revoked_key", "anchor_pin"} else 1
    assert d["counts"]["receipt_verification_failed"] == expected_failures
    assert d["counts"]["total_declared_predictions"] == 2
    assert d["state"] == "invalid" and not d["genuine_forward_evidence"]


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_receipt",
        "duplicate_prediction",
        "extra",
        "wrong_role",
        "wrong_policy",
        "substituted",
        "expectation_pin",
        "unknown_version",
        "missing_expected",
        "too_many",
    ],
)
def test_invalid_envelope_or_original_identity_is_whole_request_failure(
    tmp_path: Path, kind: str
) -> None:
    s, shared, owners, e, anchor, _ = cohort(tmp_path)
    ids = list(owners)
    if kind == "duplicate_receipt":
        d = e.to_dict()
        d["prediction_receipt_ids"][ids[1]] = d["prediction_receipt_ids"][ids[0]]
        e = FrozenJSON.freeze(d)
    elif kind == "duplicate_prediction":
        s = replace(s, cases=s.cases * 2)
    elif kind == "extra":
        owners["0" * 64] = owners[ids[0]]
    elif kind == "wrong_role":
        owners[ids[0]] = replace(owners[ids[0]], role="policy")
    elif kind == "wrong_policy":
        shared = replace(shared, artifact=owners[ids[0]].artifact)
    elif kind == "substituted":
        a = owners[ids[0]].artifact.to_dict()
        a["substituted"] = True
        artifact = FrozenJSON.freeze(a)
        owners[ids[0]] = replace(
            owners[ids[0]], artifact=artifact, artifact_pin=artifact.identifier
        )
    elif kind in {"unknown_version", "missing_expected"}:
        d = e.to_dict()
        if kind == "unknown_version":
            d["version"] = "unknown"
        else:
            d["prediction_receipt_ids"].pop(ids[0])
        e = FrozenJSON.freeze(d)
    elif kind == "too_many":
        s = replace(s, cases=s.cases * 17)
    with pytest.raises(ValueError):
        reconcile_cohort(
            s,
            shared,
            owners,
            e,
            "0" * 64 if kind == "expectation_pin" else e.identifier,
            anchor,
            anchor.identifier,
            AS_OF,
        )


def test_verified_and_unavailable_and_pending_diagnostics(tmp_path: Path) -> None:
    data = cohort(tmp_path, future=True)
    s, shared, owners, e, anchor, key = data
    cases = (s.cases[0], EvidenceCase(s.cases[1].prediction))
    d = reconcile_cohort(
        replace(s, cases=cases), shared, owners, e, e.identifier, anchor, anchor.identifier, AS_OF
    ).to_dict()
    assert d["counts"]["local_future_unavailable"] == 1
    assert d["counts"]["policy_prediction_pairs_verified"] == 2
    # The original consumer requires one exact common gate: pending and mature
    # cannot coexist at one as_of. Do not fabricate a mixed-horizon cohort.
    for identity, owner in owners.items():
        assert owner.receipt is not None
        payload = owner.receipt.to_dict()["payload"]
        payload["issued_at"] = RECORDED.isoformat()
        receipt = signed(payload, key)
        owners[identity] = replace(owner, receipt=receipt, receipt_pin=receipt.identifier)
    pending = reconcile_cohort(
        s, shared, owners, e, e.identifier, anchor, anchor.identifier, RECORDED
    ).to_dict()
    assert pending["counts"]["local_future_pending"] == 2


def test_reordered_input_stable_and_forged_output_rejected(tmp_path: Path) -> None:
    data = cohort(tmp_path)
    s, shared, owners, e, anchor, _ = data
    report = run(data)
    assert (
        reconcile_cohort(
            replace(s, cases=tuple(reversed(s.cases))),
            shared,
            dict(reversed(list(owners.items()))),
            e,
            e.identifier,
            anchor,
            anchor.identifier,
            AS_OF,
        )
        == report
    )
    for change in ("counts", "order", "flags"):
        d = report.to_dict()
        if change == "counts":
            d["counts"]["total_declared_predictions"] = 1
        elif change == "order":
            d["cases"].reverse()
        else:
            d["genuine_forward_evidence"] = True
        forged = FrozenJSON.freeze(d)
        with pytest.raises(ValueError, match="semantic_mismatch"):
            verify_cohort(
                forged,
                forged.identifier,
                d["original_references_pin"],
                s,
                shared,
                owners,
                e,
                e.identifier,
                anchor,
                anchor.identifier,
                AS_OF,
            )


def test_input_and_output_budgets_before_io(tmp_path: Path) -> None:
    s, shared, owners, e, anchor, _ = cohort(tmp_path, 1)
    huge = FrozenJSON.freeze({"large": "x" * (256 * 1024)})
    with patch("sqlite3.connect", side_effect=AssertionError("no source IO")):
        with pytest.raises(ValueError, match="expectation_budget"):
            reconcile_cohort(
                s, shared, owners, huge, huge.identifier, anchor, anchor.identifier, AS_OF
            )
        with pytest.raises(ValueError, match="output_budget"):
            verify_cohort(
                huge,
                huge.identifier,
                "0" * 64,
                s,
                shared,
                owners,
                e,
                e.identifier,
                anchor,
                anchor.identifier,
                AS_OF,
            )


def test_maximum_32_case_input_real_replay(tmp_path: Path) -> None:
    data = cohort(tmp_path, 32)
    started = perf_counter()
    report = run(data)
    elapsed = perf_counter() - started
    d = report.to_dict()
    assert d["counts"]["total_declared_predictions"] == 32
    assert d["counts"]["policy_prediction_pairs_verified"] == 32
    assert d["work_bounds"]["maximum_ed25519_checks"] == 64
    assert not d["genuine_forward_evidence"]
    print(f"32-case real replay: seconds={elapsed:.6f} bytes={len(report.payload_json.encode())}")


def test_missing_anchor_and_missing_shared_policy_are_not_approved(tmp_path: Path) -> None:
    s, shared, owners, e, anchor, _ = cohort(tmp_path)
    missing_anchor = reconcile_cohort(
        s, shared, owners, e, e.identifier, None, None, AS_OF
    ).to_dict()
    assert missing_anchor["state"] == "unresolved"
    assert missing_anchor["counts"]["not_cryptographically_verified"] == 2
    assert missing_anchor["counts"]["receipt_verification_failed"] == 2
    missing_policy = reconcile_cohort(
        s,
        replace(shared, receipt=None, receipt_pin=None),
        owners,
        e,
        e.identifier,
        anchor,
        anchor.identifier,
        AS_OF,
    ).to_dict()
    assert missing_policy["counts"]["prediction_signatures_verified"] == 2
    assert missing_policy["counts"]["policy_prediction_pairs_verified"] == 0
    assert not missing_policy["cryptographic_coverage_diagnostic"][
        "all_declared_pairs_have_valid_signatures"
    ]


def test_original_integrity_and_request_bounds_fail_whole_request(tmp_path: Path) -> None:
    s, shared, owners, e, anchor, _ = cohort(tmp_path, 1)
    case = s.cases[0]
    large = FrozenJSON.freeze({"body": "x" * (256 * 1024)})
    invalid_case = EvidenceCase(replace(case.prediction, request=large))
    with patch("sqlite3.connect", side_effect=AssertionError("no source reads")):
        with pytest.raises(ValueError, match="request_byte_budget"):
            reconcile_cohort(
                replace(s, cases=(invalid_case,)),
                shared,
                {},
                e,
                e.identifier,
                anchor,
                anchor.identifier,
                AS_OF,
            )
    invalid_case = EvidenceCase(replace(case.prediction, journal_head="0" * 64))
    with pytest.raises(ValueError):
        reconcile_cohort(
            replace(s, cases=(invalid_case,)),
            shared,
            owners,
            e,
            e.identifier,
            anchor,
            anchor.identifier,
            AS_OF,
        )


def test_invalid_future_remains_local_failure_not_receipt_approval(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.forward_capture import DB

    data = cohort(tmp_path, future=True)
    future = data[0].cases[0].future
    assert future is not None
    with (future.capture_root / DB).open("ab") as stream:
        stream.write(b"changed")
    d = run(data).to_dict()
    assert d["counts"]["local_future_invalid"] == 1
    assert d["counts"]["policy_prediction_pairs_verified"] == 2
    assert not d["independently_authenticated_chronology"]
