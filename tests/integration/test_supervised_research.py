"""Checkpoint-first recovery and the finite manual supervisor budget."""

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import autonomous_strategy as policy
from donghak_stock_vision.storage import autonomous_runner, predictive_research
from donghak_stock_vision.storage import autonomous_strategy as r4
from donghak_stock_vision.storage import supervised_research as owner
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB


def test_exact_prepared_checkpoint_is_loaded_without_refitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin, population = digest("plan"), digest([])
    cfg = dict(candidate={"family": "random_forest"})
    experiment = digest(dict(plan_id=pin, config=cfg, train=population))
    prepared: dict[str, Any] = dict(
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


def test_closed_window_can_seed_another_pinned_plan(tmp_path: Path) -> None:
    from donghak_stock_vision.storage.autonomous_research import LIMITS
    from donghak_stock_vision.storage.krx_backfill import write_new

    original = tmp_path / "prior"
    original.mkdir(mode=0o700)
    archive = original / DB
    with closing(AnalysisStore(archive)) as s:
        plan: dict[str, Any] = dict(
            version="supervised_research_plan_v1",
            bounds=LIMITS,
            max_worker_seconds=60,
            request=dict(schedule=[1, 5], max_seconds=60, max_failures=2),
            r4_hash=digest("R4"),
            **predictive_research.FLAGS,
        )
        pin = s.put("supervised_plan", plan)
        key = s.put(
            "supervised_decision",
            dict(index=0, candidate={"experiment_id": digest("used")}),
        )
        original_trial = dict(experiment_id=digest("trial"), state="completed")
        s.put("supervised_completed", dict(decision=key, trial=original_trial))
        window = s.put("supervised_window", dict(plan_id=pin))
    archive.chmod(0o600)
    pin_path = tmp_path / "plan.pin"
    write_new(pin_path, pin)
    ref = dict(root=str(original), plan_pin_file=str(pin_path))
    child = tmp_path / "child"
    with pytest.raises(ValueError, match="supervised_parent_not_closed"):
        owner._inherit(child, ref, plan["r4_hash"])
    with closing(AnalysisStore(archive)) as s:
        s.put("supervised_close", dict(window=window))
    inherited = owner._inherit(child, ref, plan["r4_hash"])
    assert inherited["plan_id"] == pin and inherited["candidate_ids"] == [digest("used")]
    assert inherited["trials"] == [original_trial]


def test_prescribed_slot_preserves_original_choice_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, pinned = ({"experiment_id": digest(key)} for key in ("old", "diagnostic"))
    experiment = digest("evidence")
    control = {
        "experiment_id": experiment,
        "config": {"goal": {"horizon": 1}},
        "state": "completed",
    }
    plan: dict[str, Any] = {
        "pools": {"1": [base, pinned]},
        "development_tickers": ["000001", "000002"],
        "inherited": {},
        "registered_at": "2026-11-28T00:00:00Z",
        "r2_feedback_id": digest("feedback"),
        "r1_origin_ids": [],
        "r4_result_id": digest("R4"),
    }
    slot = dict(
        horizon=1,
        original_candidate_id=base["experiment_id"],
        candidate_id=pinned["experiment_id"],
        evidence_experiment_ids=[experiment],
        reason="defer near duplicate",
        hypothesis="weekly transfer",
    )
    state: dict[str, Any] = {"decisions": [], "completed": {}}
    monkeypatch.setattr(r4, "_memory", lambda *args: [control])
    monkeypatch.setattr(predictive_research, "_control", lambda *args: control)
    monkeypatch.setattr(policy, "choose", lambda *args: (base, {"referenced": [experiment]}))
    assert owner._select(plan, digest("plan"), 1, 0, state, tmp_path, [], [])["candidate"] == base
    with pytest.raises(ValueError, match="supervised_diagnostic_source_binding"):
        owner._bind_diagnostics(
            plan,
            [slot | {"evidence_experiment_ids": [digest("missing")]}],
            tmp_path,
            plan,
            [control],
            [],
        )
    with pytest.raises(ValueError, match="supervised_diagnostic_original_choice"):
        owner._bind_diagnostics(
            plan, [slot | {"original_candidate_id": digest("stale")}], tmp_path, plan, [control], []
        )
    owner._bind_diagnostics(plan, [slot], tmp_path, plan, [control], [])
    selection = owner._select(plan, digest("plan"), 1, 0, state, tmp_path, [], [])
    assert selection["candidate"] == pinned
    assert selection["rationale"]["diagnostic"] == slot | {
        "policy": owner.DIAGNOSTIC_POLICY,
        "selected_at": plan["registered_at"],
    }
    assert selection["r2_feedback_id"] == plan["r2_feedback_id"]
    assert not selection["latest_changed_choice"]
    assert owner._select(plan, digest("plan"), 1, 2, state, tmp_path, [], [])["candidate"] == base


@pytest.mark.parametrize("fail_first", [False, True])
def test_diagnostic_approval_persists_across_run_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_first: bool
) -> None:
    root = tmp_path / "study"
    root.mkdir()
    with closing(AnalysisStore(root / DB)):
        pass
    pin = digest("plan")
    plan: dict[str, Any] = dict(
        code={},
        diagnostics=[{}, {}],
        max_worker_seconds=60,
        request=dict(schedule=[1, 5] * 7, max_failures=2, max_seconds=300),
    )
    monkeypatch.setattr(owner, "_plan", lambda *args: plan)
    monkeypatch.setattr(owner, "provenance", dict)
    monkeypatch.setattr(owner, "probe", dict)
    monkeypatch.setattr(owner, "resource_reason", lambda *args: None)
    launches = []

    def launch(_root: Path, _pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        index = owner._ledger(root)["attempts"][attempt]["index"]
        launches.append(index)
        owner._store(
            root,
            "completed",
            dict(decision=digest(index), trial=dict(state="failed" if fail_first else "completed")),
        )
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=1.0)

    monkeypatch.setattr(autonomous_runner, "launch", launch)
    with pytest.raises(ValueError, match="supervised_step_bound"):
        owner.run(root, pin, 3)
    first = owner.run(root, pin, 2)
    assert first["reason"] == (
        "diagnostic_slot_failed" if fail_first else "requested_steps_complete"
    )
    second = owner.run(root, pin, 1)
    assert second["reason"] == (
        "diagnostic_slot_failed" if fail_first else "diagnostic_approval_exhausted"
    )
    assert launches == ([0] if fail_first else [0, 1])


def test_diagnostic_recovery_requires_a_signed_checkpoint_before_evaluation() -> None:
    plan: dict[str, Any] = {"diagnostics": [{}, {}]}
    state: dict[str, Any] = dict(
        completed={},
        attempts={"first": {"index": 0, "checkpoint_only": True}},
        decisions=[("decision", {"index": 0})],
        checkpoints={"decision": {}},
        audited={"first": {"returncode": 0, "reason": None}},
    )
    assert owner._diagnostic_stop(plan, state, 0) is None
    state["attempts"]["first"]["checkpoint_only"] = False
    assert owner._diagnostic_stop(plan, state, 0) == "diagnostic_attempt_requires_review"
    state["attempts"]["first"]["checkpoint_only"] = True
    state["attempts"]["resume"] = {"index": 0, "checkpoint_only": False}
    assert owner._diagnostic_stop(plan, state, 0) == "diagnostic_attempt_requires_review"
