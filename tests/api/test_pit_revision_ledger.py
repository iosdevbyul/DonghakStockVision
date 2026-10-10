"""Ledger chain integrity alone is never proof of historical data availability."""

import hashlib
import json

from donghak_stock_vision.api.pit_revision_ledger import (
    RevisionLedgerAssessment,
    inspect_revision_ledger,
)
from donghak_stock_vision.data.learning import FEATURE_VERSION, FEATURES


def _documents() -> tuple[bytes, dict[str, object]]:
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
        "snapshotId": "snapshot-1",
        "inputHash": "a" * 64,
        "qualityFlags": [],
    }
    ledger: dict[str, object] = {
        "schemaVersion": "pit_source_revision_ledger_v1",
        "snapshotId": "snapshot-1",
        "ticker": "005930",
        "revisions": [
            {
                "sequence": 1,
                "observedAt": "2026-10-09T09:00:00+09:00",
                "sourceSha256": "b" * 64,
                "previousSha256": None,
            },
            {
                "sequence": 2,
                "observedAt": "2026-10-10T09:00:00+09:00",
                "sourceSha256": "c" * 64,
                "previousSha256": "b" * 64,
            },
        ],
    }
    return json.dumps(candidate).encode(), ledger


def _inspect(candidate: bytes, ledger: dict[str, object]) -> RevisionLedgerAssessment:
    document = json.dumps(ledger).encode()
    return inspect_revision_ledger(
        candidate,
        document,
        independently_trusted_ledger_sha256=hashlib.sha256(document).hexdigest(),
    )


def test_consistent_chain_never_authorizes_inference() -> None:
    candidate, ledger = _documents()
    result = _inspect(candidate, ledger)
    assert result.chain_consistent
    assert not result.historical_availability_verified
    assert not result.inference_authorized


def test_modified_predecessor_is_rejected() -> None:
    candidate, ledger = _documents()
    revisions = ledger["revisions"]
    assert isinstance(revisions, list)
    revisions[1]["previousSha256"] = "d" * 64
    assert "revision_link_invalid" in _inspect(candidate, ledger).blockers


def test_future_revision_is_rejected() -> None:
    candidate, ledger = _documents()
    revisions = ledger["revisions"]
    assert isinstance(revisions, list)
    revisions[1]["observedAt"] = "2026-10-11T09:00:00+09:00"
    assert "revision_observed_after_cutoff" in _inspect(candidate, ledger).blockers
