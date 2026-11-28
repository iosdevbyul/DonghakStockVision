"""Read-only pinned session experience; no protected test or operational writes."""

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, private_root
from donghak_stock_vision.storage.experimental_skill_agent import (
    METRICS,
    configuration,
    hypothesis_feedback,
)
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.memory_research_evidence import evidence, file_sha

MAX_SESSIONS = 12


def scope(row: dict[str, Any], request: dict[str, Any]) -> bool:
    config = row["config"]
    return (
        config["goal"]["horizon"] == request["horizon"]
        and config["stock_policy"] == request["stock_policy"]
    )


def verified_feedback(
    proposal: dict[str, Any], reference: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    metric = proposal["evidence"][0]["metric"]
    if (
        metric not in METRICS
        or proposal["experimentId"] != outcome["experiment_id"]
        or proposal["evidence"][0]["experimentId"] != reference["experiment_id"]
        or proposal["referenceValue"] != reference.get(metric)
        or reference["state"] != "completed"
        or outcome["state"] != "completed"
    ):
        raise ValueError("experience_proposal_binding")
    before, after = configuration(reference), configuration(outcome)
    # Old proposals may have recorded only three display fields.
    for key, expected in (
        ("referenceConfig", before),
        ("candidateConfig", after),
    ):
        if any(expected.get(k) != v for k, v in proposal[key].items()):
            raise ValueError("experience_configuration_binding")
    if any(
        proposal.get(key, row["config"]) != row["config"]
        for key, row in (("referenceFullConfig", reference), ("candidateFullConfig", outcome))
    ):
        raise ValueError("experience_full_configuration_binding")
    rebuilt = proposal | {
        "referenceMetrics": {k: reference.get(k) for k in METRICS},
        "referenceConfig": before,
        "candidateConfig": after,
        "expectedDirection": "decrease" if metric == "overfit_gap" else "increase",
    }
    result = hypothesis_feedback(rebuilt, [outcome])
    result["strategyMode"] = proposal.get("strategy", {}).get("mode", "unknown")
    result["semanticId"] = digest(outcome["config"])
    result["source"] = "verified_previous_session"
    return result


def restore(
    sources: list[tuple[Path, Path]],
    frozen: dict[str, Any],
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay explicit private session references and recompute outcomes."""
    if len(sources) > MAX_SESSIONS or len({root for root, _ in sources}) != len(sources):
        raise ValueError("experience_source_bound")
    rows: dict[str, dict[str, Any]] = {}
    feedback: dict[str, dict[str, Any]] = {}
    lineage: list[dict[str, Any]] = []
    for root, pin_file in sources:
        private_root(root)
        session_file = root / "session.json"
        if (
            not root.name.startswith("skill-experiment-")
            or session_file.resolve() != session_file
            or session_file.stat().st_mode & 0o077
            or session_file.stat().st_size > 8 * 1024**2
        ):
            raise ValueError("experience_private_session")
        old = json.loads(session_file.read_text())
        pin = read_pin(pin_file)
        if (
            digest(old) != pin
            or old["version"] != frozen["version"]
            or old["dataset_id"] != frozen["dataset_id"]
            or old["source_pin"] != frozen["source_pin"]
            or any(old["request"][k] != frozen["request"][k] for k in ("horizon", "stock_policy"))
        ):
            raise ValueError("experience_session_binding")
        target = root.parent / (root.name + "-gemma_blind")
        before = file_sha(target / DB)
        plan, prior_data, outcomes = evidence(target, old["plan_id"])
        if prior_data != data or plan["dataset_id"] != frozen["dataset_id"]:
            raise ValueError("experience_dataset_binding")
        # Validate old frozen memory sources before accepting its reference observations.
        for ref in old["memory_sources"]:
            if file_sha(Path(ref["root"]) / DB) != ref["database_sha256"]:
                raise ValueError("experience_original_changed")
        reference_rows = {
            row["experiment_id"]: row
            for row in old["memory"] + list(rows.values()) + outcomes
            if scope(row, frozen["request"])
        }
        by_id = {row["experiment_id"]: row for row in outcomes}
        with closing(ReadOnlyAnalysisStore(target / DB)) as store:
            selections = {
                v.get("request_id"): v
                for _, v in store.all("autonomous_selection")
                if v["state"] == "accepted"
            }
            for key, audit in store.all("experimental_skill_proposal"):
                if audit["state"] != "accepted" or key not in selections:
                    continue
                proposal = audit["proposal"]
                outcome = by_id.get(proposal["experimentId"])
                if outcome is None or outcome["state"] != "completed":
                    continue
                if selections[key]["experiment_id"] != proposal["experimentId"]:
                    raise ValueError("experience_selection_binding")
                reference = reference_rows.get(proposal["evidence"][0]["experimentId"])
                if reference is None:
                    raise ValueError("experience_reference_missing")
                item = verified_feedback(proposal, reference, outcome)
                feedback.setdefault(item["semanticId"], item)
        if before != file_sha(target / DB):
            raise ValueError("experience_source_changed")
        for row in outcomes:
            if scope(row, frozen["request"]):
                rows[row["experiment_id"]] = row
        lineage.append(
            dict(root=str(root), session_pin=pin, plan_pin=old["plan_id"], database_sha256=before)
        )
    return list(rows.values()), list(feedback.values()), lineage
