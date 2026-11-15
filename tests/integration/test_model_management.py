"""Authenticated controls and injected transports; genuine validators/fitters stay active."""

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_autonomous_research import request_data  # noqa: F401
from tests.integration.test_autonomous_research import study as _original_study  # noqa: F401
from tests.integration.test_gemma_research import healthy, proposal
from tests.integration.test_real_market_dataset import (
    request_data as _dataset_request,  # noqa: F401
)

from donghak_stock_vision.ai_model_routes import Principal, ScopedBearerAuthority
from donghak_stock_vision.api import create_app
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.models.research_agent import select_proposal
from donghak_stock_vision.storage.ai_model_management import ModelManagement, contract_probe
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, worker
from donghak_stock_vision.storage.autonomous_runner import run
from donghak_stock_vision.storage.gemma_memory import derive
from donghak_stock_vision.storage.llm_providers import ProviderFactory
from donghak_stock_vision.storage.llm_research_agent import LLMResearchAgent

TOKEN = "test-control-token-0000000000000000000"
VIEWER = "test-viewer-token-00000000000000000000"


def config(key: str = "gemma", model: str = "gemma4:e2b", revision: str = "b" * 64) -> ModelConfig:
    return ModelConfig(
        key,
        "ollama",
        model,
        revision,
        model,
        "http://127.0.0.1:11434",
        structured_output=True,
        json_schema=True,
        thinking_supported=model == "gemma4:e2b",
        estimated_memory_mb=6144,
    )


class LocalTransport:
    def __init__(self) -> None:
        self.loaded: str | None = None
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=dict(version="0.32.15"))
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json=dict(
                    models=[
                        dict(name="gemma4:e2b", digest="b" * 64, size=4587156505),
                        dict(name="gemma2:latest", digest="c" * 64, size=5443152417),
                        dict(name="unknown:latest", digest="d" * 64, size=200),
                    ]
                ),
            )
        if request.url.path == "/api/show":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json=dict(
                    capabilities=["completion", "thinking"]
                    if body["model"] == "gemma4:e2b"
                    else ["completion"]
                ),
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json=dict(
                    models=[]
                    if self.loaded is None
                    else [
                        dict(
                            name=self.loaded,
                            digest=("b" if self.loaded == "gemma4:e2b" else "c") * 64,
                            size_vram=0,
                        )
                    ]
                ),
            )
        body = json.loads(request.content)
        self.calls.append(body)
        if body.get("keep_alive") == 0:
            self.loaded = None
            return httpx.Response(200, json=dict(done=True))
        self.loaded = body["model"]
        if "prompt" not in body:
            return httpx.Response(200, json=dict(done=True))
        v = contract_probe()
        p = proposal(v)
        # Select the actual allowed configuration described by schema/prompt.
        c = json.loads(body["prompt"].split("\n", 1)[1])["candidates"][0]
        p.update(
            experimentId=c["id"],
            modelFamily=c["family"],
            featureSet=c["featureSet"],
            hyperparameters=dict(value=c["value"], seed=42),
        )
        return httpx.Response(
            200, json=dict(model=body["model"], done=True, response=json.dumps(p))
        )


@pytest.fixture(autouse=True)
def resources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "donghak_stock_vision.storage.llm_providers.probe",
        lambda: healthy() | dict(physical_memory_mb=16384),
    )
    monkeypatch.setattr(
        "donghak_stock_vision.storage.llm_providers.process_resources",
        lambda: dict(verified=True, rss_kb=100000, cpu_percent=50.0, pids=[]),
    )
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=100000, cpu_percent=0.0, pids=[]),
    )


@pytest.fixture
def service(tmp_path: Path) -> tuple[ModelManagement, LocalTransport]:
    backend = LocalTransport()
    s = ModelManagement(
        tmp_path / "control",
        [config(), config("other", "gemma2:latest", "c" * 64)],
        "gemma",
        ProviderFactory(transport=httpx.MockTransport(backend)),
    )
    return s, backend


