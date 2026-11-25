"""Actual original validators/fitter; deterministic transport is not Gemma evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_gemma_research import healthy
from tests.integration.test_gemma_research import view as _view  # noqa: F401
from tests.integration.test_memory_guided_research import Backend, original

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import grounded_memory as policy
from donghak_stock_vision.models import research_campaign as analysis
from donghak_stock_vision.storage import autonomous_runner as runner
from donghak_stock_vision.storage import memory_guided_research as owner
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, worker
from donghak_stock_vision.storage.autonomous_research import context as original_context
from donghak_stock_vision.storage.grounded_evidence import catalog, diagnostics

agent_view = _view


def grounded_view(v: dict[str, Any]) -> dict[str, Any]:
    v = copy.deepcopy(v)
    scope = dict(
        dataset=digest("dataset"),
        source=digest("source"),
        cohort="fixture",
        horizon=1,
        stock_policy="pooled",
        search_end="2025-12-31",
    )
    h = dict(
        experiment_id=digest("original"),
        config=v["candidates"][1]["config"],
        state="completed",
        mean_ap=0.4,
        mean_ba=0.5,
        mean_brier=0.25,
        baseline_ap_gain=-0.02,
        calibration_delta=0.01,
        overfit_gap=0.2,
        worst_balanced_accuracy=0.4,
        internal_folds=[],
        scope=scope,
    )
    h["content_hash"] = digest(h)
    v.update(
        history=[h], cohort="fixture", evidence_scope=scope, counterfactual_required=True, budget=1
    )
    return v


def response(p: dict[str, Any]) -> dict[str, Any]:
    e, h = next(iter(p["citations"].items()))
    c = next(k for k, v in p["candidates"].items() if policy.differences(v["config"], h["config"]))
    return dict(
        choice=c,
        cite=e,
        claim=policy.claims(h)[0],
        test=policy.differences(p["candidates"][c]["config"], h["config"])[0],
        ranking=[c],
    )


def test_alias_ownership_claim_parent_scope_and_compression(agent_view: dict[str, Any]) -> None:
    v = grounded_view(agent_view)
    c = policy.pack(v, "verified")
    d = policy.pack(v, "compact")
    assert c["citations"] == d["citations"]
    assert len(policy.context(d)[1]) < len(policy.context(c)[1])
    proposal = policy.resolve(response(c), c, v)
    assert proposal["parentExperimentId"] == v["history"][0]["experiment_id"]
    assert proposal["evidence"][0]["experimentId"] == proposal["parentExperimentId"]
    assert response(c)["cite"].startswith("e_") and "0" not in c["citations"]
    for key, value, reason in [
        ("cite", "0", "citation"),
        ("claim", "BASELINE_REFERENCE", "metric_owner"),
        ("test", "inexistent", "parent"),
        ("choice", "forged", "schema"),
        ("ranking", [{}], "schema"),
    ]:
        with pytest.raises(ValueError, match=reason):
            policy.resolve(response(c) | {key: value}, c, v)
    bad = copy.deepcopy(v)
    bad["history"][0]["mean_ap"] = 0.99
    with pytest.raises(ValueError, match="retrieval"):
        policy.pack(bad, "compact")
    bad = copy.deepcopy(v)
    bad["evidence_scope"]["cohort"] = "foreign"
    with pytest.raises(ValueError, match="retrieval"):
        policy.pack(bad, "compact")
    with pytest.raises(ValueError, match="insufficient_evidence"):
        policy.resolve(
            dict(choice="ABSTAIN", cite="NONE", claim="NONE", test="NONE", ranking=[]), c, v
        )


def test_representative_retrieval_dedup_and_preoutcome_ranking(agent_view: dict[str, Any]) -> None:
    v = grounded_view(agent_view)
    a = v["history"][0]
    rows = [a]
    for name, gain, cal in [("success", 0.02, -0.01), ("neutral", 0, 0.01)]:
        h = a | dict(experiment_id=digest(name), baseline_ap_gain=gain, calibration_delta=cal)
        h["content_hash"] = digest({k: val for k, val in h.items() if k != "content_hash"})
        rows.append(h)
    selected, coverage = policy.select(rows + [a], v["candidates"])
    assert len(selected) == 3 and all(n["selected"] == 1 for n in coverage["categories"].values())
    assert policy.select(list(reversed(rows)), v["candidates"])[0] == selected
    with pytest.raises(ValueError, match="retrieval"):
        policy.select(rows + [a | dict(mean_ap=0.9)], v["candidates"])
    rank = policy.value_rows(v["candidates"], rows)
    assert rank[1]["exact_duplicate"] and not rank[1]["information_value"]
    assert all(
        r["prospective_performance"] == "UNKNOWN"
        and r["peer_cache_knowledge"] == "UNKNOWN_UNSELECTED_PEERS"
        for r in rank
    )
    # Identical known failures under different source IDs are not a novel hypothesis.
    repeated = [a, a | dict(experiment_id=digest("other failure"))]
    near = copy.deepcopy(v["candidates"][1])
    near["config"]["candidate"]["value"] = 999
    assert policy.value_rows([near], repeated)[0]["information_value"] == 0
    empty = v | dict(history=[])
    with pytest.raises(ValueError, match="insufficient_evidence"):
        policy.pack(empty, "compact")
    assert policy.failure_class("llm_inference_timeout") == "Inference Timeout"
    assert policy.failure_class("llm_loading_headroom") == "Resource Unavailable"


class GroundedBackend(Backend):
    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        if "choice" not in schema["properties"]:
            value = json.loads(super().generate(prompt, schema))
            if value["evidence"]:
                value["parentExperimentId"] = value["evidence"][0]["experimentId"]
            return json.dumps(value)
        self.metadata = dict(inference_calls=1, maximum_rss_kb=100)
        summary = json.loads(prompt.split("\n", 1)[1])
        e, h = next(iter(summary["evidence"].items()))
        key, c = next(
            (k, c)
            for k, c in summary["candidates"].items()
            if c["m"] != h["mask"] or c["f"] != h["family"]
        )
        test = "mask_contrast" if c["m"] != h["mask"] else "family_contrast"
        return json.dumps(dict(choice=key, cite=e, claim=h["claims"][0], test=test, ranking=[key]))


def test_five_arm_real_fitter_catalog_and_legacy_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req, parent, db = original(tmp_path)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0),
    )

    def launch(root: Path, pin: str, attempt: str, **kw: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr(runner, "launch", launch)
    pin = (tmp_path / "original-pins" / "plan.pin").read_text().strip()
    runner.run(parent, pin, 1, resource_probe=healthy)
    scope, rows = catalog(parent, pin, 1, "pooled")
    assert len(rows) == 1 and rows[0]["model_sha256"] and rows[0]["mean_brier"] >= 0
    assert all(f["period"]["end"] <= scope["search_end"] for r in rows for f in r["internal_folds"])
    assert not rows[0]["chronology_authenticated"]
    with closing(ReadOnlyAnalysisStore(parent / DB)) as original_store:
        terminal = original_store.get("autonomous_terminal", rows[0]["terminal_hash"])
    assert rows[0]["parent_experiment_id"] == terminal["parent_experiment"]
    assert rows[0]["execution_started_at"] == terminal["started_at"]
    assert rows[0]["execution_finished_at"] == terminal["finished_at"]
    # Reuse only verified immutable context; original checkpoint/rows are re-read.
    replay: dict[str, Any] = {}
    real_context = original_context
    replay_calls = 0

    def tracked_context(*args: Any, **kwargs: Any) -> Any:
        nonlocal replay_calls
        replay_calls += 1
        return real_context(*args, **kwargs)

    monkeypatch.setattr(owner, "context", tracked_context)
    owner.evidence(parent, pin, extended=True, _verified=replay)
    owner.evidence(parent, pin, extended=True, _verified=replay)
    assert replay_calls == 1
    checkpoint = next((parent / "models").glob("*.checkpoint"))
    body = checkpoint.read_bytes()
    try:
        checkpoint.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="checkpoint_integrity"):
            owner.evidence(parent, pin, extended=True, _verified=replay)
    finally:
        checkpoint.write_bytes(body)
    # A fabricated JSON claim of prior verification cannot create a cache entry.
    for value in replay.values():
        value["owner"] = None
    with pytest.raises(ValueError, match="snapshot_changed"):
        owner.evidence(parent, pin, extended=True, _verified=replay)
    monkeypatch.setattr(owner, "context", real_context)

    protected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (db, parent / DB)}
    req["version"] = owner.GROUNDED_VERSION
    root = tmp_path / "grounded"
    pins = tmp_path / "grounded-pins"
    session_pin = owner.prepare(req, root, pins)
    frozen = owner.load(root, session_pin)
    assert tuple(frozen["arms"]) == policy.ARMS
    monkeypatch.setattr(owner, "OllamaProvider", GroundedBackend)
    real = runner.run
    monkeypatch.setattr(
        owner, "run_worker", lambda *a, **kw: real(*a, **kw, resource_probe=healthy)
    )
    for arm in policy.ARMS:
        value = owner.run(root, session_pin, arm)
        assert value["arms"][arm]["completed"] == 1
        before = digest(value)
        assert digest(owner.run(root, session_pin, arm)) == before
    assert sum(a["completed"] for a in value["arms"].values()) == 5
    pub = owner.publication(value)
    assert len(pub["hypotheses"]) == 5 and pub["version"] == "memory_guided_publication_v3"
    encoded = json.dumps(pub)
    assert str(tmp_path) not in encoded and frozen["source_pin"] not in encoded
    for arm in ("verified", "compact", "value_ranked"):
        d = diagnostics(owner.arm_root(root, arm))
        assert d["records"][0]["citationValid"] and d["records"][0]["parentValid"]
        assert d["inferenceSeconds"] is None  # provider supplied no measured segment
    with closing(ReadOnlyAnalysisStore(owner.arm_root(root, "compact") / DB)) as store:
        assert len(store.all("grounded_request_reservation")) == 1
    assert protected == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    sched = analysis.schedule([dict(cell_id=digest(i)) for i in range(6)], 8, policy.ARMS)
    assert len(sched) == 240 and len({r["step_id"] for r in sched}) == 240
    assert len(analysis.analyze([], 8, policy.ARMS)["comparisons"]) == 10


def test_window_requires_separate_approval_never_resets_and_publishes_five_arms(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from donghak_stock_vision.storage import research_campaign as campaign
    from donghak_stock_vision.storage.analysis import AnalysisStore

    # This is an execution-gate fixture, not a provider/source verification substitute.
    root = tmp_path / "campaign"
    root.mkdir(mode=0o700)
    cells = [
        dict(
            cell_id=digest(i),
            cohort="fixture" + str(i // 3),
            horizon=(1, 5, 20)[i % 3],
            root=str(tmp_path / f"child{i}"),
            pin=None,
            effective=0,
            status="BLOCKED",
            reason="fixture_source_unavailable",
        )
        for i in range(6)
    ]
    d = dict(
        version=campaign.GROUNDED_VERSION,
        request=dict(
            candidate_budget=1,
            wall_seconds=900,
            llm_budget=24,
            calibration_steps=3,
            execution_steps=30,
        ),
        cells=cells,
        schedule=analysis.schedule(cells, 1, policy.ARMS),
        registered_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        comparison_policy=analysis.POLICY,
        cohort_overlap=dict(
            shared_tickers=0,
            calendar_periods_overlap=True,
            independent=False,
            reason="shared_market_dates_and_source_archive",
        ),
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    (root / "campaign.json").write_text(json.dumps(d))
    pin = digest(d)
    with closing(AnalysisStore(root / DB)) as store:
        store.put("research_campaign_plan", d)
    actions: list[Callable[[], Any]] = [
        lambda: campaign.run(root, pin),
        lambda: campaign.start(root, pin),
    ]
    for action in actions:
        with pytest.raises(ValueError, match="execution_approval_required"):
            action()
    w = campaign.start(root, pin, approved=True)
    assert w == campaign.start(root, pin, approved=True)
    assert w["started_at"] != d["registered_at"] and w["max_steps"] == 30
    assert campaign.execution_start(root, d) == datetime.fromisoformat(w["started_at"])
    report = campaign.report(root, pin)
    pub = campaign.publication(report)
    assert pub["version"] == "research_campaign_publication_v3" and pub["plannedCells"] == 30
    assert len(pub["progress"]["unexecuted"]) == 30
    bad = copy.deepcopy(pub)
    bad["plannedCells"] = 18
    with pytest.raises(ValueError):
        campaign.validate_publication(bad)
    with closing(AnalysisStore(root / DB)) as store:
        store.put("research_campaign_execution_window", w | dict(max_wall_seconds=901))
    with pytest.raises(ValueError, match="execution_approval_required"):
        campaign.execution_start(root, d)


def test_duplicate_request_reserved_before_call_and_no_rule_fallback(
    tmp_path: Path, agent_view: dict[str, Any]
) -> None:
    from donghak_stock_vision.models.llm_provider import ModelConfig
    from donghak_stock_vision.storage.analysis import AnalysisStore
    from donghak_stock_vision.storage.grounded_evidence import GroundedAgent

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
    root = tmp_path / "agent"
    root.mkdir()
    with closing(AnalysisStore(root / DB)):
        pass

    class Rejecting(Backend):
        calls = 0

        def generate(self, prompt: str, schema: dict[str, Any]) -> str:
            self.calls += 1
            raise ValueError("llm_inference_timeout")

    provider = Rejecting(config)
    v = grounded_view(agent_view)
    for expected in ("inference_timeout", "duplicate_request"):
        with pytest.raises(ValueError, match=expected):
            GroundedAgent(root, provider, "compact").propose(v)
    assert provider.calls == 1
    d = diagnostics(root)
    assert [r["failureClass"] for r in d["records"]].count("Duplicate Candidate") == 1
    assert d["validProposals"] == 0 and d["records"][0]["citationValid"] is None
    assert "prompt" not in json.dumps(d).replace("promptBytes", "").replace("promptCharacters", "")


def test_transport_timeout_retains_measured_cost_and_releases_only_owned_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import httpx
    from tests.integration.test_model_management import LocalTransport, config

    from donghak_stock_vision.storage.llm_providers import OllamaProvider

    backend = LocalTransport()

    def timeout(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if "prompt" in body:
            time.sleep(0.02)
            raise httpx.ReadTimeout("test-only timeout")
        return backend(request)

    monkeypatch.setattr(
        "donghak_stock_vision.storage.llm_providers.probe",
        lambda: healthy() | dict(physical_memory_mb=16384),
    )

    def observed() -> dict[str, Any]:
        return dict(verified=True, rss_kb=100000, cpu_percent=10.0, pids=[])

    monkeypatch.setattr("donghak_stock_vision.storage.llm_providers.process_resources", observed)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0, cpu_percent=0.0, pids=[]),
    )
    provider = OllamaProvider(config(), httpx.MockTransport(timeout))
    try:
        with pytest.raises(ValueError, match="llm_inference_timeout"):
            provider.generate("bounded test", {})
        assert provider.unloaded and backend.loaded is None
        assert provider.metadata["inference_calls"] == 1
        assert provider.metadata["maximum_rss_kb"] == 100000
        assert provider.metadata["load_seconds"] >= 0
        assert provider.metadata["inference_seconds"] >= 0
        assert provider.metadata["unload_seconds"] >= 0
        assert provider.metadata.get("timings") is None
        assert policy.failure_class("llm_transport_failed") == "Resource Unavailable"
    finally:
        provider.close()


@pytest.mark.parametrize("prompt_tokens,accepted", [(1000, True), (3967, True), (4015, False)])
def test_reported_token_headroom_fails_closed_without_increasing_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_tokens: int,
    accepted: bool,
) -> None:
    from dataclasses import replace

    import httpx
    from tests.integration.test_model_management import LocalTransport, config

    from donghak_stock_vision.storage.ai_model_management import contract_probe
    from donghak_stock_vision.storage.llm_providers import OllamaProvider
    from donghak_stock_vision.storage.llm_research_agent import LLMResearchAgent

    backend = LocalTransport()

    def response(request: httpx.Request) -> httpx.Response:
        result = backend(request)
        body = json.loads(request.content) if request.content else {}
        if "prompt" in body:
            value = result.json() | dict(prompt_eval_count=prompt_tokens, eval_count=80)
            return httpx.Response(200, json=value)
        return result

    monkeypatch.setattr(
        "donghak_stock_vision.storage.llm_providers.probe",
        lambda: healthy() | dict(physical_memory_mb=16384),
    )
    monkeypatch.setattr(
        "donghak_stock_vision.storage.llm_providers.process_resources",
        lambda: dict(verified=True, rss_kb=100000, cpu_percent=10.0, pids=[]),
    )
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0, cpu_percent=0.0, pids=[]),
    )
    provider = OllamaProvider(
        replace(config(), max_output_tokens=128), httpx.MockTransport(response)
    )
    agent = LLMResearchAgent(tmp_path, provider)
    if accepted:
        assert agent.propose(contract_probe())
    else:
        with pytest.raises(ValueError, match="llm_context_headroom_insufficient"):
            agent.propose(contract_probe())
        assert policy.failure_class(agent.audit["reason"]) == "Resource Unavailable"
    assert provider.unloaded and backend.loaded is None
    assert provider.metadata["timings"]["prompt_eval_count"] == prompt_tokens
    assert provider.metadata["context_reservation_fits"] is accepted
    assert provider.config.max_output_tokens == 128 and provider.config.timeout == 240
