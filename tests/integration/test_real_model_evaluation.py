"""KRX-schema fixtures, not actual exchange performance. Real owners are verified."""

import hashlib
import json
import socket
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_real_market_dataset import request_data as request_data

from donghak_stock_vision.cli import main
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.real_market_dataset import train_dataset
from donghak_stock_vision.storage.real_model_evaluation import _trace, _verified, compute, export


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: str) -> None:
    path.write_text(value)
    path.chmod(0o600)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture(scope="module")
def evaluation_request(
    request_data: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("frozen-evaluation")
    output = root / "model.sqlite3"
    result = train_dataset(request_data, output).to_dict()
    request = root / "dataset.json"
    write(request, json.dumps(request_data))
    request_pin = root / "dataset.pin"
    write(request_pin, digest(request_data))
    pins = root / "source-pins"
    pins.mkdir(mode=0o700)
    with closing(ReadOnlyAnalysisStore(output)) as store:
        manifest = store.get("real_market_dataset", result["manifest_id"])
    for name, value in [
        ("source", digest(manifest["source"])),
        ("snapshot", result["snapshot_id"]),
        ("manifest", result["manifest_id"]),
    ]:
        write(pins / (name + ".pin"), value)
    return dict(
        version="real_model_evaluation_request_v1",
        dataset_request=str(request),
        dataset_request_pin_file=str(request_pin),
        model_database=str(output),
        model_database_pin=sha(output),
        model_version=result["training"]["model_version"],
        source_pins_root=str(pins),
        evaluated_at=datetime.now(UTC).isoformat(),
    )


@pytest.fixture(scope="module")
def evaluated(evaluation_request: dict[str, Any]) -> dict[str, str]:
    return compute(evaluation_request)


def test_real_owners_reproduction_without_fit(
    evaluation_request: dict[str, Any], evaluated: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.models.service import TrainingService

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("training forbidden during evaluation")

    monkeypatch.setattr(TrainingService, "train", forbidden)
    path = Path(evaluation_request["model_database"])
    before = sha(path)
    code = json.loads(evaluated["evaluation-manifest.json"])["code"]
    assert compute(evaluation_request, code) == evaluated
    assert sha(path) == before
    result = json.loads(evaluated["evaluation-summary.json"])
    assert result["parameters_unchanged"] and result["reproducibility_verified"]
    assert not result["trace"]["final_test_observations_read"]
    assert not result["performance_approved"] and not result["pit_qualified"]
    assert result["diagnostics"]["calibration"] == "none"


def test_ticker_and_period_counts(evaluated: dict[str, str]) -> None:
    result = json.loads(evaluated["evaluation-summary.json"])["diagnostics"]
    for direction, values in result["directions"].items():
        n = values["partitions"]["diagnostic"]["metrics"]["n"]
        assert sum(r["metrics"]["n"] for r in values["by_ticker"].values()) == n
        for kind in ["month", "quarter"]:
            rows = [
                r
                for r in result["temporal"]
                if r["direction"] == direction and r["period_kind"] == kind
            ]
            assert sum(r["metrics"]["n"] for r in rows if r["ticker"] == "ALL") == n
            assert sum(r["metrics"]["n"] for r in rows if r["ticker"] != "ALL") == n
    assert len(evaluated) == 6
    assert "training_majority_accuracy" in evaluated["per-ticker-metrics.csv"]


@pytest.mark.parametrize(
    "damage", ["version", "model_pin", "model_id", "timestamp", "dataset_owner"]
)
def test_substitutions_fail_closed(
    evaluation_request: dict[str, Any], tmp_path: Path, damage: str
) -> None:
    d = dict(evaluation_request)
    if damage == "version":
        d["version"] = "future"
    elif damage == "model_pin":
        d["model_database_pin"] = "0" * 64
    elif damage == "model_id":
        d["model_version"] = "0" * 64
    elif damage == "timestamp":
        d["evaluated_at"] = "2020-01-01T00:00:00+00:00"
    else:
        request = json.loads(Path(d["dataset_request"]).read_text())
        request["diagnostic_test"]["start"] = "2020-01-01"
        path = tmp_path / "other.json"
        write(path, json.dumps(request))
        pin = tmp_path / "other.pin"
        write(pin, digest(request))
        d["dataset_request"] = str(path)
        d["dataset_request_pin_file"] = str(pin)
    with pytest.raises(ValueError):
        _verified(d)


def test_protected_rows_and_future_labels_fail(evaluation_request: dict[str, Any]) -> None:
    _, dataset, snapshot, metadata = _verified(evaluation_request)
    original = json.loads(json.dumps(snapshot))
    ticker = next(iter(original["rows"]))
    original["rows"][ticker][0]["trading_date"] = original["real_market_source"]["request"][
        "access_split"
    ]["final_test"]["start"]
    with pytest.raises(ValueError, match="protected_rows"):
        _trace(original, dataset, metadata["model_created_at"])
    changed = json.loads(json.dumps(dataset))
    changed["split"]["partitions"]["train"][0]["label_end"] = "2099-01-01"
    with pytest.raises(ValueError, match="label_boundary"):
        _trace(snapshot, changed, metadata["model_created_at"])


def test_export_readback_tampering_and_exclusive_output(
    evaluation_request: dict[str, Any], tmp_path: Path
) -> None:
    output = tmp_path / "evaluation"
    pins = tmp_path / "pins"
    export(evaluation_request, output, pins)
    export(evaluation_request, output, pins, verify=True)
    (output / "per-ticker-metrics.csv").write_text("forged")
    with pytest.raises(ValueError, match="retained_output"):
        export(evaluation_request, output, pins, verify=True)
    with pytest.raises(ValueError, match="new_output"):
        export(evaluation_request, output, pins)


def test_cli_rejects_pin_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("dotenv forbidden")

    monkeypatch.setattr("donghak_stock_vision.cli.load_dotenv", denied)
    request = tmp_path / "request.json"
    write(request, "{}")
    pin = tmp_path / "request.pin"
    write(pin, "0" * 64)
    output = tmp_path / "output"
    assert (
        main(
            [
                "real-model-evaluation",
                "run",
                "--request",
                str(request),
                "--request-pin-file",
                str(pin),
                "--output",
                str(output),
                "--pins-root",
                str(tmp_path / "pins"),
            ]
        )
        == 2
    )
    assert not output.exists()
