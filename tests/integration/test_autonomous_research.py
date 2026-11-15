"""Offline schema fixtures; real parsers, hashes, fitters and immutable memory."""

import copy
import hashlib
import json
import plistlib
import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_real_market_dataset import (
    request_data as _dataset_request,  # noqa: F401
)

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.autonomous import champion_check, execute
from donghak_stock_vision.models.directional import build, catalog, contract
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import (
    DB,
    LIMITS,
    REQUEST,
    clock,
    confirm,
    context,
    next_experiment,
    prepare,
    status,
    terminals,
    worker,
)
from donghak_stock_vision.storage.autonomous_runner import lock, render_agent, resource_reason, run


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


@pytest.fixture
def request_data(request: pytest.FixtureRequest) -> dict[str, Any]:
    value: dict[str, Any] = request.getfixturevalue("_dataset_request")
    return value


@pytest.fixture
def study(request_data: dict[str, Any], tmp_path: Path) -> tuple[Path, Path, str]:
    d = {k: copy.deepcopy(request_data[k]) for k in ("research", "protocol", "tickers", "cutoff")}
    d.update(version=REQUEST, dataset_version="offline-schema-fixture", horizons=[1])
    root, pins = tmp_path / "study", tmp_path / "independent"
    result = prepare(d, root, pins).to_dict()
    return root, pins, result["plan_id"]


def attempt(root: Path, pin: str) -> str:
    plan, data = context(root, pin)
    with closing(AnalysisStore(root / DB)) as store:
        c = next_experiment(plan, data, terminals(store))
        assert c is not None
        return store.put(
            "autonomous_attempt",
            dict(
                plan_id=pin,
                experiment_id=c["experiment_id"],
                started_at=clock(),
                selection_reason=c["selection_reason"],
                parent_experiment=c["parent_experiment"],
            ),
        )


