"""Opt-in structured tool selection through the existing local provider."""

import json
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.models.experimental_view_skills import build_view_skills
from donghak_stock_vision.models.llm_provider import LLMProvider
from donghak_stock_vision.models.research_agent import decode_gemma_proposal
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock


def select_with_skills(
    view: dict[str, Any],
    generate: Callable[[str, dict[str, Any]], str],
    *,
    events: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = build_view_skills(view, budget=2)
    candidates = view["candidates"]
    ids = [c["experiment_id"] for c in candidates]
    histories = [h["experiment_id"] for h in view["history"]]
    if not ids or len(ids) > 36:
        raise ValueError("skill_candidate_bound")
    # Short aliases are host-owned; the model never constructs configuration IDs.
    options = ["experiment", *registry.catalog(), "dataset", "evaluation"]
    audit = [] if events is None else events
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
    correction = ""
    for step in range(3):
        sizes = {"experiment": len(ids), "memory": len(histories)}
        valid = {
            name: [i for i in range(sizes.get(name, 1)) if (name, i) not in used]
            for name in options
            if step < 2 or name == "experiment"
        }
        valid = {name: indices for name, indices in valid.items() if indices}
        valid_indices = sorted({i for v in valid.values() for i in v})
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(valid)},
                "index": {"type": "integer", "enum": valid_indices},
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
            + json.dumps(
                {
                    "view": summary,
                    "observations": trace,
                    "valid_indices": valid,
                    "previous_error": correction,
                },
                separators=(",", ":"),
            )
        )
        if len(prompt.encode()) > 10000:
            raise ValueError("skill_prompt_bound")
        event: dict[str, Any] = {"step": step + 1, "state": "generating"}
        audit.append(event)
        raw = generate(prompt, schema)
        event.update(state="received", response=raw[:2048])
        try:
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
            if (action, index) in used:
                raise ValueError("skill_tool_budget_or_duplicate")
            if action == "experiment" and index >= len(ids):
                raise ValueError("skill_candidate_index")
            if action == "memory" and index >= len(histories):
                raise ValueError("skill_history_index")
            if index not in valid.get(action, []):
                raise ValueError("skill_action_not_available")
        except ValueError as error:
            correction = str(error)[:200]
            event.update(state="rejected", reason=correction)
            if step == 2:
                raise
            continue
        event.update(action=action, index=index, reason=reason)
        if action == "experiment":
            event["state"] = "selected"
            return {
                "experimentId": ids[index],
                "parentExperimentId": None,
                "hypothesis": reason,
                "evidence": [
                    {"experimentId": histories[t["index"]], "metric": "mean_ap"}
                    for t in trace
                    if t["action"] == "memory" and "mean_ap" in t["result"]
                ],
            }, trace
        used.add((action, index))
        event["state"] = "invoking"
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
        observation_record = {"action": action, "index": index, "result": result}
        trace.append(observation_record)
        event.update(state="observed", result=result)
        correction = ""
    raise ValueError("skill_no_experiment")


class SkillAgent:
    def __init__(self, root: Path, factory: Callable[[], LLMProvider]) -> None:
        self.root = root
        self.factory = factory
        self.audit: dict[str, Any] = {}
        self.unloaded = False

    def propose(self, view: dict[str, Any]) -> str:
        calls: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
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
                try:
                    provider.close()
                finally:
                    calls.append(dict(provider.metadata))

        try:
            proposal, trace = select_with_skills(view, generate, events=events)
            self.audit.update(state="accepted", proposal=proposal, trace=trace)
            return str(proposal["experimentId"])
        except Exception as error:
            self.audit.update(reason=str(error)[:200], error_type=type(error).__name__)
            raise ValueError("skill_proposal_rejected") from error
        finally:
            self.audit.update(
                unloaded=self.unloaded,
                calls=calls,
                events=events,
                wall_seconds=time.monotonic() - began,
                metadata={
                    "inference_calls": sum(c.get("inference_calls", 0) for c in calls),
                    "maximum_rss_kb": max(
                        (c.get("maximum_rss_kb") or 0 for c in calls), default=0
                    ),
                    "inference_seconds": sum(c.get("inference_seconds") or 0 for c in calls),
                },
            )
            with closing(AnalysisStore(self.root / DB)) as store:
                self.audit["request_id"] = store.put("experimental_skill_proposal", self.audit)
                store.put("llm_proposal", self.audit)
