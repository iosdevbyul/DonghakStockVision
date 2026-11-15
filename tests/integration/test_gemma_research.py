"""Deterministic local-transport tests; real parsing, hashes, fitting and memory."""

import copy
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.integration.test_autonomous_research import request_data  # noqa: F401
from tests.integration.test_autonomous_research import study as _original_study  # noqa: F401
from tests.integration.test_real_market_dataset import (
    request_data as _dataset_request,  # noqa: F401
)

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.directional import catalog
from donghak_stock_vision.models.research_agent import select_proposal, validate_gemma_proposal
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import (
    DB,
    context,
    next_experiment,
    terminals,
    worker,
)
from donghak_stock_vision.storage.autonomous_runner import run
from donghak_stock_vision.storage.gemma_memory import derive, memory_view
from donghak_stock_vision.storage.gemma_research import (
    MODEL,
    Gemma4ResearchAgent,
    Ollama,
    prompt_view,
    recover,
)

DIGEST = "b" * 64


def healthy() -> dict[str, Any]:
    return dict(verified=True, ac_power=True, thermal_state=0, memory_free_percent=70)


@pytest.fixture
def view() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = [
        dict(experiment_id=digest(c), config=c) for c in catalog([1], "agent_masks_v1")
    ]
    return dict(
        version="research_agent_view_v1",
        dataset_version="fixture",
        goal=candidates[0]["config"]["goal"],
        candidates=candidates,
        history=[],
        leader=None,
        budget=12,
    )


def proposal(view: dict[str, Any]) -> dict[str, Any]:
    c = view["candidates"][0]
    v = c["config"]
    return dict(
        proposalVersion="gemma_research_proposal_v1",
        experimentId=c["experiment_id"],
        hypothesis="Explore reduced causal feature combinations",
        modelFamily=v["candidate"]["family"],
        featureSet=v["mask"],
        hyperparameters=dict(value=v["candidate"]["value"], seed=42),
        researchGoal=v["goal"]["label_version"],
        parentExperimentId=None,
        expectedImprovement="Hypothesis only",
        rationale="Compare within frozen training folds",
    )


def backend(view: dict[str, Any], bad: bool = False, unload_failure: bool = False) -> Ollama:
    loaded = False

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal loaded
        assert str(request.url).startswith("http://127.0.0.1:11434/api/")
        if request.url.path == "/api/version":
            return httpx.Response(200, json=dict(version="0.32.15"))
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json=dict(models=[dict(name=MODEL, digest=DIGEST, size=4587156505)])
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json=dict(models=[dict(name=MODEL, digest=DIGEST, size_vram=0)] if loaded else []),
            )
        body = json.loads(request.content)
        assert body["model"] == MODEL
        if body.get("keep_alive") == 0:
            loaded = unload_failure
            return httpx.Response(200, json=dict(done=True))
        loaded = True
        if "prompt" not in body:
            return httpx.Response(200, json=dict(done=True))
        assert (
            body["options"]["num_thread"] == 1
            and body["options"]["num_gpu"] == 0
            and body["think"] is False
        )
        value = proposal(view)
        if bad:
            value["modelFamily"] = "arbitrary-shell-code"
        return httpx.Response(
            200,
            json=dict(
                model=MODEL,
                done=True,
                response=json.dumps(value),
                eval_count=100,
                eval_duration=1000000000,
            ),
        )

    return Ollama(httpx.MockTransport(respond))


@pytest.fixture(autouse=True)
def resources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.probe", healthy)
    monkeypatch.setattr("donghak_stock_vision.storage.research_resources.probe", healthy)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=100000, cpu_percent=1.0, pids=[]),
    )


def test_actual_transport_contract_and_unload(tmp_path: Path, view: dict[str, Any]) -> None:
    root = tmp_path / "study"
    root.mkdir(mode=0o700)
    agent = Gemma4ResearchAgent(root, DIGEST, backend(view))
    assert select_proposal(agent, view) == view["candidates"][0]["experiment_id"]
    assert agent.unloaded and agent.audit["state"] == "accepted"
    assert agent.audit["local_http_requests"] >= 7
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        assert len(store.all("gemma_request")) == len(store.all("gemma_proposal")) == 1
    agent = Gemma4ResearchAgent(root, DIGEST, backend(view, bad=True))
    with pytest.raises(ValueError, match="substitution"):
        select_proposal(agent, view)
    assert agent.unloaded and agent.audit["state"] == "rejected"


