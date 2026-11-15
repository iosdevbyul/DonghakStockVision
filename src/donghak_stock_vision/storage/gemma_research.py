"""Explicit CPU-only loopback Ollama proposals, bounded and audited as untrusted text."""

import json
import queue
import re
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import httpx

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.research_agent import (
    decode_gemma_proposal,
    validate_gemma_proposal,
)
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock

ENDPOINT = "http://127.0.0.1:11434"
MODEL = "gemma4:e2b"
LIMITS = dict(
    inference_seconds=240,
    load_seconds=60,
    unload_seconds=20,
    prompt_bytes=14000,
    response_bytes=65536,
    ollama_rss_mb=7168,
    ollama_cpu_percent=450,
    minimum_free_percent=10,
    num_thread=1,
    num_ctx=4096,
    num_predict=384,
    minimum_start_free_percent=45,
)


def process_resources() -> dict[str, Any]:
    from donghak_stock_vision.storage.research_resources import _command

    output = _command(["/bin/ps", "-ax", "-o", "pid=,rss=,%cpu=,comm="])
    if output == "UNKNOWN":
        return dict(verified=False)
    rows = []
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and "ollama" in parts[3].lower():
            try:
                rows.append((int(parts[0]), int(parts[1]), float(parts[2])))
            except ValueError:
                return dict(verified=False)
    return dict(
        verified=True,
        rss_kb=sum(r[1] for r in rows),
        cpu_percent=sum(r[2] for r in rows),
        pids=[r[0] for r in rows],
    )


def proposal_schema(view: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "proposalVersion": dict(type="string", enum=["gemma_research_proposal_v1"]),
        "experimentId": dict(type="string", enum=[c["experiment_id"] for c in view["candidates"]]),
        "modelFamily": dict(
            type="string",
            enum=sorted({c["config"]["candidate"]["family"] for c in view["candidates"]}),
        ),
        "featureSet": dict(
            type="string", enum=sorted({c["config"]["mask"] for c in view["candidates"]})
        ),
        "hyperparameters": dict(
            type="object",
            properties=dict(
                value=dict(
                    type="number",
                    enum=sorted({c["config"]["candidate"]["value"] for c in view["candidates"]}),
                ),
                seed=dict(type="integer", enum=[42]),
            ),
            required=["value", "seed"],
            additionalProperties=False,
        ),
        "researchGoal": dict(type="string", enum=[view["goal"]["label_version"]]),
        "parentExperimentId": dict(type=["string", "null"]),
    }
    for key in ("hypothesis", "expectedImprovement", "rationale"):
        props[key] = dict(type="string", maxLength=160)
    return dict(type="object", properties=props, required=list(props), additionalProperties=False)


def prompt_view(view: dict[str, Any]) -> dict[str, Any]:
    def compact(c: dict[str, Any]) -> dict[str, Any]:
        v = c["config"]
        return dict(
            id=c["experiment_id"],
            family=v["candidate"]["family"],
            value=v["candidate"]["value"],
            featureSet=v["mask"],
        )

    history = []
    # The current leader and recent observations fit a bounded context. No raw
    # rows, source PINs/paths, model parameters or confirmation scores are exported.
    chosen = ([view["leader"]] if view.get("leader") else []) + view["history"][-7:]
    visited = set()
    for h in chosen:
        if h["experiment_id"] in visited:
            continue
        visited.add(h["experiment_id"])
        history.append(
            compact(h)
            | {
                k: h[k]
                for k in (
                    "state",
                    "mean_ap",
                    "stability_ap",
                    "overfit_gap",
                    "baseline_ap_gain",
                    "worst_balanced_accuracy",
                    "failure_reason",
                )
                if k in h
            }
        )
    return dict(
        dataset=view["dataset_version"],
        goal=view["goal"]["label_version"],
        features=view["goal"]["features"],
        remaining_budget=view["budget"],
        candidates=[compact(c) for c in view["candidates"]],
        history=history,
        warning=(
            "Historical development search; no independent performance/PIT/execution approval. "
            "Scores are not price probabilities."
        ),
    )


