"""Original journal ownership first; all fixtures are explicitly synthetic."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_forward_signal import changed, make
from tests.integration.test_prospective_prediction import RECORDED, policy

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.forward_signal_demo_cli import prepare
from donghak_stock_vision.prediction_outcome_cli import main
from donghak_stock_vision.storage.forward_capture import DB, run_capture
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, append_signal
from donghak_stock_vision.storage.prediction_binding import (
    FutureSource,
    PredictionSource,
    bind_outcome,
    inspect_prediction,
    verify_binding,
)


def make_original(tmp_path: Path, closes: tuple[int, ...] = (100, 90, 80, 120)) -> PredictionSource:
    root = tmp_path / "original-captures"
    split = ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 15)),
        ResearchDateRange(date(2025, 7, 16), date(2025, 7, 16)),
        ResearchDateRange(date(2025, 7, 17), date(2025, 7, 17)),
    )
    with patch(
        "donghak_stock_vision.forward_signal_demo_cli.ExplicitResearchSplit", return_value=split
    ):
        request = make(root, closes)
    p = policy()
    journal = tmp_path / "signals.sqlite3"
    appended = append_signal(
        journal,
        root,
        request,
        request.identifier,
        GENESIS,
        RECORDED,
        prediction_policy=p,
        prediction_policy_pin=p.identifier,
    ).to_dict()
    entry = appended["entry"]
    return PredictionSource(
        journal,
        appended["head_pin"],
        entry["entry_pin"],
        entry["entry"]["prediction"]["prediction_id"],
        root,
        request,
        request.identifier,
        p.identifier,
    )


@pytest.fixture
def original(tmp_path: Path) -> PredictionSource:
    return make_original(tmp_path)


FUTURE_AT = datetime(2025, 7, 12, 1, tzinfo=UTC)
AS_OF = FUTURE_AT + timedelta(seconds=3)


def future_capture(
    original: PredictionSource,
    root: Path,
    *,
    close: int = 130,
    at: datetime = FUTURE_AT,
    day: date = date(2025, 7, 11),
    split: ExplicitResearchSplit | None = None,
) -> FutureSource:
    plan = ForwardCapturePlan.from_dict(original.request.to_dict()["sources"][0]["plan"])
    assert plan.split is not None

    class Clock:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return at + timedelta(seconds=2)

    class Ingest:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return at + timedelta(seconds=1)

    def captured(*args: Any, **kwargs: Any) -> FrozenJSON:
        return run_capture(*args, **kwargs, clock=lambda: at, sleep=lambda _: None)

    with (
        patch("donghak_stock_vision.forward_signal_demo_cli.run_capture", side_effect=captured),
        patch("donghak_stock_vision.forward_signal_demo_cli.datetime", Clock),
        patch("donghak_stock_vision.storage.sqlite.datetime", Ingest),
    ):
        request = prepare(
            root, (close,), research_split=split or plan.split, observation_days=(day,)
        )
    return FutureSource(root, request, request.identifier)


def test_original_prediction_replays_without_mutation(original: PredictionSource) -> None:
    before = original.journal.read_bytes(), (original.capture_root / DB).read_bytes()
    verified = inspect_prediction(original).to_dict()
    assert verified["prediction"]["prediction_id"] == original.prediction_id
    assert verified["prediction"]["policy"] == policy().to_dict()
    assert not verified["genuine_forward_evidence"]
    assert (original.journal.read_bytes(), (original.capture_root / DB).read_bytes()) == before


def test_pending_does_not_require_or_load_future_owner(
    original: PredictionSource, tmp_path: Path
) -> None:
    invalid_future = FutureSource(
        tmp_path / "must-not-open", FrozenJSON.freeze({"invalid": True}), "0" * 64
    )
    result = bind_outcome(original, RECORDED, invalid_future)
    d = result.to_dict()
    assert d["status"] == "pending" and d["outcome"] is None
    assert d["prediction_binding_verified"] and not d["pair_verified"]
    assert not d["offline_evaluation_eligible"] and not d["genuine_forward_evidence"]
    verify_binding(result, result.identifier, original, RECORDED, invalid_future)
    assert not (tmp_path / "must-not-open").exists()


@pytest.mark.parametrize("field", ["journal_head", "entry_pin", "prediction_id", "policy_pin"])
def test_changed_original_pins_are_invalid(original: PredictionSource, field: str) -> None:
    updates: dict[str, Any] = {field: "0" * 64}
    result = bind_outcome(replace(original, **updates), RECORDED).to_dict()
    assert result["status"] == "invalid" and not result["pair_verified"]
    assert result["outcome"] is None


def test_status_cannot_precede_recording(original: PredictionSource) -> None:
    assert (
        bind_outcome(original, RECORDED - timedelta(microseconds=1)).to_dict()["status"]
        == "invalid"
    )


def test_legacy_entry_is_not_retroactively_promoted(
    original: PredictionSource, tmp_path: Path
) -> None:
    path = tmp_path / "legacy.sqlite3"
    record = append_signal(
        path, original.capture_root, original.request, original.request_pin, GENESIS, RECORDED
    ).to_dict()
    owner = replace(
        original,
        journal=path,
        journal_head=record["head_pin"],
        entry_pin=record["entry"]["entry_pin"],
    )
    before = path.read_bytes()
    result = bind_outcome(owner, RECORDED).to_dict()
    assert result["status"] == "invalid"
    assert result["reason"] == "legacy_entry_has_no_predeclared_prediction"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "close,observed,correct", [(130, "up", True), (110, "down", False), (120, "flat", False)]
)
def test_bound_outcome_uses_frozen_rule_and_exact_source(
    original: PredictionSource, tmp_path: Path, close: int, observed: str, correct: bool
) -> None:
    future = future_capture(original, tmp_path / "future", close=close)
    paths = (original.journal, original.capture_root / DB, future.capture_root / DB)
    before = [p.read_bytes() for p in paths]
    result = bind_outcome(original, AS_OF, future)
    d = result.to_dict()
    assert d["status"] == "verified" and d["pair_verified"]
    assert d["prediction_binding_verified"] and d["offline_evaluation_eligible"]
    assert not d["prospective_evaluation_eligible"] and not d["genuine_forward_evidence"]
    assert not d["independent_creation_time_verified"] and not d["execution_authorized"]
    assert d["outcome"]["observed_direction"] == observed
    assert d["outcome"]["correct"] == correct
    assert d["outcome"]["reference_close"] == 120 and d["outcome"]["target_close"] == close
    assert d["outcome"]["price_change_fraction"] == {"numerator": close - 120, "denominator": 120}
    assert d["target_trading_date"] == "2025-07-11"
    assert bind_outcome(replace(original), AS_OF, replace(future)) == result
    verify_binding(result, result.identifier, original, AS_OF, future)
    assert [p.read_bytes() for p in paths] == before


def test_sell_prediction_is_not_reinterpreted_as_buy(tmp_path: Path) -> None:
    original = make_original(tmp_path, (100, 110, 120, 80))
    future = future_capture(original, tmp_path / "future", close=70)
    d = bind_outcome(original, AS_OF, future).to_dict()
    assert d["status"] == "verified"
    assert d["outcome"]["predicted_direction"] == "down" and d["outcome"]["correct"]


@pytest.mark.parametrize("kind", ["missing", "early", "late"])
def test_unavailable_never_becomes_a_failed_or_zero_outcome(
    original: PredictionSource, tmp_path: Path, kind: str
) -> None:
    due = datetime(2025, 7, 12, tzinfo=UTC)
    deadline = datetime(2025, 7, 15, tzinfo=UTC)
    future = None
    at = AS_OF
    if kind == "early":
        future = future_capture(original, tmp_path / "future", at=due - timedelta(seconds=1))
    elif kind == "late":
        future = future_capture(original, tmp_path / "future", at=deadline)
        at = deadline + timedelta(seconds=3)
    d = bind_outcome(original, at, future).to_dict()
    assert d["status"] == "unavailable" and d["outcome"] is None
    assert not d["pair_verified"] and not d["offline_evaluation_eligible"]
    if kind == "early":
        assert d["reason"] == "observation_precedes_frozen_gate"


def test_timely_observation_can_be_processed_after_deadline(
    original: PredictionSource, tmp_path: Path
) -> None:
    deadline = datetime(2025, 7, 15, tzinfo=UTC)
    future = future_capture(original, tmp_path / "future", at=deadline - timedelta(seconds=1))
    d = bind_outcome(original, deadline + timedelta(days=1), future).to_dict()
    assert d["status"] == "verified"


@pytest.mark.parametrize("kind", ["target", "cohort", "ticker", "pin"])
def test_future_substitution_is_invalid(
    original: PredictionSource, tmp_path: Path, kind: str
) -> None:
    split = None
    day = date(2025, 7, 11)
    if kind == "target":
        day = date(2025, 7, 14)
    if kind == "cohort":
        split = ExplicitResearchSplit(
            ResearchDateRange(date(2025, 6, 30), date(2025, 7, 15)),
            ResearchDateRange(date(2025, 7, 16), date(2025, 7, 16)),
            ResearchDateRange(date(2025, 7, 17), date(2025, 7, 17)),
        )
    future = future_capture(
        original,
        tmp_path / "future",
        day=day,
        split=split,
        at=AS_OF + timedelta(days=4) if kind == "target" else FUTURE_AT,
    )
    if kind == "ticker":
        req = changed(future.request, symbol="000001")
        future = replace(future, request=req, request_pin=req.identifier)
    if kind == "pin":
        future = replace(future, request_pin="0" * 64)
    d = bind_outcome(original, AS_OF, future).to_dict()
    assert d["status"] == "invalid" and d["outcome"] is None
    assert not d["pair_verified"]


def test_tampered_sources_and_rehashed_result_are_rejected(
    original: PredictionSource, tmp_path: Path
) -> None:
    future = future_capture(original, tmp_path / "future")
    report = bind_outcome(original, AS_OF, future)
    forged = report.to_dict()
    forged["outcome"]["correct"] = False
    owner = FrozenJSON.freeze(forged)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_binding(owner, owner.identifier, original, AS_OF, future)
    with (future.capture_root / DB).open("ab") as stream:
        stream.write(b"tampered")
    assert bind_outcome(original, AS_OF, future).to_dict()["status"] == "invalid"


def test_original_predictor_substitution_fails_semantic_replay(original: PredictionSource) -> None:
    fake = {"strategy_id": "substituted", "strategy_version": "1", "code_sha256": "0" * 64}
    with patch("donghak_stock_vision.storage.forward_signal.reference", return_value=fake):
        d = bind_outcome(original, RECORDED).to_dict()
    assert d["status"] == "invalid" and not d["pair_verified"]


def test_original_entry_selection_survives_later_journal_appends(
    original: PredictionSource, tmp_path: Path
) -> None:
    req = changed(original.request, fast=1)
    p = policy()
    appended = append_signal(
        original.journal,
        original.capture_root,
        req,
        req.identifier,
        original.journal_head,
        RECORDED + timedelta(seconds=1),
        prediction_policy=p,
        prediction_policy_pin=p.identifier,
    ).to_dict()
    selected = replace(original, journal_head=appended["head_pin"])
    future = future_capture(original, tmp_path / "future")
    d = bind_outcome(selected, AS_OF, future).to_dict()
    assert d["status"] == "verified" and d["prediction_id"] == original.prediction_id
    assert d["entry_pin"] == original.entry_pin


def cli_args(original: PredictionSource, tmp_path: Path, at: datetime) -> list[str]:
    request = tmp_path / "original-request.json"
    request.write_text(original.request.payload_json)
    return [
        "--journal",
        str(original.journal),
        "--journal-head",
        original.journal_head,
        "--entry-pin",
        original.entry_pin,
        "--prediction-id",
        original.prediction_id,
        "--capture-root",
        str(original.capture_root),
        "--request",
        str(request),
        "--request-pin",
        original.request_pin,
        "--policy-pin",
        original.policy_pin,
        "--as-of",
        at.isoformat(),
    ]


def test_cli_pending_does_not_load_future_request(
    original: PredictionSource, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli_args(original, tmp_path, RECORDED)
    missing = tmp_path / "future-not-to-open.json"
    assert (
        main(
            args
            + [
                "--future-capture-root",
                str(tmp_path / "missing"),
                "--future-request",
                str(missing),
                "--future-request-pin",
                "0" * 64,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "pending"
    assert not missing.exists()


def test_cli_verified_and_invalid_json_status(
    original: PredictionSource, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    future = future_capture(original, tmp_path / "future")
    path = tmp_path / "future-request.json"
    path.write_text(future.request.payload_json)
    args = cli_args(original, tmp_path, AS_OF)
    assert (
        main(
            args
            + [
                "--future-capture-root",
                str(future.capture_root),
                "--future-request",
                str(path),
                "--future-request-pin",
                future.request_pin,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    args[args.index("--policy-pin") + 1] = "0" * 64
    assert main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid" and not result["offline_evaluation_eligible"]