@pytest.mark.parametrize(
    "field,value",
    [
        ("experimentId", "already-completed"),
        ("modelFamily", "unapproved"),
        ("featureSet", "future-return"),
        ("hyperparameters", {"value": True, "seed": 42}),
        ("parentExperimentId", "not-a-parent"),
        ("proposalVersion", "unsupported"),
        ("hypothesis", "x" * 601),
    ],
)
def test_schema_and_ownership_fail_closed(view: dict[str, Any], field: str, value: Any) -> None:
    p = proposal(view)
    p[field] = value
    with pytest.raises(ValueError):
        validate_gemma_proposal(p, view)


def test_unknown_fields_and_mutating_agent_cannot_expand_plan(view: dict[str, Any]) -> None:
    p = proposal(view)
    p["shellCommand"] = "never execute"
    with pytest.raises(ValueError):
        validate_gemma_proposal(p, view)
    before = copy.deepcopy(view)

    class MutatingAgent:
        def propose(self, v: dict[str, Any]) -> str:
            v["candidates"].append(dict(experiment_id="forged"))
            return "forged"

    with pytest.raises(ValueError):
        select_proposal(MutatingAgent(), view)
    assert view == before


def test_memory_prompt_does_not_export_secrets_or_confirmation(view: dict[str, Any]) -> None:
    view.update(
        database="/private/path",
        source_pin="SECRET",
        confirmation={"score": 1},
        KRX_API_KEY="SECRET",
    )
    encoded = json.dumps(prompt_view(view))
    assert (
        "SECRET" not in encoded and "/private/path" not in encoded and "confirmation" not in encoded
    )


def test_no_remote_redirects_or_network_proxy(view: dict[str, Any]) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "https://outside.invalid"})
    )
    client = Ollama(transport)
    with pytest.raises(ValueError, match="http_error"):
        client.request("/api/version")
    with pytest.raises(ValueError, match="allowlist"):
        client.request("https://outside.invalid")
    client.close()


@pytest.fixture
def study(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, Path, str, Path, str]:
    parent, _, old_pin = request.getfixturevalue("_original_study")
    root = tmp_path / "gemma"
    pins = tmp_path / "gemma-pins"
    pin = derive(parent, old_pin, root, pins, DIGEST)
    return root, pins, pin, parent, old_pin


def test_separate_versioned_study_old_history_preserved(
    study: tuple[Path, Path, str, Path, str],
) -> None:
    root, _, pin, parent, oldpin = study
    plan, data = context(root, pin)
    assert (
        plan["version"] == "autonomous_plan_v2"
        and context(parent, oldpin)[0]["version"] == "autonomous_plan_v1"
    )
    assert len(plan["configurations"]) == 12 and not data["protected_final_test_read"]
    oldc = context(parent, oldpin)[0]["configurations"]
    assert not {digest(c["config"]) for c in plan["configurations"]} & {
        digest(c["config"]) for c in oldc
    }
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        choice = next_experiment(plan, data, terminals(store))
        assert choice is not None
        v = memory_view(root, plan, data, {}, choice)
    assert v["budget"] == 12