def client_for(service: ModelManagement) -> TestClient:
    authority = ScopedBearerAuthority(
        [
            (TOKEN, Principal("owner", frozenset({"ai:read", "ai:manage"}))),
            (VIEWER, Principal("viewer", frozenset({"ai:read"}))),
        ]
    )
    return TestClient(create_app(ai_management=service, ai_authorize=authority))


def headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def test_registry_auth_capability_and_legacy_routes(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    c = client_for(s)
    assert c.get("/api/v1/health").status_code == 200
    assert c.get("/api/v1/ai/models").status_code == 401
    assert c.get("/api/v1/ai/models", headers=headers("wrong")).status_code == 401
    assert (
        c.get("/api/v1/ai/models", headers={"Authorization": "bearer " + TOKEN}).status_code == 200
    )
    values = c.get("/api/v1/ai/models", headers=headers()).json()["items"]
    assert len(values) == 3 and values[-1]["validationStatus"] == "not_registered"
    assert values[-1]["available"] is False
    assert values[0]["selected"] and values[0]["validationStatus"] == "not_validated"
    assert values[0]["generationSettings"]["contextWindow"] == 4096
    assert "endpoint" not in json.dumps(values) and "credential_env" not in json.dumps(values)
    assert (
        c.post(
            "/api/v1/ai/models/validate?wait=true",
            headers=headers(VIEWER),
            json={"modelKey": "gemma"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/ai/models/validate?wait=true",
            headers=headers(),
            json={"modelKey": "gemma", "endpoint": "https://bad.invalid"},
        ).status_code
        == 422
    )


def test_real_adapter_validation_selection_history_and_restart(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, b = service
    c = client_for(s)
    before = c.get("/api/v1/ai/models/active", headers=headers()).json()
    assert before["selectedModelKey"] == "gemma" and before["lastApplied"] is None
    receipt = c.post(
        "/api/v1/ai/models/validate?wait=true", headers=headers(), json={"modelKey": "other"}
    ).json()
    assert receipt["valid"] and not receipt["researchStarted"] and not receipt["modelChanged"]
    assert b.loaded is None
    response = c.put(
        "/api/v1/ai/models/active",
        headers=headers(),
        json=dict(
            modelKey="other",
            validationId=receipt["validationId"],
            expectedSelectionVersion=0,
            approve=True,
        ),
    )
    assert response.status_code == 200 and response.json()["selectedModelKey"] == "other"
    assert response.json()["lastApplied"] is None
    restarted = ModelManagement(s.root, list(s.configs.values()), "gemma", s.factory)
    assert restarted.active()["selectedModelKey"] == "other"
    assert restarted.history()[0]["actor"] == "owner"
    with restarted.lease() as chosen:
        assert chosen.model_id == "gemma2:latest"
        assert (
            c.put(
                "/api/v1/ai/models/active",
                headers=headers(),
                json=dict(
                    modelKey="other",
                    validationId=receipt["validationId"],
                    expectedSelectionVersion=1,
                    approve=True,
                ),
            ).status_code
            == 409
        )
    assert s.active()["selectionVersion"] == 1
    assert not any("think" in v for v in b.calls if v.get("model") == "gemma2:latest")


def test_reject_unvalidated_conflicting_or_unapproved_changes(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    c = client_for(s)
    base = dict(modelKey="other", validationId="0" * 64, expectedSelectionVersion=0, approve=True)
    assert c.put("/api/v1/ai/models/active", headers=headers(), json=base).status_code == 422
    r = c.post(
        "/api/v1/ai/models/validate?wait=true", headers=headers(), json={"modelKey": "other"}
    ).json()
    assert (
        c.put(
            "/api/v1/ai/models/active",
            headers=headers(),
            json=base | dict(validationId=r["validationId"], approve=False),
        ).status_code
        == 422
    )
    assert (
        c.put(
            "/api/v1/ai/models/active",
            headers=headers(),
            json=base | dict(validationId=r["validationId"], expectedSelectionVersion=3),
        ).status_code
        == 409
    )
    assert s.active()["selectionVersion"] == 0 and len(s.history()) == 3


@pytest.mark.parametrize(
    "changes",
    [
        dict(endpoint="http://outside.invalid"),
        dict(cpu_threads=8),
        dict(context_window=100000),
        dict(max_memory_mb=20000),
        dict(thinking=True, thinking_supported=False),
    ],
)
def test_configuration_limits(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(config(), **changes)


def test_unsupported_capability_and_remote_transfer_denied(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    with pytest.raises(ValueError):
        replace(config(), json_schema=None).require_execution()
    remote = ModelConfig(
        "remote",
        "openai_compatible",
        "reviewed-model",
        "reviewed-revision",
        "Remote",
        "https://approved.invalid/v1",
        execution_policy="remote",
        credential_env="TEST_REMOTE_KEY",
        structured_output=True,
        json_schema=True,
    )
    factory = ProviderFactory(
        credential=lambda name: "secret-not-for-logs",
        transport=httpx.MockTransport(lambda request: pytest.fail("unapproved remote call")),
    )
    provider = factory.create(remote)
    with pytest.raises(ValueError, match="transfer_not_approved"):
        provider.generate("summary", {})
    provider.close()


def test_openai_compatible_adapter_di_and_common_validator(tmp_path: Path) -> None:
    remote = ModelConfig(
        "remote",
        "openai_compatible",
        "reviewed-model",
        "reviewed-revision",
        "Remote",
        "https://approved.invalid/v1",
        execution_policy="remote",
        credential_env="TEST_REMOTE_KEY",
        structured_output=True,
        json_schema=True,
        remote_enabled=True,
        approved_transfer_scope="bounded_research_summary_v1",
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://approved.invalid/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert "Source PIN" not in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json=dict(
                model="reviewed-model",
                choices=[
                    dict(
                        finish_reason="stop",
                        message=dict(content=json.dumps(proposal(contract_probe()))),
                    )
                ],
            ),
        )

    agent = LLMResearchAgent(
        tmp_path,
        ProviderFactory(
            credential=lambda name: "test-secret", transport=httpx.MockTransport(respond)
        ).create(remote),
    )
    assert (
        select_proposal(agent, contract_probe())
        == contract_probe()["candidates"][0]["experiment_id"]
    )
    assert agent.unloaded and agent.audit["state"] == "accepted"
    with closing(ReadOnlyAnalysisStore(tmp_path / DB)) as store:
        assert "test-secret" not in json.dumps(store.all("llm_request") + store.all("llm_proposal"))


def test_existing_storage_cannot_be_used_as_control(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    (root / "original").write_bytes(b"preserved")
    with pytest.raises(ValueError):
        ModelManagement(root, [config()], "gemma")
    assert list(root.iterdir()) == [root / "original"]


def test_managed_engine_real_fit_and_comparison_record(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, _, oldpin = request.getfixturevalue("_original_study")
    root = tmp_path / "managed-study"
    pins = tmp_path / "pins"
    pin = derive(parent, oldpin, root, pins, "b" * 64, managed=True)
    backend = LocalTransport()
    s = ModelManagement(
        tmp_path / "control",
        [config()],
        "gemma",
        ProviderFactory(transport=httpx.MockTransport(backend)),
    )
    receipt = s.validate("gemma", "owner")
    assert receipt["valid"]

    def launch(root: Path, pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.launch", launch)
    with s.lease() as selected:
        result = run(
            root,
            pin,
            1,
            agent="managed",
            resource_probe=healthy,
            agent_factory=lambda root, plan: s.agent_for(root, selected),
        ).to_dict()
        s.record_run(root, pin, selected, result, 1)
    assert result["trained_this_run"] == 1 and result["failed"] == 0
    assert context(root, pin)[0]["version"] == "autonomous_plan_v3"
    assert s.active()["lastApplied"]["model_key"] == "gemma"
    assert len(s.comparisons()) == 1 and not s.comparisons()[0]["performanceApproved"]
    assert result["pit_qualified"] is False and result["execution_authorized"] is False


def test_async_validation_and_interrupted_job_recovery(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    c = client_for(s)
    r = c.post("/api/v1/ai/models/validate", headers=headers(), json={"modelKey": "gemma"})
    assert r.status_code == 202
    result = c.get(r.json()["statusUrl"], headers=headers()).json()
    assert result["state"] == "completed" and result["result"]["valid"]
    assert s.active()["selectionVersion"] == 0
    orphan = s.submit_validation("gemma", "owner")
    replacement = s.submit_validation("other", "owner")
    assert s.validation_job(orphan)["state"] == "interrupted"
    s.execute_validation_job(replacement)
    assert s.validation_job(replacement)["result"]["valid"]


def test_catalog_extension_does_not_change_selection(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    remote = ModelConfig(
        "remote",
        "openai_compatible",
        "reviewed-model",
        "reviewed-revision",
        "Remote",
        "https://approved.invalid/v1",
        execution_policy="remote",
        credential_env="TEST_REMOTE_KEY",
        structured_output=True,
        json_schema=True,
    )
    extended = ModelManagement(s.root, [*s.configs.values(), remote], s.default, s.factory)
    assert extended.active()["selectedModelKey"] == "gemma"
    with pytest.raises(ValueError):
        ModelManagement(
            s.root, [replace(config(), temperature=0.7), s.configs["other"]], s.default, s.factory
        )


def test_thinking_capability_is_observed_and_generation_remains_bounded(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, b = service
    assert s.validate("gemma", "owner")["valid"]
    assert all(
        v["options"]["num_thread"] == 1 and v["options"]["num_gpu"] == 0
        for v in b.calls
        if "options" in v
    )


def test_large_metadata_only_has_separate_bound() -> None:
    from donghak_stock_vision.storage.gemma_research import Ollama

    client = Ollama(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"capabilities": ["completion"], "license": "x" * 67000}
            )
        )
    )
    assert client.request("/api/show", {"model": "existing"})["capabilities"] == ["completion"]
    with pytest.raises(ValueError):
        client.request("/api/generate", {"model": "existing"})
    client.close()


def test_body_bound_and_openapi_authentication_contract(
    service: tuple[ModelManagement, LocalTransport],
) -> None:
    s, _ = service
    c = client_for(s)
    r = c.post("/api/v1/ai/models/validate", headers=headers(), content="x" * 5000)
    assert r.status_code == 413
    schema = c.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "202" in schema["paths"]["/api/v1/ai/models/validate"]["post"]["responses"]
    assert s.active()["lastApplied"] is None


def test_remote_reply_cannot_echo_credentials(tmp_path: Path) -> None:
    remote = ModelConfig(
        "remote",
        "openai_compatible",
        "model",
        "revision",
        "Remote",
        "https://approved.invalid/v1",
        execution_policy="remote",
        credential_env="TEST_REMOTE_KEY",
        structured_output=True,
        json_schema=True,
        remote_enabled=True,
        approved_transfer_scope="bounded_research_summary_v1",
    )
    factory = ProviderFactory(
        credential=lambda name: "private-credential-value",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=dict(
                    model="model",
                    choices=[
                        dict(finish_reason="stop", message=dict(content="private-credential-value"))
                    ],
                ),
            )
        ),
    )
    agent = LLMResearchAgent(tmp_path, factory.create(remote))
    with pytest.raises(ValueError, match="secret_echo"):
        agent.propose(contract_probe())
    assert agent.audit["metadata"]["remote_http_requests"] == 1
    with closing(ReadOnlyAnalysisStore(tmp_path / DB)) as store:
        assert "private-credential-value" not in json.dumps(store.all("llm_proposal"))


def test_failed_local_discovery_retains_attempt_count(tmp_path: Path) -> None:
    agent = LLMResearchAgent(
        tmp_path,
        ProviderFactory(transport=httpx.MockTransport(lambda request: httpx.Response(503))).create(
            config()
        ),
    )
    with pytest.raises(ValueError):
        agent.propose(contract_probe())
    assert agent.audit["metadata"]["local_http_requests"] == 1


def test_schema_parent_ids_are_verified_history_only() -> None:
    from donghak_stock_vision.storage.gemma_research import proposal_schema

    view = contract_probe()
    view["history"] = [{"experiment_id": "verified-parent"}]
    assert proposal_schema(view)["properties"]["parentExperimentId"]["enum"] == [
        None,
        "verified-parent",
    ]
