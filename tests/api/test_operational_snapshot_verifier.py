"""Offline verifier tests use synthetic snapshots and never operator-owned data."""

from pathlib import Path

import pytest

from donghak_stock_vision.operational_verification_cli import main, verify
from tests.api.test_operational_market_read import make_snapshot


def test_verify_catalog_summary_and_bars(tmp_path: Path) -> None:
    snapshot = tmp_path / "synthetic.snapshot.sqlite3"
    sha256 = make_snapshot(snapshot)
    result = verify(snapshot, sha256, expected_tickers=1)
    assert result["verified"] is True
    assert result["evidenceStatus"] == "unreviewed"
    assert result["tickerCount"] == 1
    assert result["observationCount"] == 1
    assert result["tradingDayCount"] == 1
    assert result["ohlcvBarsInspected"] == 1


def test_expected_ticker_count_mismatch_fails(tmp_path: Path) -> None:
    snapshot = tmp_path / "synthetic.snapshot.sqlite3"
    sha256 = make_snapshot(snapshot)
    with pytest.raises(ValueError, match="operator expectation"):
        verify(snapshot, sha256, expected_tickers=940)


def test_tampered_snapshot_is_rejected(tmp_path: Path) -> None:
    snapshot = tmp_path / "synthetic.snapshot.sqlite3"
    sha256 = make_snapshot(snapshot)
    with snapshot.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="fingerprint"):
        verify(snapshot, sha256)


def test_cli_failure_does_not_claim_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "synthetic.snapshot.sqlite3"
    sha256 = make_snapshot(snapshot)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--snapshot",
                str(snapshot),
                "--sha256",
                sha256,
                "--expected-tickers",
                "940",
            ]
        )
    assert error.value.code == 2
    assert "verified" not in capsys.readouterr().out
