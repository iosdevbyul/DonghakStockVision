from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from donghak_stock_vision.data.learning import AnalysisError, Direction, Sample
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.models import experiment as exp
from donghak_stock_vision.storage.analysis import AnalysisStore


def samples(start: date, end: date) -> list[Sample]:
    result = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            for i in range(8):
                direction: Direction = "up" if i < 4 else "down"
                result.append(
                    Sample(
                        f"{i:06d}",
                        day,
                        datetime.combine(day, datetime.min.time(), UTC),
                        (0.01, 0.02, -0.03 if i < 4 else 0.03, 0.01, 0.02, 0.03, 0.1, 0.2),
                        direction,
                        i % 2,
                        day,
                        day,
                        None,
                        "labeled",
                        "fixture",
                        (day,),
                    )
                )
        day += timedelta(days=1)
    return result


def folds() -> tuple[exp.Fold, ...]:
    start = date(2022, 1, 3)
    return tuple(
        exp.Fold(ResearchDateRange(start, train_end), ResearchDateRange(begin, end))
        for train_end, begin, end in (
            (date(2023, 3, 31), date(2023, 4, 1), date(2023, 6, 30)),
            (date(2023, 6, 30), date(2023, 7, 1), date(2023, 9, 30)),
            (date(2023, 9, 30), date(2023, 10, 1), date(2023, 12, 28)),
        )
    )


def test_five_candidates_and_fixed_hgb() -> None:
    assert len(exp.CANDIDATES) == 5
    rows = samples(date(2023, 1, 2), date(2023, 2, 1))
    for candidate in exp.CANDIDATES[3:]:
        fitted = exp.fit_candidate(candidate, rows)
        assert fitted.early_stopping is False
        assert fitted.random_state == 42
        assert fitted.max_leaf_nodes == candidate.value
        assert fitted.n_features_in_ == 8
        assert exp.predict_candidate(candidate, fitted, rows) == exp.predict_candidate(
            candidate, exp.fit_candidate(candidate, rows), rows
        )
    with pytest.raises(AnalysisError):
        exp.Candidate("unapproved", "linear", 100)


def test_snapshot_rejected_before_label_generation(
    snapshot: tuple[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = deepcopy(snapshot[1])
    permitted = ResearchDateRange(date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"]))

    def forbidden(_: Any) -> Any:
        pytest.fail("must reject before feature/label generation")

    monkeypatch.setattr(exp, "build_dataset", forbidden)
    raw["rows"][next(iter(raw["rows"]))][0]["trading_date"] = "2099-01-01"
    with pytest.raises(AnalysisError, match="beyond_permitted"):
        exp.bounded_samples(raw, permitted)


def test_purge_and_conditional_population() -> None:
    rows = samples(date(2023, 3, 30), date(2023, 4, 4))
    rows[0] = replace(rows[0], label_end=date(2023, 4, 1))
    train, evaluation, removed = exp.purged_fold(rows, folds()[0], "up")
    assert removed == 1
    assert all(s.direction == "up" and s.label_end < date(2023, 4, 1) for s in train)  # type: ignore[operator]
    assert all(s.direction == "up" for s in evaluation)
    assert rows[0] not in train


def test_development_uses_only_past_and_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fit(candidate: exp.Candidate, rows: list[Sample]) -> Any:
        calls.append((candidate.name, max(s.trading_date for s in rows)))
        assert len({s.direction for s in rows}) == 1
        return max(s.trading_date for s in rows)

    def predict(candidate: exp.Candidate, fitted: Any, rows: list[Sample]) -> list[float]:
        assert all(s.trading_date > fitted for s in rows)
        return [0.7 if s.label else 0.3 for s in rows]

    monkeypatch.setattr(exp, "fit_candidate", fit)
    monkeypatch.setattr(exp, "predict_candidate", predict)
    span = ResearchDateRange(date(2022, 1, 3), date(2023, 12, 28))
    rows = samples(span.start, span.end)
    first = exp.develop(rows, span, folds())
    assert len(calls) == 30
    assert first == exp.develop(rows, span, folds())
    assert first["directions"]["up"]["selected"] == "linear_c01"
    with pytest.raises(AnalysisError, match="boundary"):
        exp.develop(rows + samples(date(2024, 1, 2), date(2024, 1, 2)), span, folds())
    with pytest.raises(AnalysisError, match="folds"):
        exp.develop(rows, span, folds()[::-1])


def test_stability_selection() -> None:
    assert (
        exp.select_candidate(
            [
                {"candidate": "unstable", "mean_ap": 0.6, "stability_ap": 0.2},
                {"candidate": "stable", "mean_ap": 0.5, "stability_ap": 0.45},
            ]
        )
        == "stable"
    )


def test_locked_validation_loads_once_and_checks_models(
    analysis: AnalysisStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fitted = {"up": {"fixture": 1}, "down": {"fixture": 2}}
    lock = {
        "validation": {"start": "2024-01-02", "end": "2024-06-28"},
        "research_calendar": None,
        "directions": {
            d: {
                "candidate": exp.CANDIDATES[0].to_dict(),
                "estimator_hash": exp.estimator_hash(model),
                "approved_baseline_validation_ap": 0.4,
            }
            for d, model in fitted.items()
        },
    }
    lock_id = analysis.put("experiment_lock", lock)
    calls = []

    def loader() -> Any:
        calls.append(True)
        assert analysis.all("experiment_validation_started")
        return {"research_calendar": None}

    rows = samples(date(2024, 1, 2), date(2024, 6, 28))
    monkeypatch.setattr(exp, "bounded_samples", lambda snapshot, span: (rows, {}))
    monkeypatch.setattr(
        exp, "predict_candidate", lambda c, m, rows: [0.7 if s.label else 0.3 for s in rows]
    )
    with pytest.raises(AnalysisError, match="locked_estimator"):
        exp.qualify_locked(analysis, lock_id, {"up": {}, "down": {}}, loader)
    assert not calls
    result = exp.qualify_locked(analysis, lock_id, fitted, loader)
    assert len(calls) == 1
    assert result["operational_status"] == "unregistered"
    assert result["final_test_accessed"] is False
    with pytest.raises(AnalysisError, match="already_consumed"):
        exp.qualify_locked(analysis, lock_id, fitted, loader)
    assert len(calls) == 1