class Ollama:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.calls = 0
        self.client = httpx.Client(
            base_url=ENDPOINT,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, read=240),
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def request(
        self, path: str, body: dict[str, Any] | None = None, timeout: float = 5
    ) -> dict[str, Any]:
        require(
            path in ("/api/version", "/api/tags", "/api/ps", "/api/generate"),
            "gemma_endpoint_allowlist",
        )
        self.calls += 1
        began = time.monotonic()
        with self.client.stream(
            "GET" if body is None else "POST",
            path,
            json=body,
            timeout=httpx.Timeout(timeout, connect=3, write=5, pool=3),
        ) as response:
            require(response.status_code == 200, "gemma_http_error")
            value = bytearray()
            for chunk in response.iter_bytes():
                value.extend(chunk)
                require(
                    len(value) <= LIMITS["response_bytes"] and time.monotonic() - began <= timeout,
                    "gemma_response_bound",
                )
        parsed = json.loads(value)
        require(type(parsed) is dict, "gemma_response_object")
        return cast(dict[str, Any], parsed)

    def identity(self, digest: str) -> dict[str, Any]:
        version = self.request("/api/version")
        models = self.request("/api/tags")["models"]
        matching = [m for m in models if m["name"] == MODEL and m["digest"] == digest]
        require(len(matching) == 1, "gemma_installed_model_binding")
        return dict(
            server_version=version["version"],
            model=MODEL,
            digest=digest,
            installed_bytes=matching[0]["size"],
        )

    def idle(self) -> bool:
        return self.request("/api/ps").get("models") == []

    def release(self, digest: str) -> bool:
        try:
            loaded = self.request("/api/ps")["models"]
            if not loaded:
                released = process_resources()
                return bool(released["verified"] and released["rss_kb"] < 256 * 1024)
            require(
                all(
                    m.get("digest") == digest and m.get("name", m.get("model")) == MODEL
                    for m in loaded
                ),
                "gemma_other_model_active",
            )
            self.request("/api/generate", dict(model=MODEL, keep_alive=0, stream=False), timeout=10)
            began = time.monotonic()
            while time.monotonic() - began < LIMITS["unload_seconds"]:
                if self.idle():
                    released = process_resources()
                    if released["verified"] and released["rss_kb"] < 256 * 1024:
                        return True
                time.sleep(0.5)
        except (OSError, httpx.TransportError):
            # A down local daemon can fall back only with independent local OS
            # evidence that Ollama processes hold no large model allocation.
            state = process_resources()
            return bool(state["verified"] and state["rss_kb"] < 256 * 1024)
        except (ValueError, KeyError, httpx.HTTPError):
            return False
        return False


