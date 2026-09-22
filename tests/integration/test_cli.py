import json
from pathlib import Path

import pytest

from donghak_stock_vision.cli import main


def test_offline_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    common = ["--db", str(tmp_path / "market.db")]
    selection = ["--tickers", "005930", "--start", "2024-01-02", "--end", "2024-01-08"]
    assert main([*common, "collect", "--provider", "fake", *selection]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] == 5
    assert main([*common, "update", "--provider", "fake", *selection]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] == 0
    assert main([*common, "query", *selection]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5
    assert main([*common, "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_key_missing_and_no_silent_fake_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    assert (
        main(
            [
                "--db",
                str(tmp_path / "market.db"),
                "collect",
                "--tickers",
                "005930",
                "--start",
                "2024-01-02",
                "--end",
                "2024-01-08",
            ]
        )
        == 2
    )
    assert not (tmp_path / "market.db").exists()


def test_missing_db_validate_is_not_false_success(tmp_path: Path) -> None:
    assert main(["--db", str(tmp_path / "missing.db"), "validate"]) == 2


def test_invalid_range(tmp_path: Path) -> None:
    assert (
        main(
            [
                "--db",
                str(tmp_path / "market.db"),
                "collect",
                "--provider",
                "fake",
                "--tickers",
                "005930",
                "--start",
                "2024-02-01",
                "--end",
                "2024-01-01",
            ]
        )
        == 2
    )


def test_help() -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
