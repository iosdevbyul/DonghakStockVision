"""Verified internal-only catalog and bounded agent adapter over existing owners."""

from __future__ import annotations

import statistics
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import grounded_memory as policy
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock, terminals
from donghak_stock_vision.storage.llm_research_agent import LLMResearchAgent


def catalog(
    root: Path,
    pin: str,
    horizon: int,
    stock_policy: str,
    *,
    _verified: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Local import: the session owns original verification; this is a read-only consumer.
    from donghak_stock_vision.storage.memory_guided_research import evidence, file_sha

    before = file_sha(root / DB)
    plan, data, rows = evidence(root, pin, extended=True, _verified=_verified)
    scope = dict(
        dataset=plan["dataset_id"],
        source=plan["source_pin"],
        cohort=data["generalization"]["cohort"],
        horizon=horizon,
        stock_policy=stock_policy,
        search_end=data["historical"]["search_end"],
    )
    selected = []
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        done = {v["experiment_id"]: v for _, v in store.all("autonomous_terminal")}
        terminals(store)
        proposals = [p for _, p in store.all("llm_proposal") if p.get("state") == "accepted"]
        for row in rows:
            cfg = row["config"]
            if cfg["goal"]["horizon"] != horizon or cfg["stock_policy"] != stock_policy:
                continue
            owner = done[row["experiment_id"]]
            parents = {
                p["proposal"].get("parentExperimentId")
                for p in proposals
                if p.get("selected") == row["experiment_id"]
            }
            require(len(parents) <= 1, "memory_grounded_parent")
            record = row | dict(
                scope=scope,
                parent_experiment_id=next(iter(parents), None),
                source_plan=pin,
                terminal_hash=digest(owner),
                created_at=store.created_at("autonomous_terminal", digest(owner)).isoformat(),
                chronology_authenticated=False,
            )
            if row["state"] == "completed":
                result = store.get("autonomous_result", row["result_id"])
                defs = data["goals"][str(horizon)]["folds"]
                require(
                    all(f["evaluation"]["end"] <= scope["search_end"] for f in defs),
                    "memory_grounded_future_evidence",
                )
                record.update(
                    mean_ba=statistics.mean(
                        f["evaluation"]["metrics"]["balanced_accuracy"] for f in result["folds"]
                    ),
                    mean_brier=statistics.mean(
                        f["evaluation"]["metrics"]["brier_score"] for f in result["folds"]
                    ),
                    model_sha256=owner["model_sha256"],
                )
            selected.append(record | dict(content_hash=digest(record)))
    require(before == file_sha(root / DB), "memory_evidence_snapshot_changed")
    return scope, selected


class GroundedAgent(LLMResearchAgent):
    mode: str

    def __init__(self, root: Path, provider: Any, mode: str) -> None:
        super().__init__(root, provider)
        self.mode = mode
        self.pack: dict[str, Any] = {}
        self.diagnostic: dict[str, Any] = {}

    def proposal_input(self, view: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
        began = time.monotonic()
        self.pack = policy.pack(view, self.mode)
        summary, prompt = policy.context(self.pack)
        fingerprint = digest(
            dict(
                pack=self.pack,
                budget=view["budget"],
                configuration=self.provider.config.fingerprint,
            )
        )
        with closing(AnalysisStore(self.root / DB)) as store:
            require(
                not any(
                    v["fingerprint"] == fingerprint
                    for _, v in store.all("grounded_request_reservation")
                ),
                "memory_duplicate_request",
            )
            pack_id = store.put("grounded_evidence_pack", self.pack)
            store.put(
                "grounded_request_reservation", dict(fingerprint=fingerprint, pack_id=pack_id)
            )
        self.diagnostic.update(
            pack_id=pack_id,
            fingerprint=fingerprint,
            prompt_characters=len(prompt),
            prompt_bytes=len(prompt.encode()),
            generation_seconds=time.monotonic() - began,
            evidence_count=len(self.pack["citations"]),
            coverage=self.pack["coverage"],
        )
        return summary, prompt, policy.schema(self.pack)

    def validate(self, proposal: dict[str, Any], view: dict[str, Any]) -> str:
        canonical = policy.resolve(proposal, self.pack, view)
        # Both audit and worker consume the original strict canonical contract.
        self.diagnostic.update(
            citation=proposal["cite"],
            claim=proposal["claim"],
            ranking=proposal["ranking"],
            original_experiment_id=canonical["parentExperimentId"],
            citation_valid=True,
            parent_valid=True,
            selected=canonical["experimentId"],
        )
        proposal.clear()
        proposal.update(canonical)
        return str(canonical["experimentId"])

    def propose(self, view: dict[str, Any]) -> str:
        self.diagnostic = dict(
            version=policy.VERSION,
            mode=self.mode,
            at=clock(),
            citation_valid=None,
            parent_valid=None,
        )
        began = time.monotonic()
        try:
            return super().propose(view)
        except Exception as error:
            import re

            reason = str(error)
            if re.fullmatch(r"(?:memory|llm|gemma)_[a-z0-9_]{1,90}", reason) is None:
                reason = "memory_evidence_retrieval"
            self.diagnostic.update(reason=reason, failure_class=policy.failure_class(reason))
            # Preparation failures occur before the transport's existing audit begins.
            if not self.audit:
                self.audit = dict(
                    request_id=digest(self.diagnostic),
                    state="rejected",
                    reason=reason,
                    unloaded=True,
                    wall_seconds=time.monotonic() - began,
                    metadata={},
                )
                with closing(AnalysisStore(self.root / DB)) as store:
                    store.put("llm_proposal", self.audit)
            raise ValueError(reason) from None
        finally:
            self.diagnostic.update(
                request_id=self.audit.get("request_id"),
                accepted=self.audit.get("state") == "accepted",
                wall_seconds=time.monotonic() - began,
            )
            with closing(AnalysisStore(self.root / DB)) as store:
                store.put("grounded_proposal_diagnostic", self.diagnostic)


def diagnostics(root: Path) -> dict[str, Any]:
    """Allowlist public measurements; no prompt, source PIN, filesystem path or pack bytes."""
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        entries = [v for _, v in store.all("grounded_proposal_diagnostic")]
        public = []
        for d in entries:
            if "pack_id" in d:
                p = store.get("grounded_evidence_pack", d["pack_id"])
                require(
                    p["version"] == policy.VERSION and p["mode"] == d["mode"],
                    "memory_evidence_retrieval",
                )
                for key, row in p["citations"].items():
                    require(
                        digest({k: v for k, v in row.items() if k != "content_hash"})
                        == row["content_hash"]
                        and row["scope"] == p["scope"]
                        and key
                        == "e_"
                        + digest(
                            dict(
                                experiment=row["experiment_id"],
                                content=row["content_hash"],
                                scope=row["scope"],
                            )
                        )[:12],
                        "memory_grounded_citation",
                    )
            ranking = [p["candidates"][k]["experiment_id"] for k in d.get("ranking", [])]
            if d["accepted"]:
                original = p["citations"][d["citation"]]["experiment_id"]
                require(original == d["original_experiment_id"], "memory_grounded_parent")
            public.append(
                dict(
                    mode=d["mode"],
                    accepted=d["accepted"],
                    reason=d.get("reason"),
                    failureClass=d.get("failure_class"),
                    citationValid=d["citation_valid"],
                    parentValid=d["parent_valid"],
                    originalExperimentId=d.get("original_experiment_id"),
                    ranking=ranking,
                    promptCharacters=d.get("prompt_characters"),
                    promptBytes=d.get("prompt_bytes"),
                    preparationSeconds=d.get("generation_seconds"),
                    evidenceCount=d.get("evidence_count"),
                    omittedEvidence=d.get("coverage", {}).get("omitted"),
                )
            )
        llm = [
            v for _, v in store.all("llm_proposal") if v.get("metadata", {}).get("inference_calls")
        ]

    def measured(key: str) -> float | None:
        values = [v["metadata"].get(key) for v in llm]
        return sum(values) if all(type(n) in (int, float) for n in values) else None

    result = dict(
        version="grounded_diagnostics_v1",
        records=public,
        loadSeconds=measured("load_seconds"),
        inferenceSeconds=measured("inference_seconds"),
        unloadSeconds=measured("unload_seconds"),
        validProposals=sum(d["accepted"] for d in entries),
        calls=len(llm),
    )
    return validate_diagnostics(result)


def validate_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
    import math
    import re

    from donghak_stock_vision.backtest.validation import fields

    def number(n: Any) -> bool:
        return n is None or type(n) in (int, float) and math.isfinite(n) and n >= 0

    def identifier(s: Any) -> bool:
        return type(s) is str and re.fullmatch("[a-f0-9]{64}", s) is not None

    fields(value, "version records loadSeconds inferenceSeconds unloadSeconds validProposals calls")
    require(
        value["version"] == "grounded_diagnostics_v1"
        and type(value["records"]) is list
        and len(value["records"]) <= 16,
        "memory_publication_diagnostics",
    )
    require(
        all(number(value[k]) for k in ("loadSeconds", "inferenceSeconds", "unloadSeconds"))
        and all(type(value[k]) is int and 0 <= value[k] <= 16 for k in ("validProposals", "calls")),
        "memory_publication_diagnostics",
    )
    for r in value["records"]:
        fields(
            r,
            "mode accepted reason failureClass citationValid parentValid originalExperimentId "
            "ranking promptCharacters promptBytes preparationSeconds evidenceCount omittedEvidence",
        )
        require(
            r["mode"] in ("verified", "compact", "value_ranked")
            and type(r["accepted"]) is bool
            and all(r[k] is None or type(r[k]) is bool for k in ("citationValid", "parentValid")),
            "memory_publication_diagnostics",
        )
        require(
            r["reason"] is None
            or type(r["reason"]) is str
            and re.fullmatch("(?:memory|llm|gemma)_[a-z0-9_]{1,100}", r["reason"]) is not None,
            "memory_publication_diagnostics",
        )
        require(
            r["failureClass"] is None
            or r["failureClass"] in set(policy.FAILURES.values()) | {"Resource Unavailable"},
            "memory_publication_diagnostics",
        )
        require(
            r["originalExperimentId"] is None or identifier(r["originalExperimentId"]),
            "memory_publication_diagnostics",
        )
        require(
            type(r["ranking"]) is list
            and len(r["ranking"]) <= 3
            and all(identifier(s) for s in r["ranking"]),
            "memory_publication_diagnostics",
        )
        require(
            all(
                number(r[k])
                for k in (
                    "promptCharacters",
                    "promptBytes",
                    "preparationSeconds",
                    "evidenceCount",
                    "omittedEvidence",
                )
            ),
            "memory_publication_diagnostics",
        )
    return value


def rejection_catalog(root: Path) -> list[dict[str, Any]]:
    """A rejected proposal is an audit fact, not a completed/model experiment."""
    import re

    result = []
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        for key, value in store.all("llm_proposal"):
            if value.get("state") == "accepted":
                continue
            reason = value.get("reason", "llm_proposal_rejected")
            require(
                type(reason) is str
                and re.fullmatch("(?:memory|llm|gemma)_[a-z0-9_]{1,100}", reason) is not None,
                "memory_evidence_retrieval",
            )
            result.append(
                dict(
                    proposal_artifact_id=key,
                    original_experiment_id=None,
                    reason=reason,
                    failure_class=policy.failure_class(reason),
                    scope="unassigned_rejected_proposal_not_model_evidence",
                )
            )
    return result
