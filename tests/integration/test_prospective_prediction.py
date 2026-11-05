"""Synthetic capture -> existing journal -> frozen prospective horizon lifecycle."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_signal import CALCULATED, changed, make

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_signal import generate_signal
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, append_signal, history
from donghak_stock_vision.storage.prospective_prediction import state

RECORDED = CALCULATED + timedelta(seconds=1)


def policy(**updates: Any) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "forward_prediction_policy_v1",
            "target_trading_date": "2025-07-11",
            "evaluate_not_before": "2025-07-12T00:00:00+00:00",
            "outcome_deadline": "2025-07-15T00:00:00+00:00",
            "max_recording_delay_seconds": 10,
            **updates,
        }
    )


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, FrozenJSON, Path]:
    root = tmp_path / "captures"
    cohort = ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 15)),
        ResearchDateRange(date(2025, 7, 16), date(2025, 7, 16)),
        ResearchDateRange(date(2025, 7, 17), date(2025, 7, 17)),
    )
    with patch(
        "donghak_stock_vision.forward_signal_demo_cli.ExplicitResearchSplit", return_value=cohort
    ):
        request = make(root)
    return root, request, tmp_path / "signals.sqlite3"


def append(
    source: tuple[Path, FrozenJSON, Path],
    head: str = GENESIS,
    at: datetime = RECORDED,
    owner: FrozenJSON | None = None,
) -> FrozenJSON:
    root, request, path = source
    p = owner if owner is not None else policy()
    return append_signal(
        path,
        root,
        request,
        request.identifier,
        head,
        at,
        prediction_policy=p,
        prediction_policy_pin=p.identifier,
    )


def test_freezes_original_predictor_inputs_and_horizon_without_source_writes(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    root, request, path = source
    before = (root / DB).read_bytes()
    result = append(source).to_dict()
    entry = history(path, result["head_pin"]).to_dict()["entries"][0]["entry"]
    prediction = entry["prediction"]
    decision = generate_signal(root, request, request.identifier)
    assert entry["version"] == "forward_signal_entry_v2"
    assert prediction["decision_pin"] == decision.identifier
    assert prediction["predictor"] == decision.to_dict()["strategy"]
    assert prediction["parameters"] == {"fast": 2, "slow": 3}
    assert prediction["reference_close"] == 120 and prediction["direction"] == "up"
    assert prediction["recorded_at"] == RECORDED.isoformat()
    assert prediction["policy"] == policy().to_dict()
    assert not prediction["independent_creation_time_verified"]
    assert not prediction["genuine_forward_evidence"]
    assert not prediction["execution_authorized"]
    assert (root / DB).read_bytes() == before


def test_late_duplicate_preserves_original_recording_time_and_head(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    first = append(source).to_dict()
    before = source[2].read_bytes()
    again = append(source, first["head_pin"], RECORDED + timedelta(days=20)).to_dict()
    assert again["duplicate"] and again["entry"] == first["entry"]
    assert again["head_pin"] == first["head_pin"]
    assert source[2].read_bytes() == before


@pytest.mark.parametrize(
    "updates",
    [
        {"target_trading_date": "2025-07-12", "evaluate_not_before": "2025-07-13T00:00:00+00:00"},
        {"outcome_deadline": "2025-07-16T00:00:00+00:00"},
    ],
)
def test_changed_horizon_cannot_overwrite_recorded_prediction(
    source: tuple[Path, FrozenJSON, Path],
    updates: dict[str, Any],
) -> None:
    first = append(source).to_dict()
    before = source[2].read_bytes()
    with pytest.raises(ValueError, match="different_prediction_contract"):
        append(source, first["head_pin"], owner=policy(**updates))
    assert source[2].read_bytes() == before


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"target_trading_date": "2025-07-10"}, "target_must_be_future"),
        ({"evaluate_not_before": "2025-07-11T00:00:00+00:00"}, "horizon_order"),
        ({"outcome_deadline": "2025-07-12T00:00:00+00:00"}, "horizon_order"),
        ({"evaluate_not_before": "2025-07-12T09:00:00+09:00"}, "utc_required"),
        ({"max_recording_delay_seconds": True}, "recording_delay_budget"),
        ({"version": "unknown"}, "policy_version"),
    ],
)
def test_bad_or_retrospective_policy_is_rejected_without_output_creation(
    source: tuple[Path, FrozenJSON, Path], updates: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        append(source, owner=policy(**updates))
    assert not source[2].exists()


def test_clock_order_and_staleness(source: tuple[Path, FrozenJSON, Path]) -> None:
    for at, reason in [
        (CALCULATED - timedelta(seconds=1), "before_calculation"),
        (CALCULATED + timedelta(seconds=11), "clock_or_staleness"),
    ]:
        with pytest.raises(ValueError, match=reason):
            append(source, at=at)
    assert not source[2].exists()


def test_wrong_policy_pin(source: tuple[Path, FrozenJSON, Path]) -> None:
    root, req, path = source
    with pytest.raises(ValueError, match="policy_pin_mismatch"):
        append_signal(
            path,
            root,
            req,
            req.identifier,
            GENESIS,
            RECORDED,
            prediction_policy=policy(),
            prediction_policy_pin="0" * 64,
        )
    assert not path.exists()


def test_target_in_protected_partition_rejected_before_capture_loading(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    p = policy(
        target_trading_date="2025-07-17",
        evaluate_not_before="2025-07-18T00:00:00+00:00",
        outcome_deadline="2025-07-20T00:00:00+00:00",
    )
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal.generate_signal",
        side_effect=AssertionError("capture must not load"),
    ):
        with pytest.raises(ValueError, match="outside_authorized_research_partition"):
            append(source, owner=p)
    assert not source[2].exists()


@pytest.mark.parametrize("signal", ["HOLD", "INSUFFICIENT_DATA"])
def test_non_directional_signal_cannot_become_a_prediction(
    source: tuple[Path, FrozenJSON, Path], signal: str
) -> None:
    value = generate_signal(source[0], source[1], source[1].identifier).to_dict()
    value["calculation"]["signal"] = signal
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal.generate_signal",
        return_value=FrozenJSON.freeze(value),
    ):
        with pytest.raises(ValueError, match="directional_prediction_required"):
            append(source)
    assert not source[2].exists()


def test_real_origin_cannot_gain_authenticated_prediction_status(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    root, req, path = source
    value = generate_signal(root, req, req.identifier).to_dict()
    value["origin"] = "real_provider_https_local_capture"
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal.generate_signal",
        return_value=FrozenJSON.freeze(value),
    ):
        with pytest.raises(ValueError, match="timestamp_evidence_unavailable"):
            append(source)
    assert not path.exists()


@pytest.mark.parametrize(
    "at,expected",
    [
        (RECORDED, "pending"),
        (datetime(2025, 7, 12, tzinfo=UTC), "matured"),
        (datetime(2025, 7, 15, tzinfo=UTC), "matured"),
        (datetime(2025, 7, 15, tzinfo=UTC) + timedelta(microseconds=1), "unavailable"),
    ],
)
def test_restart_status_and_no_fabricated_outcome(
    source: tuple[Path, FrozenJSON, Path], at: datetime, expected: str
) -> None:
    first = append(source).to_dict()
    prediction = history(source[2], first["head_pin"]).to_dict()["entries"][0]["entry"][
        "prediction"
    ]
    before = source[2].read_bytes()
    result = state(FrozenJSON.freeze(prediction), at).to_dict()
    assert result["state"] == expected and result["outcome"] is None
    assert not result["genuine_forward_evidence"]
    assert source[2].read_bytes() == before
    with pytest.raises(ValueError, match="before_prediction_recording"):
        state(FrozenJSON.freeze(prediction), RECORDED - timedelta(seconds=1))


def test_legacy_cannot_be_retroactively_promoted_and_mixed_chain_is_readable(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    root, req, path = source
    old = append_signal(path, root, req, req.identifier, GENESIS, RECORDED).to_dict()
    with pytest.raises(ValueError, match="different_prediction_contract"):
        append(source, old["head_pin"])
    new_req = changed(req, fast=1)
    new = append((root, new_req, path), old["head_pin"]).to_dict()
    entries = history(path, new["head_pin"]).to_dict()["entries"]
    assert [e["entry"]["version"] for e in entries] == [
        "forward_signal_entry_v1",
        "forward_signal_entry_v2",
    ]
    assert entries[0] == old["entry"]


def test_rehashed_prediction_forgery_is_rejected(source: tuple[Path, FrozenJSON, Path]) -> None:
    result = append(source).to_dict()
    entry = result["entry"]["entry"]
    entry["prediction"]["reference_close"] = 1
    forged = FrozenJSON.freeze(entry)
    with sqlite3.connect(source[2]) as db:
        db.execute("DROP TRIGGER signal_entries_no_update")
        db.execute(
            "UPDATE signal_entries SET document=?,entry_pin=?",
            (forged.payload_json, forged.identifier),
        )
        db.execute(
            "CREATE TRIGGER signal_entries_no_update BEFORE UPDATE ON signal_entries "
            "BEGIN SELECT RAISE(ABORT,'immutable'); END"
        )
    with pytest.raises(ValueError, match="prediction_evidence_mismatch"):
        history(source[2], forged.identifier)


def test_interrupted_append_rolls_back_and_can_resume(
    source: tuple[Path, FrozenJSON, Path],
) -> None:
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal._commit", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            append(source)
    assert history(source[2], GENESIS).to_dict()["total"] == 0
    result = append(source).to_dict()
    assert history(source[2], result["head_pin"]).to_dict()["total"] == 1


def test_cli_prediction_and_status(
    source: tuple[Path, FrozenJSON, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    root, req, journal = source
    request_path, policy_path = tmp_path / "request.json", tmp_path / "policy.json"
    request_path.write_text(req.payload_json)
    policy_path.write_text(policy().payload_json)

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return RECORDED

    base = [
        "forward-signal",
        "--capture-root",
        str(root),
        "--request",
        str(request_path),
        "--request-pin",
        req.identifier,
        "--journal",
        str(journal),
    ]
    with patch("donghak_stock_vision.forward_signal_cli.datetime", Clock):
        assert (
            main(
                base
                + [
                    "predict",
                    "--journal-pin",
                    GENESIS,
                    "--prediction-policy",
                    str(policy_path),
                    "--prediction-policy-pin",
                    policy().identifier,
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert (
            main(
                base
                + [
                    "prediction-status",
                    "--journal-pin",
                    result["head_pin"],
                    "--signal-id",
                    result["entry"]["entry"]["decision"]["signal_id"],
                ]
            )
            == 0
        )
    assert json.loads(capsys.readouterr().out)["state"] == "pending"


def test_executable_offline_demo_uses_existing_capture_and_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from tests.integration.test_forward_signal import NOW, IngestionClock

    from donghak_stock_vision.storage.forward_capture import run_capture

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Clock":
            return cls.fromtimestamp(CALCULATED.timestamp(), UTC)

    def captured(*args: Any, **kwargs: Any) -> FrozenJSON:
        return run_capture(*args, **kwargs, clock=lambda: NOW, sleep=lambda _: None)

    root, path = tmp_path / "new-captures", tmp_path / "signals.db"
    with (
        patch("donghak_stock_vision.forward_signal_demo_cli.datetime", Clock),
        patch("donghak_stock_vision.forward_signal_demo_cli.run_capture", side_effect=captured),
        patch("donghak_stock_vision.storage.sqlite.datetime", IngestionClock),
    ):
        assert (
            main(
                [
                    "forward-signal",
                    "offline-prediction-demo",
                    "--capture-root",
                    str(root),
                    "--journal",
                    str(path),
                ]
            )
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["origin"] == "offline_fixture" and result["live_requests"] == 0
    assert result["prediction"]["policy"]["target_trading_date"] == "2025-07-11"
    assert result["status"]["state"] == "pending"
    assert history(path, result["journal_head_pin"]).to_dict()["total"] == 1
