from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError, digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.models.service import EvaluationService, TrainingService
from donghak_stock_vision.signals.dataset import build_dataset, split_dataset
from donghak_stock_vision.storage.analysis import AnalysisStore


def config() -> ExplicitResearchSplit:
    return ExplicitResearchSplit(
        ResearchDateRange(date(2022, 1, 3), date(2023, 12, 28)),
        ResearchDateRange(date(2024, 1, 2), date(2024, 6, 28)),
        ResearchDateRange(date(2024, 7, 1), date(2025, 6, 30)),
    )


def test_contract_round_trip() -> None:
    c = config()
    assert ExplicitResearchSplit.from_dict(c.to_dict()) == c
    assert c.identifier == digest(c.to_dict())
    with pytest.raises(FrozenInstanceError):
        c.training = c.validation  # type: ignore[misc]  # Verify runtime immutability.


@pytest.mark.parametrize("bad", ["reverse", "overlap", "datetime", "version", "missing"])
def test_invalid_contract(bad: str) -> None:
    with pytest.raises(AnalysisError):
        c = config()
        if bad == "reverse":
            ResearchDateRange(date(2024, 1, 2), date(2024, 1, 1))
        elif bad == "overlap":
            replace(c, validation=c.training)
        elif bad == "datetime":
            ResearchDateRange(datetime(2024, 1, 1), date(2024, 1, 2))
        else:
            raw = c.to_dict()
            if bad == "version":
                raw["version"] = 2
            else:
                del raw["final_test"]
            ExplicitResearchSplit.from_dict(raw)


@pytest.mark.parametrize(
    ("day", "end", "part", "purged"),
    [
        ("2022-01-03", "2022-01-10", "train", False),
        ("2023-12-28", "2024-01-01", "train", False),
        ("2023-12-28", "2024-01-02", "train", True),
        ("2024-01-02", "2024-01-09", "validation", False),
        ("2024-06-28", "2024-06-30", "validation", False),
        ("2024-06-28", "2024-07-01", "validation", True),
        ("2024-07-01", "2024-07-08", "test", False),
        ("2025-06-23", "2025-06-30", "test", False),
        ("2025-06-30", "2025-07-01", "test", True),
        ("2023-12-29", "2024-01-05", None, False),
        ("2024-06-29", "2024-07-05", None, False),
        ("2025-07-01", "2025-07-08", None, False),
    ],
)
def test_assignment_and_actual_label_end(
    snapshot: tuple[str, dict[str, Any]],
    day: str,
    end: str,
    part: str | None,
    purged: bool,
) -> None:
    samples, _ = build_dataset(snapshot[1])
    sample = replace(
        samples[0],
        trading_date=date.fromisoformat(day),
        anchor_at=datetime.fromisoformat(day + "T00:00:00+09:00"),
        direction="up",
        label=1,
        label_end=date.fromisoformat(end),
    )
    result = split_dataset([sample], "historical_research", config())
    assert bool(result["purged"]) == purged
    for name, rows in result["partitions"].items():
        assert len(rows) == int(name == part and not purged)
    assert result["outside_range"] == int(part is None)
    # UTC representation of the same Seoul date assigns identically.
    from datetime import UTC

    utc = replace(sample, anchor_at=sample.anchor_at.astimezone(UTC))
    other = split_dataset([utc], "historical_research", config())
    assert [len(x) for x in other["partitions"].values()] == [
        len(x) for x in result["partitions"].values()
    ]


def test_modes_snapshot_and_automatic_regression(snapshot: tuple[str, dict[str, Any]]) -> None:
    samples, _ = build_dataset(snapshot[1])
    assert split_dataset(samples, "historical_research") == split_dataset(
        samples, "historical_research", None
    )
    with pytest.raises(AnalysisError, match="historical_research"):
        split_dataset(samples, "point_in_time", config())
    with pytest.raises(AnalysisError, match="outside_snapshot"):
        config().validate_snapshot(snapshot[1])
    raw = config().to_dict()
    changed = dict(reversed(list(raw.items())))
    assert ExplicitResearchSplit.from_dict(changed).identifier == config().identifier


def test_explicit_artifact_reproduction(
    snapshot: tuple[str, dict[str, Any]],
    analysis: AnalysisStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only deterministic temporary synthetic fixtures; never a production model/DB.
    samples, _ = build_dataset(snapshot[1])
    auto = split_dataset(samples, "historical_research")
    v = datetime.fromisoformat(auto["validation_start"]).date()
    t = datetime.fromisoformat(auto["test_start"]).date()
    days = sorted({s.trading_date for s in samples})
    c = ExplicitResearchSplit(
        ResearchDateRange(days[0], days[days.index(v) - 1]),
        ResearchDateRange(v, days[days.index(t) - 1]),
        ResearchDateRange(t, days[-1]),
    )
    result = TrainingService(analysis).train(snapshot[0], split_configuration=c)
    assert result["status"] == "trained"
    model = analysis.get("model", result["model_version"])
    dataset = analysis.get("dataset", model["dataset_id"])
    assert dataset["split"]["configuration"] == c.to_dict()
    assert model["snapshot_id"] == snapshot[0]
    assert model["split_hash"] == digest(dataset["split"])
    evaluated = EvaluationService(analysis).evaluate(result["model_version"], "historical_research")
    assert evaluated["split"]["configuration_hash"] == c.identifier
    assert split_dataset(samples[::-1], "historical_research", c) == split_dataset(
        samples, "historical_research", c
    )

    # Alter only final-test outcomes: selected parameters and validation remain frozen.
    altered = [
        replace(s, label=1 - s.label)
        if s.trading_date >= c.final_test.start and s.label is not None
        else s
        for s in samples
    ]
    monkeypatch.setattr(
        "donghak_stock_vision.models.service.build_dataset",
        lambda snapshot: (altered, {}),
    )
    second = TrainingService(analysis).train(snapshot[0], split_configuration=c)
    other = analysis.get("model", second["model_version"])
    for direction in ("up", "down"):
        assert (
            other["directions"][direction]["parameters"]
            == model["directions"][direction]["parameters"]
        )
        assert (
            other["directions"][direction]["validation"]
            == model["directions"][direction]["validation"]
        )
