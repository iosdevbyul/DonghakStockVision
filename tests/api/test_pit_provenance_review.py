"""PIT provenance diagnostics cannot override source quality or grant inference."""

import hashlib
import json

from donghak_stock_vision.api.pit_provenance_review import inspect_pit_provenance
from donghak_stock_vision.data.learning import FEATURE_VERSION, FEATURES


def _inputs(
    *,
    flags: tuple[str, ...] = (),
    revision: str = "verified",
) -> tuple[bytes, bytes]:
    candidate = {
        "schemaVersion": "pit_inference_input_v1",
        "ticker": "005930",
        "mode": "point_in_time",
        "dataOrigin": "real",
        "featureVersion": FEATURE_VERSION,
        "featureNames": FEATURES,
        "featureValues": [0.1] * len(FEATURES),
        "lastTradingDate": "2026-10-08",
        "asOf": "2026-10-10T12:00:00+09:00",
        "dataCutoff": "2026-10-09T00:00:00+09:00",
        "sourceCutoff": "2026-10-10T12:00:00+09:00",
        "snapshotId": "snapshot-digest",
        "inputHash": "a" * 64,
        "qualityFlags": flags,
    }
    evidence = {
        "schemaVersion": "pit_source_evidence_v1",
        "snapshotId": "snapshot-digest",
        "inputHash": "a" * 64,
        "ticker": "005930",
        "lastTradingDate": "2026-10-08",
        "featureVersion": FEATURE_VERSION,
        "snapshotCutoff": "2026-10-10T12:00:00+09:00",
        "revisionHistoryStatus": revision,
        "corporateActionStatus": "verified",
        "calendarStatus": "verified",
        "qualityFlags": flags,
    }
    return json.dumps(candidate).encode(), json.dumps(evidence).encode()


def test_all_declared_consistent_evidence_still_does_not_authorize() -> None:
    candidate, evidence = _inputs()
    report = inspect_pit_provenance(
        candidate,
        evidence,
        trusted_evidence_sha256=hashlib.sha256(evidence).hexdigest(),
    )
    assert report.evidence_bound
    assert not report.provenance_authenticated
    assert not report.inference_authorized
    assert "independent_source_issuer_signature_missing" in report.blockers


def test_unverified_revisions_and_flags_remain_blocked() -> None:
    candidate, evidence = _inputs(
        flags=("revision_history_unavailable",),
        revision="unavailable",
    )
    result = inspect_pit_provenance(
        candidate,
        evidence,
        trusted_evidence_sha256=hashlib.sha256(evidence).hexdigest(),
    )
    assert not result.evidence_bound
    assert "pit_revision_history_not_verified" in result.blockers
    assert "pit_snapshot_quality_flags_unresolved" in result.blockers


def test_tampering_and_mismatched_identity_fail_closed() -> None:
    candidate, evidence = _inputs()
    pin = hashlib.sha256(evidence).hexdigest()
    result = inspect_pit_provenance(candidate, evidence + b" ", trusted_evidence_sha256=pin)
    assert "independent_evidence_pin_mismatch" in result.blockers
    changed = json.loads(evidence)
    changed["ticker"] = "000660"
    changed_bytes = json.dumps(changed).encode()
    result = inspect_pit_provenance(
        candidate,
        changed_bytes,
        trusted_evidence_sha256=hashlib.sha256(changed_bytes).hexdigest(),
    )
    assert "pit_evidence_candidate_mismatch" in result.blockers


def test_unverified_corporate_actions_block_even_with_matching_evidence_hash() -> None:
    candidate, evidence = _inputs()
    for status in ("unverified", "unknown"):
        changed = json.loads(evidence)
        changed["corporateActionStatus"] = status
        changed_bytes = json.dumps(changed).encode()
        report = inspect_pit_provenance(
            candidate,
            changed_bytes,
            trusted_evidence_sha256=hashlib.sha256(changed_bytes).hexdigest(),
        )
        assert "pit_corporate_actions_not_verified" in report.blockers
        assert not report.evidence_bound
        assert not report.provenance_authenticated
        assert not report.inference_authorized
