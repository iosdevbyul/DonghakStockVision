"""Real contract/source/fitting tests; deterministic transport is not Gemma evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_gemma_research import healthy, proposal
from tests.integration.test_gemma_research import view as _original_view  # noqa: F401
from tests.integration.test_real_market_training import source

from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.models import memory_reasoning as policy
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage import autonomous_runner as runner
from donghak_stock_vision.storage import memory_guided_research as owner
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, prepare, worker
from donghak_stock_vision.storage.stock_universe import inspect

agent_view = _original_view


def test_hypothesis_schema_identity_and_evidence(agent_view: dict[str, Any]) -> None:
    v = agent_view | dict(cohort="fixture")
    p = (
        proposal(v)
        | policy.FIXED
        | dict(
            horizon=1,
            cohort="fixture",
            problem="Overfit gap",
            failureCondition="No baseline gain",
            evidence=[],
        )
    )
    from donghak_stock_vision.storage.gemma_research import proposal_schema

    assert policy.schema(v, proposal_schema(v))["properties"]["evidence"]["maxItems"] == 0
    assert policy.validate(p, v) == p["experimentId"]
    old = digest(p)
    for k, val in (
        ("horizon", True),
        ("cohort", "other"),
        ("baseline", "Final Test"),
        ("evidence", [dict(experimentId="forged", metric="mean_ap")]),
        ("shell", "forbidden"),
    ):
        with pytest.raises(ValueError):
            policy.validate(p | {k: val}, v)
    assert digest(p) == old
    history = [dict(experiment_id=digest("original"), mean_ap=0.4)]
    with pytest.raises(ValueError, match="missing_reasoning"):
        policy.validate(p, v | dict(history=history))
    constrained = policy.schema(
        v | dict(history=history), proposal_schema(v | dict(history=history))
    )
    assert constrained["properties"]["evidence"]["minItems"] == 1
    assert constrained["properties"]["evidence"]["maxItems"] == 1
    p["evidence"] = [dict(experimentId=history[0]["experiment_id"], metric="mean_ap")]
    assert policy.validate(p, v | dict(history=history))
    assert policy.hypothesis_id(p, "session", "gemma_blind") != policy.hypothesis_id(
        p, "session", "gemma_memory"
    )


def original(tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    src = source(tmp_path / "source", 1280, universe_size=10)
    import sqlite3

    with closing(sqlite3.connect(src.database)) as db:
        days = [
            r[0] for r in db.execute("SELECT DISTINCT trading_date FROM bars ORDER BY trading_date")
        ]

    # Fixture provider dates are consecutive observed sessions, explicitly test-only.
    def span(a: int, b: int) -> dict[str, str]:
        return dict(start=days[a], end=days[b])

    protocol = dict(
        split=dict(
            version=1, training=span(0, 449), validation=span(450, 509), final_test=span(510, 569)
        ),
        research_calendar=dict(
            version=1,
            reason="known_incomplete_trading_date",
            action="exclude_research_session",
            incomplete_dates=[],
        ),
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        horizon_sessions=5,
    )
    pp = tmp_path / "protocol.json"
    pp.write_text(json.dumps(protocol))
    d: dict[str, Any] = dict(
        version="autonomous_historical_source_v1",
        dataset_version="offline-memory",
        research=dict(
            database=str(src.database),
            pin=hashlib.sha256(src.database.read_bytes()).hexdigest(),
            source_contract="receipt_archive_v1",
        ),
        protocol=dict(path=str(pp), sha256=hashlib.sha256(pp.read_bytes()).hexdigest()),
        tickers=list(src.tickers),
        cutoff=src.cutoff.isoformat(),
        horizons=[1],
        historical=dict(
            windows=[span(0, 449), span(570, 1279)],
            search_end=days[1079],
            evaluations=[span(780, 879), span(880, 979), span(980, 1079)],
            diagnostic=span(1080, 1279),
            policy_version="historical_robust_lexicographic_v1",
            references=[],
        ),
    )
    inv = inspect(d)
    ip = tmp_path / "inventory.json"
    ip.write_text(json.dumps(inv))
    d["multi_stock"] = dict(
        heldout_tickers=list(src.tickers[-2:]),
        budget=12,
        policy="adaptive",
        inventory=dict(path=str(ip), pin=digest(inv)),
        cohort="fixture",
    )
    root = tmp_path / "original"
    pins = tmp_path / "original-pins"
    prepare(d, root, pins)
    ref = dict(root=str(root), plan_pin_file=str(pins / "plan.pin"))
    config = ModelConfig(
        "gemma",
        "ollama",
        "gemma4:e2b",
        "b" * 64,
        "fixture",
        "http://127.0.0.1:11434",
        structured_output=True,
        json_schema=True,
    )
    from dataclasses import asdict

    req = dict(
        version=owner.VERSION,
        source=ref,
        memory=[ref],
        horizon=1,
        stock_policy="pooled",
        budget=1,
        model=asdict(config),
    )
    return req, root, src.database


class Backend:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.unloaded = True
        self.metadata: dict[str, Any] = {}

    def discover(self) -> dict[str, Any]:
        return dict(installed=True, configured=True, reachable=True)

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        v = json.loads(prompt.split("\n", 1)[1])
        c = v["candidates"][1]
        result = dict(
            proposalVersion="gemma_research_proposal_v1",
            experimentId=c["id"],
            hypothesis="Lower variance candidate",
            modelFamily=c["family"],
            featureSet=c["featureSet"],
            hyperparameters=dict(value=c["value"], seed=42),
            researchGoal=v["goal"],
            parentExperimentId=None,
            expectedImprovement="Baseline improvement hypothesis",
            rationale="Frozen internal evidence",
            horizon=v["horizon"],
            cohort=v["cohort"],
            problem="Uncertain baseline gain",
            failureCondition="No baseline gain",
            evidence=[],
            **policy.FIXED,
        )
        if v["history"]:
            h = v["history"][0]
            result["evidence"] = [dict(experimentId=h["id"], metric="mean_ap")]
        self.metadata = dict(inference_calls=1, maximum_rss_kb=100)
        return json.dumps({k: result[k] for k in schema["required"]})

    def close(self) -> None:
        pass


def test_finite_abc_real_fit_feedback_recovery_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req, parent, db = original(tmp_path)
    protected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (db, parent / DB)}
    root = tmp_path / "session"
    pins = tmp_path / "pins"
    pin = owner.prepare(req, root, pins)
    monkeypatch.setattr(owner, "OllamaProvider", Backend)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0),
    )
    real_run = runner.run
    monkeypatch.setattr(
        owner, "run_worker", lambda *a, **kw: real_run(*a, **kw, resource_probe=healthy)
    )

    def launch(root: Path, pin: str, attempt: str, **kw: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr(runner, "launch", launch)
    # Feedback can be rebuilt after interruption, without another proposal or fit.
    real_feedback = owner.feedback
    calls = 0

    def interrupted(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("after worker commit")
        real_feedback(*args)

    monkeypatch.setattr(owner, "feedback", interrupted)
    with pytest.raises(RuntimeError):
        owner.run(root, pin, "rule")
    monkeypatch.setattr(owner, "feedback", real_feedback)
    result = owner.run(root, pin, "rule")
    assert result["arms"]["rule"]["completed"] == 1
    for arm in ("gemma_blind", "gemma_memory"):
        result = owner.run(root, pin, arm)
    assert all(a["completed"] == 1 for a in result["arms"].values())
    assert sum(a["actual_python_trainings"] for a in result["arms"].values()) == 2
    assert sum(a["reused"] for a in result["arms"].values()) == 1
    assert len(result["feedback"]) == 3
    for arm in policy.ARMS:
        assert owner.run(root, pin, arm) == result
    pub = owner.validate_publication(owner.publication(result))
    assert "/tmp/" not in json.dumps(pub)
    changed = copy.deepcopy(pub)
    changed["model"]["privatePath"] = "/private/secret"
    with pytest.raises(ValueError):
        owner.validate_publication(changed)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in protected.items())
    from donghak_stock_vision.storage.research_lab_publication import ResearchLabPublication

    output = tmp_path / "publication"
    output.mkdir(mode=0o700)
    p = output / "memory.json"
    p.write_text(json.dumps(pub))
    p.chmod(0o600)
    index = output / "index.json"
    index.write_text(
        json.dumps(
            dict(
                version="research_lab_publication_index_v2",
                entries=[],
                memoryReports=[
                    dict(report="memory.json", sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                ],
            )
        )
    )
    index.chmod(0o600)
    reader = ResearchLabPublication(output, hashlib.sha256(index.read_bytes()).hexdigest())
    assert reader.report_catalog()["total"] == 1 and reader.report(pin) == pub
    with closing(ReadOnlyAnalysisStore(owner.arm_root(root, "gemma_blind") / DB)) as store:
        assert all(not v["summary"]["history"] for _, v in store.all("llm_request"))


def test_cli_import_and_pinned_session_fail_closed(tmp_path: Path) -> None:
    from donghak_stock_vision.cli import parser

    assert (
        parser()
        .parse_args(
            ["memory-research", "report", "--root", str(tmp_path), "--report", str(tmp_path / "r")]
        )
        .command
        == "memory-research"
    )
    p = tmp_path / "session.json"
    p.write_text(json.dumps(dict(version=owner.VERSION)))
    with pytest.raises(ValueError, match="session_pin"):
        owner.load(tmp_path, "0" * 64)


def test_untrusted_proposal_rejected_without_execution(
    tmp_path: Path, agent_view: dict[str, Any]
) -> None:
    v = agent_view | dict(cohort="fixture")
    config = ModelConfig(
        "test",
        "ollama",
        "gemma4:e2b",
        "b" * 64,
        "test",
        "http://127.0.0.1:11434",
        structured_output=True,
        json_schema=True,
    )

    class Forged(Backend):
        def generate(self, prompt: str, schema: dict[str, Any]) -> str:
            value = json.loads(super().generate(prompt, schema))
            value["modelFamily"] = "execute shell"
            return json.dumps(value)

    agent = owner.MemoryAgent(tmp_path, Forged(config))
    with pytest.raises(ValueError, match="configuration_substitution"):
        agent.propose(v)
    with closing(ReadOnlyAnalysisStore(tmp_path / DB)) as store:
        assert store.all("llm_proposal")[0][1]["state"] == "rejected"
        assert store.all("autonomous_attempt") == []
