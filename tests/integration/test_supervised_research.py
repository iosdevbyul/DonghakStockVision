"""Checkpoint-first recovery and the finite manual supervisor budget."""

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage import autonomous_runner, predictive_research
from donghak_stock_vision.storage import supervised_research as owner
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB


def test_exact_prepared_checkpoint_is_loaded_without_refitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin, population = digest("plan"), digest([])
    cfg = dict(candidate={"family": "random_forest"})
    experiment = digest(dict(plan_id=pin, config=cfg, train=population))
    prepared = dict(
        checkpoint=f"models/{experiment}.checkpoint",
        model_id=digest("checkpoint"),
        training_population=population,
        evaluation_population=population,
        cache_source=None,
        config=cfg,
        fit_seconds=1.25,
    )
    loaded = []

    def estimator(path: Path, model_id: str, family: str, versions: dict[str, str]) -> object:
        loaded.append((path, model_id, family, versions))
        return object()

    def forbid(*unused: Any) -> Any:
        pytest.fail("already durable model must not refit")

    monkeypatch.setattr(predictive_research, "verified_estimator", estimator)
    monkeypatch.setattr(predictive_research, "_fit", forbid)
    result = predictive_research._trial(
        tmp_path,
        {"code": {"versions": {}}},
        pin,
        cfg,
        [],
        ([], [], []),
        {},
        None,
        "supervised_research_trial",
        prepared=prepared,
        checkpoint_ready=lambda _: True,
    )
    assert result["state"] == "checkpointed" and result["fit_seconds"] == 1.25
    assert loaded == [
        (tmp_path / prepared["checkpoint"], prepared["model_id"], "random_forest", {})
    ]


def test_missing_supervisor_audit_counts_as_failure_and_retries_its_unfinished_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    with closing(AnalysisStore(root / DB)):
        pass
    pin = digest("plan")
    plan = dict(
        code={},
        request=dict(schedule=[1, 5], max_failures=2, max_seconds=90),
        max_worker_seconds=60,
    )
    window = owner._store(root, "window", dict(plan_id=pin, started_at="ignored"))
    previous = owner._store(
        root,
        "attempt",
        dict(
            plan_id=pin,
            window=window,
            index=0,
            allocated_seconds=20,
        ),
    )
    monkeypatch.setattr(owner, "_plan", lambda *args: plan)
    monkeypatch.setattr(owner, "provenance", dict)
    monkeypatch.setattr(owner, "probe", dict)
    monkeypatch.setattr(owner, "resource_reason", lambda *args: None)

    def launch(_root: Path, _pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        state = owner._ledger(root)
        assert state["attempts"][attempt]["index"] == 0
        assert state["audited"][previous]["reason"] == "supervisor_interrupted"
        assert 0 < kwargs["timeout_seconds"] <= 60
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=2.0)

    monkeypatch.setattr(autonomous_runner, "launch", launch)
    owner.run(root, pin, 1)
    state = owner._ledger(root)
    seconds, failures = owner._usage(state)
    assert state["closures"][window]["reason"] == "supervisor_interrupted"
    assert seconds >= 22 and failures == 1