def test_pinned_source_sealed_poison_and_readonly(
    study: tuple[Path, Path, str], request_data: dict[str, Any]
) -> None:
    root, pins, pin = study
    before = hashlib.sha256(Path(request_data["research"]["database"]).read_bytes()).hexdigest()
    plan, data = context(root, pin)
    assert before == request_data["research"]["pin"]
    assert data["observation_count"] == 3350 and not data["protected_final_test_read"]
    assert not data["goals"]["1"]["readiness"]["failures"]
    assert len(plan["configurations"]) == 12
    assert (pins / "plan.pin").read_text().strip() == pin
    with pytest.raises(ValueError):
        context(root, "0" * 64)
    with closing(sqlite3.connect(root / DB)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM analysis_artifacts")


def test_train_reproduce_dedup_and_model_families(study: tuple[Path, Path, str]) -> None:
    root, _, pin = study
    configurations: list[dict[str, Any]] = []
    for _ in range(3):
        key = attempt(root, pin)
        worker(root, pin, key)
        worker(root, pin, key)
        with closing(ReadOnlyAnalysisStore(root / DB)) as store:
            done = terminals(store)
            latest = next(v for v in done.values() if v["attempt_id"] == key)
            assert latest["state"] == "completed", latest
            result = store.get("autonomous_result", latest["result_id"])
            configurations.append(result["config"])
            if len(configurations) == 1:
                rebuilt, body = execute(context(root, pin)[1], result["config"])
                assert hashlib.sha256(body).hexdigest() == result["model_sha256"]
                assert rebuilt["folds"] == result["folds"]
    assert len({c["candidate"]["family"] for c in configurations}) == 3
    assert status(root, pin).to_dict()["completed"] == 3
    with pytest.raises(ValueError, match="search_not_finished"):
        confirm(root, pin)


def test_continuation_after_interrupted_attempt(
    study: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, pin = study
    attempt(root, pin)  # Persisted before process death; no terminal commit.

    def real_inprocess_worker(root: Path, pin: str, key: str, **kwargs: Any) -> dict[str, Any]:
        worker(root, pin, key)
        return dict(returncode=0, reason=None, maximum_rss_kb=0, wall_seconds=0)

    monkeypatch.setattr(
        "donghak_stock_vision.storage.autonomous_runner.launch", real_inprocess_worker
    )

    def healthy() -> dict[str, Any]:
        return dict(verified=True, ac_power=True, thermal_state=0, memory_free_percent=80)

    first = run(root, pin, 1, resource_probe=healthy).to_dict()
    second = run(root, pin, 1, resource_probe=healthy).to_dict()
    assert first["completed"] == 1 and second["completed"] == 2
    assert second["interrupted_attempts"] == 1 and second["attempts"] == 3
    assert second["network_requests"] == 0 and second["new_market_data_required"] is False
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        done = terminals(store)
        assert len(done) == 2
        chosen = next_experiment(*context(root, pin), done)
        assert chosen is not None and chosen["experiment_id"] not in done


def test_future_labels_never_enter_features_or_cross_split(study: tuple[Path, Path, str]) -> None:
    root, _, pin = study
    plan, data = context(root, pin)
    goal = data["goals"]["1"]
    split = data["split"]
    for sample in goal["samples"]:
        assert sample["label_start"] > sample["trading_date"]
        assert (
            sample["label_end"] <= split["training"]["end"]
            or split["validation"]["start"]
            <= sample["trading_date"]
            <= sample["label_end"]
            <= split["validation"]["end"]
        )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        snap = store.get("autonomous_snapshot", plan["snapshot_id"])
    rows, _, _ = build(snap, 1)
    changed = copy.deepcopy(snap)
    ticker = rows[0].ticker
    changed["rows"][ticker][-1]["close"] += 1
    again, _, _ = build(changed, 1)
    assert rows[0].values == again[0].values
    mutated = copy.deepcopy(data)
    for s in mutated["goals"]["1"]["samples"]:
        if s["trading_date"] >= split["validation"]["start"]:
            s["label"] = 1 - s["label"]
    a, _ = execute(data, plan["configurations"][0]["config"])
    b, _ = execute(mutated, plan["configurations"][0]["config"])
    assert a == b  # Search and model fitting ignore the confirmation labels entirely.


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_separate_finite_label_contracts(horizon: int) -> None:
    c = contract(horizon)
    assert c["horizon"] == horizon and not c["pit_qualified"] and not c["live_forward_compatible"]
    configs = catalog([horizon])
    assert len({digest(c) for c in configs}) == 12
    assert len({c["mask"] for c in configs}) == 4
    with pytest.raises(ValueError):
        contract(2)


@pytest.mark.parametrize(
    "state,reason",
    [
        ({"verified": False}, "resource_probe_unknown"),
        (dict(verified=True, ac_power=False), "on_battery"),
        (dict(verified=True, ac_power=True, thermal_state=2), "thermal_pressure"),
        (
            dict(verified=True, ac_power=True, thermal_state=0, memory_free_percent=1),
            "memory_pressure",
        ),
    ],
)
def test_resource_failure_closed(tmp_path: Path, state: dict[str, Any], reason: str) -> None:
    assert resource_reason(tmp_path, state) == reason


def test_resource_pause_single_process_and_render_only(study: tuple[Path, Path, str]) -> None:
    root, pins, pin = study
    result = run(root, pin, 2, resource_probe=lambda: dict(verified=False)).to_dict()
    assert result["attempts"] == 0 and result["pause_reason"] == "resource_probe_unknown"
    with lock(root, ".runner.lock"), pytest.raises(ValueError, match="already_active"):
        run(root, pin, 1)
    path = root / "research-agent.draft.plist"
    render_agent(root, pins / "plan.pin", path)
    p = plistlib.loads(path.read_bytes())
    assert p["StartInterval"] == 300 and "krx-scheduler" not in json.dumps(p)
    with pytest.raises(ValueError):
        render_agent(root, pins / "plan.pin", path)


def test_champion_strict_gate_and_incumbent_margin() -> None:
    m = dict(balanced_accuracy=0.6, roc_auc=0.65, pr_auc_ap=0.65, brier_score=0.20)
    search = dict(worst_balanced_accuracy=0.55, largest_train_gap=0.05)
    gate: dict[str, Any] = dict(
        prior_cross_study_exposure=False,
        confirmation=dict(
            metrics=m,
            baselines={"training_prior": dict(pr_auc_ap=0.5, brier_score=0.25)},
            cost_scenario=dict(mean_net_event_return=0.01),
            by_quarter={"Q1": m},
            by_ticker={str(i): m for i in range(5)},
        ),
    )
    assert champion_check(search, gate)["promote"]
    assert not champion_check(search, gate, dict(balanced_accuracy=0.59))["promote"]
    gate["confirmation"]["metrics"]["balanced_accuracy"] = 0.5
    assert not champion_check(search, gate)["promote"]


def test_agent_cannot_expand_space_or_change_authority() -> None:
    from donghak_stock_vision.models.research_agent import RuleBasedAgent, select_proposal

    view = dict(
        version="research_agent_view_v1", candidates=[dict(experiment_id="allowed")], history=[]
    )
    assert select_proposal(RuleBasedAgent(), view) == "allowed"

    class OutsideAgent:
        def propose(self, view: dict[str, Any]) -> str:
            return "outside-frozen-plan"

    with pytest.raises(ValueError, match="outside_plan"):
        select_proposal(OutsideAgent(), view)


def test_real_child_timeout_and_memory_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    from donghak_stock_vision.storage import autonomous_runner as runner

    original = subprocess.Popen

    def sleeper(args: Any, **kwargs: Any) -> Any:
        return original([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", sleeper)
    monkeypatch.setattr(runner, "_command", lambda args: "1")
    monkeypatch.setitem(LIMITS, "timeout_seconds", 0.05)
    result = runner.launch(tmp_path, "unused", "timeout-test")
    assert result["reason"] == "worker_timeout" and result["returncode"] != 0
    monkeypatch.setitem(LIMITS, "timeout_seconds", 300)
    monkeypatch.setattr(runner, "_command", lambda args: str(2048 * 1024))
    result = runner.launch(tmp_path, "unused", "rss-test")
    assert result["reason"] == "worker_memory_limit" and result["returncode"] != 0


def test_confirmation_failure_consumes_gate_without_reopening(
    study: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, pin = study
    worker(root, pin, attempt(root, pin))
    plan, _ = context(root, pin)
    with closing(AnalysisStore(root / DB)) as store:
        done = terminals(store)
        for c in plan["configurations"]:
            if c["experiment_id"] not in done:
                store.put(
                    "autonomous_terminal",
                    dict(
                        experiment_id=c["experiment_id"],
                        config=c["config"],
                        state="failed",
                        reason="injected_interruption",
                    ),
                )

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected failure after gate consumption")

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_research.execute", fail)
    confirm(root, pin)
    confirm(root, pin)
    current = status(root, pin).to_dict()
    assert current["gates_consumed"] == 1 and current["confirmation_failures"] == 1
    assert current["confirmations"] == 0 and not current["research_champions"]


def test_checkpoint_tampering_rejected(study: tuple[Path, Path, str]) -> None:
    root, _, pin = study
    worker(root, pin, attempt(root, pin))
    checkpoint = next((root / "models").glob("*.checkpoint"))
    checkpoint.write_bytes(b"not the frozen model")
    with pytest.raises(ValueError, match="checkpoint_integrity"):
        status(root, pin)


def test_atomic_memory_rollback(study: tuple[Path, Path, str]) -> None:
    root, _, pin = study
    with closing(AnalysisStore(root / DB)) as store:
        with pytest.raises(TypeError):
            store.put_many([("rollback_probe", dict(value=1)), ("invalid", dict(value=object()))])
        assert not store.all("rollback_probe")
    assert status(root, pin).to_dict()["attempts"] == 0


def test_cli_never_loads_operating_credentials(
    study: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision import cli

    root, pins, _ = study

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("operating dotenv must not be read")

    monkeypatch.setattr(cli, "load_dotenv", forbidden)
    assert (
        cli.main(
            [
                "autonomous-research",
                "status",
                "--root",
                str(root),
                "--plan-pin-file",
                str(pins / "plan.pin"),
            ]
        )
        == 0
    )