class Gemma4ResearchAgent:
    """One proposal then verified unload; the runner owns the heavy-work lock."""

    def __init__(self, root: Path, digest: str, backend: Ollama | None = None) -> None:
        self.root = root
        self.digest = digest
        self.backend = backend or Ollama()
        self.audit: dict[str, Any] = {}
        self.unloaded = False

    def propose(self, view: dict[str, Any]) -> str:
        from donghak_stock_vision.storage.research_resources import probe, resource_reason

        request = prompt_view(view)
        prompt = (
            "Choose exactly one unexecuted allowed experiment. Output schema JSON only, "
            "in English. Keep each prose field below 120 characters. Copy the chosen id, "
            "family, featureSet and value exactly; seed=42. Parent may be a history id or null. "
            "expectedImprovement is a hypothesis, never a measured gain. Do not emit code or "
            "commands, alter gates or access data. Use memory to justify changing one setting "
            "and avoid high overfit gaps.\n" + json.dumps(request, separators=(",", ":"))
        )
        require(len(prompt.encode()) <= LIMITS["prompt_bytes"], "gemma_prompt_bound")
        value = dict(
            started_at=clock(),
            request=request,
            prompt=prompt,
            limits=LIMITS,
            model=MODEL,
            digest=self.digest,
            state="started",
        )
        with closing(AnalysisStore(self.root / DB)) as store:
            attempt = store.put("gemma_request", value)
        self.audit = dict(
            request_id=attempt,
            started_at=value["started_at"],
            model=MODEL,
            digest=self.digest,
            state="rejected",
            stage="health",
            reason=None,
            unloaded=False,
            maximum_rss_kb=0,
            maximum_cpu_percent=0.0,
            inference_calls=0,
        )
        began = time.monotonic()
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        thread: threading.Thread | None = None
        owned = False
        try:
            require(resource_reason(self.root, probe()) is None, "gemma_resources_not_ready")
            self.audit.update(self.backend.identity(self.digest))
            require(self.backend.idle(), "gemma_preexisting_model_active")
            require(
                probe()["memory_free_percent"] >= LIMITS["minimum_start_free_percent"],
                "gemma_loading_headroom",
            )
            owned = True
            self.audit["stage"] = "loading"
            # Loading has a separate, shorter bounded request.
            self.backend.request(
                "/api/generate",
                dict(
                    model=MODEL,
                    keep_alive=30,
                    stream=False,
                    options=dict(num_thread=1, num_gpu=0, num_ctx=4096),
                ),
                timeout=LIMITS["load_seconds"],
            )
            loaded = self.backend.request("/api/ps")["models"]
            require(
                len(loaded) == 1
                and loaded[0].get("digest") == self.digest
                and loaded[0].get("size_vram") == 0,
                "gemma_loaded_cpu_identity",
            )
            self.audit["loaded_metadata"] = {
                k: loaded[0].get(k) for k in ("digest", "size", "size_vram", "context_length")
            }
            observed = process_resources()
            require(
                observed["verified"] and observed["rss_kb"] <= LIMITS["ollama_rss_mb"] * 1024,
                "gemma_loaded_memory_limit",
            )
            self.audit["maximum_rss_kb"] = observed["rss_kb"]
            self.audit["maximum_cpu_percent"] = observed["cpu_percent"]
            self.audit["stage"] = "inference"
            self.audit["inference_calls"] = 1
            body = dict(
                model=MODEL,
                prompt=prompt,
                format=proposal_schema(view),
                stream=False,
                think=False,
                keep_alive=30,
                options=dict(
                    num_thread=1,
                    num_gpu=0,
                    num_ctx=4096,
                    num_predict=LIMITS["num_predict"],
                    temperature=0.2,
                    seed=42,
                ),
            )

            def generate() -> None:
                try:
                    result.put(
                        (
                            True,
                            self.backend.request(
                                "/api/generate", body, timeout=LIMITS["inference_seconds"]
                            ),
                        )
                    )
                except Exception as error:
                    result.put((False, error))

            thread = threading.Thread(target=generate, daemon=True)
            thread.start()
            inference_start = time.monotonic()
            while thread.is_alive():
                thread.join(timeout=2)
                if not thread.is_alive():
                    break
                resources = process_resources()
                native = probe()
                require(resources["verified"], "gemma_process_probe_unknown")
                self.audit["maximum_rss_kb"] = max(
                    self.audit["maximum_rss_kb"], resources["rss_kb"]
                )
                self.audit["maximum_cpu_percent"] = max(
                    self.audit["maximum_cpu_percent"], resources["cpu_percent"]
                )
                require(resources["rss_kb"] <= LIMITS["ollama_rss_mb"] * 1024, "gemma_memory_limit")
                require(
                    bool(native.get("verified"))
                    and native["ac_power"]
                    and native["thermal_state"] in (0, 1)
                    and native["memory_free_percent"] >= LIMITS["minimum_free_percent"],
                    "gemma_resource_pressure",
                )
                require(
                    time.monotonic() - inference_start <= LIMITS["inference_seconds"],
                    "gemma_timeout",
                )
            ok, response = result.get_nowait()
            if not ok:
                raise response
            require(
                response.get("done") is True and response.get("model") == MODEL,
                "gemma_incomplete_response",
            )
            text = response.get("response")
            require(type(text) is str and len(text) <= 8192, "gemma_proposal_size")
            self.audit.update(
                response_text=text,
                ollama_timings={
                    k: response[k]
                    for k in (
                        "total_duration",
                        "load_duration",
                        "eval_count",
                        "eval_duration",
                        "prompt_eval_count",
                    )
                    if k in response
                },
            )
            proposal = decode_gemma_proposal(text)
            require(type(proposal) is dict, "gemma_proposal_object")
            selected = validate_gemma_proposal(proposal, view)
            self.audit.update(state="accepted", proposal=proposal, selected=selected)
            return selected
        except Exception as error:
            self.audit.update(error_class=type(error).__name__, failed_stage=self.audit["stage"])
            reason = str(error)
            self.audit["reason"] = (
                reason if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", reason) else "gemma_request_failed"
            )
            raise ValueError(self.audit["reason"]) from None
        finally:
            if owned:
                if thread is not None and thread.is_alive():
                    self.audit["aborted_transport_calls"] = self.backend.calls
                    self.backend.close()
                    thread.join(timeout=3)
                    self.backend = Ollama()
                self.unloaded = self.backend.release(self.digest) and (
                    thread is None or not thread.is_alive()
                )
            else:
                try:
                    self.unloaded = self.backend.idle()
                except Exception:
                    resources = process_resources()
                    self.unloaded = bool(resources["verified"] and resources["rss_kb"] < 256 * 1024)
            calls = self.backend.calls + self.audit.get("aborted_transport_calls", 0)
            self.backend.close()
            self.audit.update(
                local_http_requests=calls,
                unloaded=self.unloaded,
                finished_at=clock(),
                wall_seconds=time.monotonic() - began,
            )
            with closing(AnalysisStore(self.root / DB)) as store:
                store.put("gemma_proposal", self.audit)


def recover(root: Path, digest: str) -> bool:
    """Clean up an interrupted owned proposal; never unload unrelated models."""
    from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore

    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        requests = store.all("gemma_request")
        finished = {v["request_id"] for _, v in store.all("gemma_proposal")}
        recovered = {v["request_id"] for _, v in store.all("gemma_recovery")}
    pending = [key for key, _ in requests if key not in finished | recovered]
    if not pending:
        return True
    backend = Ollama()
    try:
        unloaded = backend.release(digest)
        if unloaded:
            with closing(AnalysisStore(root / DB)) as store:
                for key in pending:
                    store.put("gemma_recovery", dict(request_id=key, at=clock(), unloaded=True))
        return unloaded
    finally:
        backend.close()
