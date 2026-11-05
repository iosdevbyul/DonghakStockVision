"""Independent offline future-observation resolution from original captures."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_signal import CALCULATED, NOW, changed, make

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.future_observation_cli import main
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_signal import generate_signal
from donghak_stock_vision.storage.future_observation import resolve_observation, verify_resolution


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, FrozenJSON]:
    root = tmp_path / "future"
    request = make(root)
    return root, changed(
        request, start="2025-07-04", end="2025-07-04", sources=request.to_dict()["sources"][-1:]
    )


def window(**updates: Any) -> dict[str, Any]:
    return {
        "recorded_at": datetime(2025, 7, 2, tzinfo=UTC),
        "target_day": date(2025, 7, 4),
        "not_before": datetime(2025, 7, 5, tzinfo=UTC),
        "available_by": datetime(2025, 7, 12, tzinfo=UTC),
        "as_of": CALCULATED,
        **updates,
    }


def test_original_future_bar_verified_without_mutation(inputs: tuple[Path, FrozenJSON]) -> None:
    root, request = inputs
    before = (root / DB).read_bytes()
    result = resolve_observation(root, request, request.identifier, **window())
    doc = result.to_dict()
    assert doc["status"] == "available"
    assert doc["mode"] == "offline_fixture_resolution" and doc["source_origin"] == "offline_fixture"
    assert (
        doc["observation"]
        == generate_signal(root, request, request.identifier).to_dict()["inputs"]["observations"][0]
    )
    assert doc["observation"]["bar"]["close"] == 120
    assert doc["request_pin"] == request.identifier
    assert not doc["prediction_binding_verified"] and not doc["genuine_forward_evidence"]
    assert not doc["independent_timestamp_verified"] and not doc["execution_authorized"]
    verify_resolution(result, result.identifier, root, request, request.identifier, **window())
    assert (
        resolve_observation(root, FrozenJSON(request.payload_json), request.identifier, **window())
        == result
    )
    assert (root / DB).read_bytes() == before


def test_pending_never_loads_future_capture(inputs: tuple[Path, FrozenJSON]) -> None:
    root, request = inputs
    with patch(
        "donghak_stock_vision.storage.future_observation.generate_signal",
        side_effect=AssertionError("future source must not load"),
    ):
        result = resolve_observation(
            root / "missing",
            request,
            request.identifier,
            **window(as_of=datetime(2025, 7, 3, tzinfo=UTC)),
        ).to_dict()
    assert result["status"] == "pending"
    assert result["observation"] is None and result["request_pin"] is None
    assert not (root / "missing").exists()


@pytest.mark.parametrize("after_deadline", [False, True])
def test_missing_outcome_stays_unavailable(after_deadline: bool) -> None:
    result = resolve_observation(
        None,
        None,
        None,
        **window(as_of=datetime(2025, 7, 13, tzinfo=UTC) if after_deadline else CALCULATED),
    ).to_dict()
    assert result["status"] == "unavailable" and result["observation"] is None


@pytest.mark.parametrize(
    "updates",
    [
        {"recorded_at": datetime(2025, 7, 4, tzinfo=UTC)},
        {"target_day": datetime(2025, 7, 4, tzinfo=UTC)},
        {"not_before": datetime(2025, 7, 4, tzinfo=UTC)},
        {"available_by": datetime(2025, 7, 5, tzinfo=UTC)},
        {"as_of": datetime(2025, 7, 1, tzinfo=UTC)},
        {"available_by": datetime(2026, 7, 12, tzinfo=UTC)},
        {"recorded_at": datetime(2025, 7, 2)},
    ],
)
def test_invalid_windows_fail_before_source_io(updates: dict[str, Any]) -> None:
    with patch(
        "donghak_stock_vision.storage.future_observation.generate_signal",
        side_effect=AssertionError("no source loading"),
    ):
        with pytest.raises(ValueError):
            resolve_observation(None, None, None, **window(**updates))


def test_request_from_after_as_of_is_rejected_before_source_loading(
    inputs: tuple[Path, FrozenJSON],
) -> None:
    root, request = inputs
    with patch(
        "donghak_stock_vision.storage.future_observation.generate_signal",
        side_effect=AssertionError("no future loading"),
    ):
        with pytest.raises(ValueError, match="after_as_of"):
            resolve_observation(root, request, request.identifier, **window(as_of=NOW))


def test_deadline_is_arrival_not_processing_time(inputs: tuple[Path, FrozenJSON]) -> None:
    root, request = inputs
    result = resolve_observation(
        root,
        request,
        request.identifier,
        **window(available_by=NOW + timedelta(seconds=1), as_of=datetime(2025, 7, 13, tzinfo=UTC)),
    ).to_dict()
    assert result["status"] == "available"
    late = resolve_observation(
        root, request, request.identifier, **window(available_by=NOW)
    ).to_dict()
    assert late["status"] == "unavailable" and late["observation"] is None
    assert late["reason"] == "observation_not_available_by_deadline"


def test_wrong_scope_and_protected_partition_rejected_before_capture_io(
    inputs: tuple[Path, FrozenJSON],
) -> None:
    root, request = inputs
    bad = request.to_dict()
    bad["sources"][0]["plan"]["partition"] = "final_test"
    protected = FrozenJSON.freeze(bad)
    with patch(
        "donghak_stock_vision.storage.future_observation.generate_signal",
        side_effect=AssertionError("no protected capture loading"),
    ):
        with pytest.raises(ValueError, match="partition"):
            resolve_observation(root, protected, protected.identifier, **window())
        with pytest.raises(ValueError, match="exact_target"):
            resolve_observation(
                root, request, request.identifier, **window(target_day=date(2025, 7, 3))
            )
        with pytest.raises(ValueError, match="pin_mismatch"):
            resolve_observation(root, request, "0" * 64, **window())


def test_missing_capture_does_not_create_database(inputs: tuple[Path, FrozenJSON]) -> None:
    root, request = inputs
    missing = root / "no-capture"
    result = resolve_observation(missing, request, request.identifier, **window()).to_dict()
    assert result["status"] == "unavailable" and not missing.exists()


def test_rehashed_observation_forgery_requires_semantic_replay(
    inputs: tuple[Path, FrozenJSON],
) -> None:
    root, request = inputs
    original = resolve_observation(root, request, request.identifier, **window())
    doc = original.to_dict()
    doc["observation"]["bar"]["close"] = 99999
    forged = FrozenJSON.freeze(doc)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_resolution(forged, forged.identifier, root, request, request.identifier, **window())
    with pytest.raises(ValueError, match="resolution_pin_mismatch"):
        verify_resolution(original, "0" * 64, root, request, request.identifier, **window())


def test_modified_capture_cannot_be_reinterpreted_as_future_evidence(
    inputs: tuple[Path, FrozenJSON],
) -> None:
    root, request = inputs
    with (root / DB).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="database_pin_mismatch"):
        resolve_observation(root, request, request.identifier, **window())


@pytest.mark.parametrize("defect", ["origin", "clock"])
def test_incompatible_upstream_observation_is_never_promoted(
    inputs: tuple[Path, FrozenJSON], defect: str
) -> None:
    root, request = inputs
    value = generate_signal(root, request, request.identifier).to_dict()
    if defect == "origin":
        value["origin"] = "real_provider_https_local_capture"
    else:
        value["inputs"]["observations"][0]["local_capture_at"] = window()["recorded_at"].isoformat()
    with patch(
        "donghak_stock_vision.storage.future_observation.generate_signal",
        return_value=FrozenJSON.freeze(value),
    ):
        with pytest.raises(ValueError, match="origin_mismatch|chronology_mismatch"):
            resolve_observation(root, request, request.identifier, **window())


def test_old_or_mixed_observations_cannot_substitute_for_target(
    inputs: tuple[Path, FrozenJSON],
) -> None:
    root, request = inputs
    original = make(root / "other")
    with pytest.raises(ValueError, match="exact_target"):
        resolve_observation(root / "other", original, original.identifier, **window())
    prior = changed(original, sources=original.to_dict()["sources"][:1])
    with pytest.raises(ValueError, match="exact_target"):
        resolve_observation(root / "other", prior, prior.identifier, **window())


def test_cli_pending_does_not_open_later_request_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.json"
    assert (
        main(
            [
                "--capture-root",
                str(tmp_path / "missing"),
                "--request",
                str(missing),
                "--request-pin",
                "0" * 64,
                "--recorded-at",
                "2025-07-02T00:00:00Z",
                "--target",
                "2025-07-04",
                "--not-before",
                "2025-07-05T00:00:00Z",
                "--available-by",
                "2025-07-12T00:00:00Z",
                "--as-of",
                "2025-07-03T00:00:00Z",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "pending" and not missing.exists()


def test_cli_verified_readback(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, request = inputs
    path = tmp_path / "request.json"
    path.write_text(request.payload_json)
    before = (root / DB).read_bytes()
    assert (
        main(
            [
                "--capture-root",
                str(root),
                "--request",
                str(path),
                "--request-pin",
                request.identifier,
                "--recorded-at",
                "2025-07-02T00:00:00Z",
                "--target",
                "2025-07-04",
                "--not-before",
                "2025-07-05T00:00:00Z",
                "--available-by",
                "2025-07-12T00:00:00Z",
                "--as-of",
                CALCULATED.isoformat(),
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "available" and doc["observation"]["bar"]["close"] == 120
    assert (root / DB).read_bytes() == before
