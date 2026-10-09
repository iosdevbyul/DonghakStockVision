"""Offline synthetic contracts and separate preserved-evidence replay checks."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.security_lifecycle import (
    LifecycleEvidence,
    LifecycleReason,
    reconcile_lifecycle,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.backtest.verify_security_lifecycle import (
    official_identity,
    source,
    verify,
)

SOURCE = FrozenJSON.freeze(
    dict(url="https://kind.krx.co.kr/fixture", raw_sha256="a" * 64, document_reference="fixture")
)


def evidence(**changes: Any) -> LifecycleEvidence:
    values: dict[str, Any] = dict(
        ticker="005930",
        security_identity="FIXTURE_ISIN",
        reason=LifecycleReason.DELISTED,
        start=date(2025, 1, 3),
        end=date(2025, 1, 10),
        effective_date=date(2025, 1, 3),
        source=SOURCE,
        identity_source=SOURCE,
        exact_identity_verified=True,
        effective_date_verified=True,
        review_reference="fixture_review",
    )
    values.update(changes)
    return LifecycleEvidence(**values)


def gaps(*days: str) -> FrozenJSON:
    return FrozenJSON.freeze({"005930": list(days)})


@pytest.mark.parametrize(
    "reason", [LifecycleReason.NOT_YET_LISTED, LifecycleReason.RELISTING_TRANSITION]
)
def test_listing_explains_only_before_effective_boundary(reason: LifecycleReason) -> None:
    item = evidence(reason=reason, start=date(2025, 1, 1), end=date(2025, 1, 2))
    result = reconcile_lifecycle(gaps("2025-01-02", "2025-01-03"), (item,)).to_dict()
    assert result["explained"] == 1
    assert result["sessions"][1]["reason"] == "UNEXPLAINED_DATA_GAP"
    with pytest.raises(ContractError, match="listing_boundary"):
        replace(item, end=date(2025, 1, 3))


def test_delisting_uses_effective_date_not_filing() -> None:
    result = reconcile_lifecycle(gaps("2025-01-02", "2025-01-03"), (evidence(),)).to_dict()
    assert [r["verified"] for r in result["sessions"]] == [False, True]
    with pytest.raises(ContractError, match="delisting_boundary"):
        evidence(start=date(2025, 1, 2))
    assert (
        reconcile_lifecycle(
            gaps("2025-01-03"), (evidence(effective_date_verified=False),)
        ).to_dict()["explained"]
        == 0
    )


def test_bounded_suspension_only() -> None:
    item = evidence(reason=LifecycleReason.TRADING_SUSPENDED, end=date(2025, 1, 6))
    result = reconcile_lifecycle(
        gaps("2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"), (item,)
    ).to_dict()
    assert [r["verified"] for r in result["sessions"]] == [False, True, True, False]
    with pytest.raises((TypeError, ContractError)):
        evidence(end=None)


def test_ambiguous_identity_and_current_only_mapping_block() -> None:
    assert (
        reconcile_lifecycle(
            gaps("2025-01-03"), (evidence(exact_identity_verified=False),)
        ).to_dict()["explained"]
        == 0
    )
    a = evidence()
    b = replace(a, security_identity="OTHER_ISIN")
    result = reconcile_lifecycle(gaps("2025-01-03"), (a, b)).to_dict()
    assert result["sessions"][0]["blocker"] == "conflicting_lifecycle_intervals"
    assert result["status"] == "BLOCKED"


def test_exact_duplicate_evidence_is_idempotent() -> None:
    item = evidence()
    assert reconcile_lifecycle(gaps("2025-01-03"), (item,)) == reconcile_lifecycle(
        gaps("2025-01-03"), (item, item)
    )


def test_no_evidence_never_proves_suspension() -> None:
    result = reconcile_lifecycle(gaps("2025-01-03"), ()).to_dict()
    assert result["sessions"][0]["reason"] == "UNEXPLAINED_DATA_GAP"
    assert result["status"] == "BLOCKED"
    assert result["bar_absence_used_as_evidence"] is False
    assert result["price_adjustment_performed"] is False


def test_one_unexplained_session_blocks_readiness() -> None:
    assert reconcile_lifecycle(gaps("2025-01-03"), (evidence(),)).to_dict()["status"] == "VERIFIED"
    assert (
        reconcile_lifecycle(gaps("2025-01-03", "2025-01-13"), (evidence(),)).to_dict()["status"]
        == "BLOCKED"
    )
    assert reconcile_lifecycle(FrozenJSON.freeze({}), ()).to_dict()["status"] == "BLOCKED"


@pytest.mark.parametrize("days", [("2025-01-03", "2025-01-03"), ("2025-01-06", "2025-01-03")])
def test_invalid_gap_inventory_rejected(days: tuple[str, ...]) -> None:
    with pytest.raises(ContractError, match="session_order"):
        reconcile_lifecycle(gaps(*days), ())


def test_deterministic_identity_changes_with_semantics() -> None:
    a = evidence()
    assert a.fingerprint == evidence().fingerprint
    b = replace(a, end=date(2025, 1, 9))
    assert a.fingerprint != b.fingerprint
    metadata = SOURCE.to_dict() | {"timestamp": "2099-01-01", "path": "/different/location"}
    assert replace(a, source=FrozenJSON.freeze(metadata)).fingerprint == a.fingerprint


def test_secret_metadata_rejected() -> None:
    with pytest.raises(ContractError, match="secret"):
        evidence(source=FrozenJSON.freeze(SOURCE.to_dict() | {"DART_API_KEY": "fixture_secret"}))


def test_exact_official_code_never_guessed() -> None:
    text = "\ud45c\uc900\ucf54\ub4dc KR7005930003 \uc885\ubaa9\ucf54\ub4dc 005930"
    assert official_identity(text) == ("005930", "KR7005930003")
    with pytest.raises(ContractError):
        official_identity(text.replace("005930", "00593"))
    with pytest.raises(ContractError):
        official_identity(text + " " + text)


def test_unofficial_source_and_unknown_reason_rejected() -> None:
    with pytest.raises(ContractError, match="official"):
        evidence(source=FrozenJSON.freeze(SOURCE.to_dict() | {"url": "https://example.com"}))
    with pytest.raises(ContractError, match="unknown_lifecycle"):
        evidence(reason="inferred_from_missing_bar")


def test_offline_replay_has_no_private_data_or_network_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import sqlite3

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Network and database access are forbidden during evidence replay")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    root = Path(__file__).resolve().parents[2]
    base = root / "docs/evidence/baseline-security-lifecycle"
    result = verify(root, base)
    assert result == verify(root, base)
    summary = result["missing-session-reconciliation"]["artifact"]
    assert summary["total"] == summary["explained"] == 8118
    assert summary["unexplained"] == 0
    assert len({r["ticker"] for r in summary["sessions"]}) == 43
    assert (
        result["integrated-security-evidence"]["artifact"]["ready_for_baseline_run_input_freeze"]
        is False
    )


def test_source_tamper_detected(tmp_path: Path) -> None:
    import gzip
    import json

    directory = tmp_path / "sources"
    directory.mkdir()
    (directory / "fixture.html.gz").write_bytes(gzip.compress(b"altered"))
    (directory / "fixture.json").write_text(json.dumps(dict(raw_sha256="a" * 64)))
    with pytest.raises(ContractError, match="source_hash"):
        source(tmp_path, "fixture")