def test_real_training_fallback_restart_and_cache(
    study: tuple[Path, Path, str, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, _, pin, parent, oldpin = study
    from donghak_stock_vision.storage.gemma_research import Gemma4ResearchAgent

    calls = []

    def failed(self: Any, view: dict[str, Any]) -> str:
        self.unloaded = True
        raise ValueError("gemma_fixture_unavailable")

    monkeypatch.setattr(Gemma4ResearchAgent, "propose", failed)

    def launch(root: Path, pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(attempt)
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=1, wall_seconds=0.1)

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.launch", launch)
    one = run(root, pin, 1, agent="gemma4", resource_probe=healthy).to_dict()
    two = run(root, pin, 1, agent="rule", resource_probe=healthy).to_dict()
    assert one["trained_this_run"] == 1 and two["completed"] == 2
    assert one["selections_this_run"][0]["state"] == "fallback"
    peer = tmp_path / "rule"
    peerpins = tmp_path / "rule-pins"
    peerpin = derive(parent, oldpin, peer, peerpins, DIGEST)
    cached = run(peer, peerpin, 1, reuse=(root, pin), resource_probe=healthy).to_dict()
    assert (
        cached["cache_hits_this_run"] == 1 and cached["trained_this_run"] == 0 and len(calls) == 2
    )
    assert not cached["research_champions"] and not cached["performance_approved"]


def test_failed_unload_prevents_training(
    study: tuple[Path, Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.storage.gemma_research import Gemma4ResearchAgent

    root, _, pin, _, _ = study

    def failed(self: Any, view: dict[str, Any]) -> str:
        self.unloaded = False
        raise ValueError("gemma_timeout")

    monkeypatch.setattr(Gemma4ResearchAgent, "propose", failed)
    result = run(root, pin, 1, agent="gemma4", resource_probe=healthy).to_dict()
    assert result["pause_reason"] == "gemma_release_unverified" and result["attempts"] == 0


def test_interrupted_owned_proposal_release_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(AnalysisStore(tmp_path / DB)) as store:
        store.put("gemma_request", dict(started_at="retained"))
    monkeypatch.setattr(Ollama, "release", lambda self, digest: True)
    assert recover(tmp_path, DIGEST) and recover(tmp_path, DIGEST)
    with closing(ReadOnlyAnalysisStore(tmp_path / DB)) as store:
        assert len(store.all("gemma_recovery")) == 1


@pytest.mark.parametrize(
    "text", ['{"experimentId":"a","experimentId":"b"}', '{"value": NaN}', "[]"]
)
def test_nonfinite_or_duplicate_json_rejected(text: str) -> None:
    from donghak_stock_vision.models.research_agent import decode_gemma_proposal

    with pytest.raises(ValueError):
        decode_gemma_proposal(text)


def test_timeout_fallback_only_with_verified_release(tmp_path: Path, view: dict[str, Any]) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout")

    agent = Gemma4ResearchAgent(tmp_path, DIGEST, Ollama(httpx.MockTransport(timeout)))
    with pytest.raises(ValueError):
        agent.propose(view)
    assert agent.unloaded and agent.audit["state"] == "rejected"


def test_known_other_model_cannot_be_assumed_unloaded() -> None:
    client = Ollama(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=dict(models=[dict(name="unrelated", digest="c" * 64)])
            )
        )
    )
    assert not client.release(DIGEST)
    client.close()


def test_release_failure_is_not_success(
    tmp_path: Path, view: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.storage.gemma_research import LIMITS

    monkeypatch.setitem(LIMITS, "unload_seconds", 0)
    agent = Gemma4ResearchAgent(tmp_path, DIGEST, backend(view, unload_failure=True))
    agent.propose(view)
    assert not agent.unloaded


def test_low_memory_prevents_model_load(
    tmp_path: Path, view: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "donghak_stock_vision.storage.research_resources.probe",
        lambda: healthy() | dict(memory_free_percent=30),
    )
    agent = Gemma4ResearchAgent(tmp_path, DIGEST, backend(view))
    with pytest.raises(ValueError, match="loading_headroom"):
        agent.propose(view)
    assert agent.unloaded and agent.audit["inference_calls"] == 0


def test_rule_mode_blocks_orphaned_llm(
    study: tuple[Path, Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, pin, _, _ = study
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=3 * 1024**2, cpu_percent=100.0, pids=[123]),
    )
    result = run(root, pin, 1, resource_probe=healthy).to_dict()
    assert (
        result["pause_reason"] == "ollama_release_resources_unverified" and result["attempts"] == 0
    )


def test_empty_api_is_not_native_resource_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=3 * 1024**2, cpu_percent=0.0, pids=[123]),
    )
    client = Ollama(httpx.MockTransport(lambda request: httpx.Response(200, json=dict(models=[]))))
    assert not client.release(DIGEST)
    client.close()
