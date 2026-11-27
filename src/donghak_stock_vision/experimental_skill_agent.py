"""Opt-in structured tool selection through the existing local provider."""

import json
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.experimental_view_skills import build_view_skills
from donghak_stock_vision.models.llm_provider import LLMProvider
from donghak_stock_vision.models.research_agent import decode_gemma_proposal
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock


def select_with_skills(
    view: dict[str, Any], generate: Callable[[str, dict[str, Any]], str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = build_view_skills(view, budget=2)
    candidates = view["candidates"]
    ids = [c["experiment_id"] for c in candidates]
    histories = [h["experiment_id"] for h in view["history"]]
    if not ids or len(ids) > 36:
        raise ValueError("skill_candidate_bound")
    # Short aliases are host-owned; the model never constructs configuration IDs.
    options = ["experiment", *registry.catalog(), "dataset", "evaluation"]
    summary = {
        "goal": view["goal"]["label_version"],
        "candidates": [
            [
                i,
                c["config"]["candidate"]["family"],
                c["config"]["mask"],
                c["config"]["candidate"]["value"],
            ]
            for i, c in enumerate(candidates)
        ],
        "history": [
            [i, h["state"], h.get("mean_ap"), h.get("overfit_gap")]
            for i, h in enumerate(view["history"])
        ],
    }
    trace: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    for step in range(3):
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": options if step < 2 else ["experiment"]},
                "index": {"type": "integer", "minimum": 0, "maximum": 35},
                "reason": {"type": "string", "maxLength": 120},
            },
            "required": ["action", "index", "reason"],
            "additionalProperties": False,
        }
        prompt = (
            "Choose a research tool or experiment. Indices are zero-based. "
            "memory uses history index; experiment uses candidate index; "
            "other tools use index 0. Internal evidence only; no causal or trading claims. "
            "Do not repeat tools. JSON only.\n"
            + json.dumps({"view": summary, "observations": trace}, separators=(",", ":"))
        )
        if len(prompt.encode()) > 10000:
            raise ValueError("skill_prompt_bound")
        raw = generate(prompt, schema)
        if len(raw) > 2048:
            raise ValueError("skill_response_bound")
        choice = decode_gemma_proposal(raw)
        if set(choice) != {"action", "index", "reason"}:
            raise ValueError("skill_action_fields")
        action, index, reason = choice["action"], choice["index"], choice["reason"]
        if (
            type(action) is not str
            or action not in options
            or type(index) is not int
            or not 0 <= index <= 35
            or type(reason) is not str
            or not 0 < len(reason) <= 120
        ):
            raise ValueError("skill_action_value")
        if action == "experiment":
            if index >= len(ids):
                raise ValueError("skill_candidate_index")
            return {
                "experimentId": ids[index],
                "parentExperimentId": None,
                "hypothesis": reason,
            }, trace
        if step == 2 or (action, index) in used:
            raise ValueError("skill_tool_budget_or_duplicate")
        used.add((action, index))
        if action == "memory":
            if index >= len(histories):
                raise ValueError("skill_history_index")
            observation = registry.invoke("memory", {"experiment_id": histories[index]})
            result: dict[str, Any] = {
                k: observation[k]
                for k in (
                    "state",
                    "mean_ap",
                    "overfit_gap",
                    "baseline_ap_gain",
                    "worst_balanced_accuracy",
                    "failure_reason",
                )
                if k in observation
            }
        else:
            if index != 0:
                raise ValueError("skill_unexpected_index")
            if action == "dataset":
                result = {"version": view["dataset_version"], "goal": view["goal"]["label_version"]}
            elif action == "evaluation":
                result = {"internal": summary["history"]}
            elif action == "candidates":
                result = {"candidates": summary["candidates"]}
            else:
                result = dict(registry.invoke("diagnosis", {}))
        trace.append({"action": action, "index": index, "result": result})
    raise ValueError("skill_no_experiment")


class SkillAgent:
    def __init__(self, root: Path, factory: Callable[[], LLMProvider]) -> None:
        self.root = root
        self.factory = factory
        self.audit: dict[str, Any] = {}
        self.unloaded = False

    def propose(self, view: dict[str, Any]) -> str:
        calls: list[dict[str, Any]] = []
        began = time.monotonic()
        self.audit = {"state": "rejected", "started_at": clock()}

        def generate(prompt: str, schema: dict[str, Any]) -> str:
            provider = self.factory()
            self.unloaded = False
            try:
                response = provider.generate(prompt, schema)
                self.unloaded = provider.unloaded
                if not self.unloaded:
                    raise ValueError("skill_unload_unverified")
                return response
            finally:
                self.unloaded = provider.unloaded
                provider.close()
                calls.append(dict(provider.metadata))

        try:
            proposal, trace = select_with_skills(view, generate)
            self.audit.update(state="accepted", proposal=proposal, trace=trace)
            return str(proposal["experimentId"])
        except Exception as error:
            self.audit["reason"] = type(error).__name__
            raise ValueError("skill_proposal_rejected") from error
        finally:
            self.audit.update(
                unloaded=self.unloaded,
                calls=calls,
                wall_seconds=time.monotonic() - began,
                metadata={"inference_calls": len(calls)},
            )
            with closing(AnalysisStore(self.root / DB)) as store:
                self.audit["request_id"] = store.put("experimental_skill_proposal", self.audit)
                store.put("llm_proposal", self.audit)
