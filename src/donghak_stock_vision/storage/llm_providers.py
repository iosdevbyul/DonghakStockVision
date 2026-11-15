"""Server-configured adapters. Transport never validates or executes research proposals."""

import math
import os
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.llm_provider import LLMProvider, ModelConfig
from donghak_stock_vision.storage.gemma_research import Ollama, process_resources
from donghak_stock_vision.storage.research_resources import probe


class OllamaProvider:
    def __init__(self, config: ModelConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.backend = Ollama(transport, model=config.model_id)
        self.previous_calls = 0
        self.unloaded = False
        self.metadata: dict[str, Any] = {}

    def discover(self) -> dict[str, Any]:
        identity = self.backend.identity(self.config.revision)
        info = self.backend.request("/api/show", dict(model=self.config.model_id))
        advertised = info.get("capabilities", [])
        require(
            not self.config.thinking_supported or "thinking" in advertised,
            "llm_thinking_capability_mismatch",
        )
        return dict(
            installed=True,
            configured=True,
            reachable=True,
            identity=identity,
            advertisedCapabilities=advertised,
        )

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        c = self.config
        c.require_execution()
        self.discover()
        native = probe()
        require(
            bool(native.get("verified"))
            and native["ac_power"]
            and native["thermal_state"] in (0, 1),
            "llm_native_resources_unavailable",
        )
        require(type(native.get("physical_memory_mb")) is int, "llm_physical_memory_unknown")
        # Native host memory and the installed artifact lower bound both apply.
        models = self.backend.request("/api/tags")["models"]
        model = next(m for m in models if m["name"] == c.model_id and m["digest"] == c.revision)
        require(
            c.estimated_memory_mb >= math.ceil(model["size"] / 1024**2),
            "llm_memory_estimate_below_artifact",
        )
        require(
            native["memory_free_percent"] >= 45
            and native["physical_memory_mb"] * native["memory_free_percent"] / 100
            >= c.estimated_memory_mb + 1024,
            "llm_loading_headroom",
        )
        require(self.backend.idle(), "llm_preexisting_model_active")
        self.unloaded = False
        options = dict(
            num_thread=1,
            num_gpu=0,
            num_ctx=c.context_window,
            num_predict=c.max_output_tokens,
            temperature=c.temperature,
            seed=42,
        )
        thread: threading.Thread | None = None
        began = time.monotonic()
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(1)
        try:
            self.backend.request(
                "/api/generate",
                dict(model=c.model_id, keep_alive=30, stream=False, options=options),
                timeout=60,
            )
            loaded = self.backend.request("/api/ps")["models"]
            require(
                len(loaded) == 1
                and loaded[0].get("digest") == c.revision
                and loaded[0].get("size_vram") == 0,
                "llm_resident_identity",
            )
            self.metadata["inference_calls"] = 1
            body = dict(
                model=c.model_id,
                prompt=prompt,
                format=schema,
                stream=False,
                keep_alive=30,
                options=options,
            )
            if c.thinking_supported is True:
                body["think"] = c.thinking

            def execute() -> None:
                try:
                    result.put(
                        (True, self.backend.request("/api/generate", body, timeout=c.timeout))
                    )
                except Exception as error:
                    result.put((False, error))

            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            inference_start = time.monotonic()
            maximum_rss = 0
            maximum_cpu = 0.0
            while thread.is_alive():
                thread.join(timeout=2)
                resources = process_resources()
                state = probe()
                require(
                    resources["verified"] and bool(state.get("verified")),
                    "llm_resource_probe_unknown",
                )
                maximum_rss = max(maximum_rss, resources["rss_kb"])
                maximum_cpu = max(maximum_cpu, resources["cpu_percent"])
                require(
                    resources["rss_kb"] <= c.max_memory_mb * 1024
                    and resources["cpu_percent"] <= c.max_cpu_percent,
                    "llm_resource_limit",
                )
                require(
                    state["ac_power"]
                    and state["thermal_state"] in (0, 1)
                    and state["memory_free_percent"] >= 10,
                    "llm_resource_pressure",
                )
                require(time.monotonic() - inference_start <= c.timeout, "llm_inference_timeout")
            ok, response = result.get_nowait()
            if not ok:
                raise ValueError("llm_transport_failed")
            require(
                response.get("done") is True
                and response.get("model") == c.model_id
                and type(response.get("response")) is str,
                "llm_incomplete_response",
            )
            self.metadata.update(
                maximum_rss_kb=maximum_rss,
                maximum_cpu_percent=maximum_cpu,
                timings={
                    k: response[k]
                    for k in ("total_duration", "eval_count", "load_duration")
                    if k in response
                },
            )
            return str(response["response"])
        finally:
            if thread is not None and thread.is_alive():
                self.previous_calls += self.backend.calls
                self.backend.close()
                thread.join(timeout=3)
                self.backend = Ollama(model=c.model_id)
            self.unloaded = self.backend.release(c.revision) and (
                thread is None or not thread.is_alive()
            )
            self.metadata.update(
                wall_seconds=time.monotonic() - began,
                unloaded=self.unloaded,
                local_http_requests=self.previous_calls + self.backend.calls,
                provider_id=c.provider_id,
                model_id=c.model_id,
                revision=c.revision,
            )

    def close(self) -> None:
        self.metadata["local_http_requests"] = self.previous_calls + self.backend.calls
        self.backend.close()


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: ModelConfig,
        credential: Callable[[str], str | None],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.credential = credential
        self.unloaded = True
        self.metadata: dict[str, Any] = {}
        self.client = httpx.Client(trust_env=False, follow_redirects=False, transport=transport)

    def discover(self) -> dict[str, Any]:
        return dict(
            installed=None,
            configured=bool(self.credential(self.config.credential_env or "")),
            reachable=None,
        )

    def generate(self, prompt: str, schema: dict[str, Any]) -> str:
        c = self.config
        c.require_execution()
        secret = self.credential(c.credential_env or "")
        require(bool(secret), "llm_remote_credential_unavailable")
        require(not c.thinking, "llm_remote_thinking_mapping_unsupported")
        body = dict(
            model=c.model_id,
            messages=[dict(role="user", content=prompt)],
            temperature=c.temperature,
            max_completion_tokens=c.max_output_tokens,
            response_format=dict(
                type="json_schema",
                json_schema=dict(name="research_proposal", strict=True, schema=schema),
            ),
        )
        began = time.monotonic()
        payload = bytearray()
        self.metadata.update(
            provider_id=c.provider_id,
            model_id=c.model_id,
            revision=c.revision,
            remote_http_requests=self.metadata.get("remote_http_requests", 0) + 1,
            revision_authenticated=False,
            unloaded=True,
        )
        with self.client.stream(
            "POST",
            c.endpoint.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + str(secret)},
            json=body,
            timeout=httpx.Timeout(c.timeout, connect=5),
        ) as response:
            require(response.status_code == 200, "llm_remote_http_error")
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                require(
                    len(payload) <= 65536 and time.monotonic() - began <= c.timeout,
                    "llm_remote_response_bound",
                )
        import json

        d = json.loads(payload)
        require(d.get("model") in (c.model_id, c.revision), "llm_remote_model_mismatch")
        choices = d.get("choices", [])
        require(
            len(choices) == 1
            and choices[0].get("finish_reason") == "stop"
            and not choices[0]["message"].get("tool_calls"),
            "llm_remote_incomplete",
        )
        text = choices[0]["message"].get("content")
        require(type(text) is str, "llm_remote_content")
        require(str(secret) not in text, "llm_remote_secret_echo")
        self.metadata.update(
            provider_id=c.provider_id,
            model_id=c.model_id,
            revision=c.revision,
            wall_seconds=time.monotonic() - began,
            remote_http_requests=1,
            observed_model_id=d.get("model"),
            revision_authenticated=False,
            unloaded=True,
        )
        return str(text)

    def close(self) -> None:
        self.client.close()


class ProviderFactory:
    def __init__(
        self,
        credential: Callable[[str], str | None] = os.environ.get,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.credential = credential
        self.transport = transport

    def create(self, config: ModelConfig) -> LLMProvider:
        if config.provider_id == "ollama":
            return OllamaProvider(config, self.transport)
        return OpenAICompatibleProvider(config, self.credential, self.transport)


def local_resource_ready(config: ModelConfig) -> bool:
    if config.provider_id != "ollama":
        return True
    state = probe()
    return bool(
        state.get("verified")
        and state.get("ac_power")
        and state.get("thermal_state") in (0, 1)
        and type(state.get("physical_memory_mb")) is int
        and state["memory_free_percent"] >= 45
        and state["physical_memory_mb"] * state["memory_free_percent"] / 100
        >= config.estimated_memory_mb + 1024
    )
