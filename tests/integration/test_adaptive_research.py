"""New research contracts exercised with actual validators and fitters, offline."""

import copy
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_autonomous_research import (
    request_data as _research_request,  # noqa: F401
)
from tests.integration.test_gemma_research import healthy
from tests.integration.test_real_market_dataset import (
    request_data as _dataset_request,  # noqa: F401
)

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.research import FEATURES_V2, closed_period, vector
from donghak_stock_vision.models.experiment import Candidate
from donghak_stock_vision.models.research_v2 import catalog, folds, rank
from donghak_stock_vision.storage.adaptive_research_report import report
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, confirm, context, prepare, worker
from donghak_stock_vision.storage.autonomous_runner import run


def test_expanded_source_fit_restart_and_seal(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_data = request.getfixturevalue("_research_request")
    d = {k: copy.deepcopy(request_data[k]) for k in ("research", "protocol", "tickers", "cutoff")}
    d.update(
        version="autonomous_direction_source_v2",
        dataset_version="offline-v2-schema",
        horizons=[1, 5, 20],
    )
    root, pins = tmp_path / "adaptive", tmp_path / "pins"
    before = Path(d["research"]["database"]).read_bytes()
    pin = prepare(d, root, pins).to_dict()["plan_id"]
    plan, data = context(root, pin)
    assert plan["experiment_budget"] == 24 and len(plan["configurations"]) == 294
    assert all(not g["readiness"]["failures"] for g in data["goals"].values())
    assert len({digest(c["config"]) for c in plan["configurations"]}) == 294
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        snapshot = store.get("autonomous_snapshot", plan["snapshot_id"])
    bars = snapshot_bars(snapshot, sorted(snapshot["rows"])[0])
    history = bars[:100]
    cutoff = max(b.collected_at for b in history)
    expected = vector(history, snapshot, cutoff)
    assert len(expected) == len(FEATURES_V2) == 46
    partial = history[:-1] + [replace(history[-1], volume=history[-1].volume + 100)]
    assert vector(partial, snapshot, cutoff)[-12:] == expected[-12:]
    for period in ("week", "month"):
        assert closed_period(history, period)[-1].trading_date < history[-1].trading_date
    with pytest.raises(ValueError, match="not_available"):
        vector(history, snapshot, cutoff - timedelta(days=10000))
    changed = copy.deepcopy(snapshot)
    for field in ("open", "high", "low", "close"):
        changed["rows"][history[-1].ticker][-1][field] *= 2
    assert vector(snapshot_bars(changed, history[-1].ticker)[:100], changed, cutoff) == expected

    def launch(root: Path, pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.launch", launch)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0, cpu_percent=0),
    )
    first = run(root, pin, 1, resource_probe=healthy).to_dict()
    second = run(root, pin, 2, resource_probe=healthy).to_dict()
    assert first["completed"] == 1 and second["completed"] == 3 and not second["failed"]
    r = report(root, pin)
    assert len({s["experiment_id"] for s in r["selections"]}) == 3
    assert all(
        "adaptation" in s and s["adaptation"]["selected_without_memory"] for s in r["selections"]
    )
    assert not r["protected_final_test_read"] and not r["confirmation_read"]
    assert (
        not r["pit_qualified"] and not r["performance_approved"] and not r["execution_authorized"]
    )
    assert before == Path(d["research"]["database"]).read_bytes()
    with pytest.raises(ValueError, match="confirmation_sealed"):
        confirm(root, pin)
    with pytest.raises(ValueError):
        context(root, "0" * 64)
    assert folds(snapshot, ExplicitResearchSplit.from_dict(data["split"]), 20)


def test_history_changes_selection_and_failure_penalty() -> None:
    choices = [dict(experiment_id=str(i), config=c) for i, c in enumerate(catalog([1])[:14])]
    initial = rank(choices, [])[0]
    history = [
        dict(
            experiment_id="old",
            config=initial["config"],
            state="completed",
            stability_ap=0.1,
            largest_train_gap=0.7,
        )
    ]
    adapted = rank(choices, history)[0]
    assert adapted["experiment_id"] != initial["experiment_id"]
    assert rank(choices, history) == rank(choices, copy.deepcopy(history))


@pytest.mark.parametrize(
    "parameters",
    [
        {"solver": "exec", "class_weight": "balanced"},
        {"n_estimators": 1000000},
        {"shell": "whoami"},
    ],
)
def test_parameter_space_cannot_expand(parameters: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Candidate("bad", "linear", 1.0, parameters)
