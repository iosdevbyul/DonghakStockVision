"""Public key registry must be pinned independently from review receipts."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from donghak_stock_vision.api.reviewer_key_registry import (
    ReviewerRegistryRejected,
    load_pinned_reviewer_keys,
)


def _registry(tmp_path: Path, *, status: str = "active") -> tuple[Path, str]:
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "trusted_reviewer_keys_v1",
                "keys": [
                    {
                        "keyId": "reviewer-key-v1",
                        "reviewer": "independent-reviewer",
                        "publicKeyBase64": base64.b64encode(b"x" * 32).decode("ascii"),
                        "status": status,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_loads_active_and_revoked_public_keys(tmp_path: Path) -> None:
    path, pin = _registry(tmp_path)
    keys = load_pinned_reviewer_keys(path, trusted_sha256=pin)
    assert len(keys) == 1
    assert keys[0].active
    assert keys[0].reviewer == "independent-reviewer"
    path, pin = _registry(tmp_path, status="revoked")
    assert not load_pinned_reviewer_keys(path, trusted_sha256=pin)[0].active


def test_modified_registry_fails_closed(tmp_path: Path) -> None:
    path, pin = _registry(tmp_path)
    path.write_text(path.read_text() + " ", encoding="utf-8")
    with pytest.raises(ReviewerRegistryRejected):
        load_pinned_reviewer_keys(path, trusted_sha256=pin)


def test_invalid_or_unpinned_registry_fails_closed(tmp_path: Path) -> None:
    path, pin = _registry(tmp_path)
    with pytest.raises(ReviewerRegistryRejected):
        load_pinned_reviewer_keys(path, trusted_sha256="untrusted")
    payload = json.loads(path.read_text())
    payload["keys"].append(payload["keys"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")
    modified_pin = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ReviewerRegistryRejected):
        load_pinned_reviewer_keys(path, trusted_sha256=modified_pin)
    assert pin != modified_pin
