import json
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.service import freeze_request, run_backtest
from donghak_stock_vision.cli import main, parser
from tests.backtest.test_performance import valuation_policy
from tests.backtest.test_position_history import round_trip


@pytest.fixture(scope="module")
def frozen_request() -> FrozenJSON:
    return freeze_request(round_trip(), valuation_policy())


def arguments(path: Path, document: FrozenJSON) -> list[str]:
    c = document.to_dict()["config"]
    return [
        "backtest",
        "--input",
        str(path),
        "--start",
        c["start_at"],
        "--end",
        c["end_at"],
        "--initial-cash",
        "10000",
        "--tickers",
        "005930",
    ]


def test_cli_full_flow_json_text_and_repeat(
    frozen_request: FrozenJSON,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "frozen.json"
    path.write_text(frozen_request.payload_json)
    args = arguments(path, frozen_request)
    assert main([*args, "--format", "json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["performance"]["total_return"] == "0.015973"
    assert first["performance"]["completed_trades"] == 1
    assert main([*args, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert main(args) == 0
    text = capsys.readouterr().out
    assert "SYNTHETIC HISTORICAL RESEARCH" in text and "1.5973%" in text
    assert first["performance_hash"] in text


@pytest.mark.parametrize(
    "flag,value",
    [("--initial-cash", "20000"), ("--tickers", "000001"), ("--start", "2099-01-01T00:00:00Z")],
)
def test_cli_conflicting_frozen_inputs(
    frozen_request: FrozenJSON,
    tmp_path: Path,
    flag: str,
    value: str,
) -> None:
    path = tmp_path / "frozen.json"
    path.write_text(frozen_request.payload_json)
    args = arguments(path, frozen_request)
    args[args.index(flag) + 1] = value
    assert main(args) == 2


def test_cli_missing_explicit_inputs_and_malformed_json(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["backtest", "--start", "2026-01-01"])
    path = tmp_path / "bad.json"
    path.write_text("{}")
    assert (
        main(
            [
                "backtest",
                "--input",
                str(path),
                "--start",
                "2026-01-01T00:00:00Z",
                "--end",
                "2026-01-02T00:00:00Z",
                "--initial-cash",
                "10000",
                "--tickers",
                "005930",
            ]
        )
        == 2
    )


def test_reusable_service_rejects_missing_policy(frozen_request: FrozenJSON) -> None:
    d = frozen_request.to_dict()
    del d["valuation_policy"]["field"]
    with pytest.raises(ValueError, match="missing_or_unknown_fields"):
        run_backtest(
            FrozenJSON.freeze(d),
            start=d["config"]["start_at"],
            end=d["config"]["end_at"],
            initial_cash="10000",
            tickers=["005930"],
        )
