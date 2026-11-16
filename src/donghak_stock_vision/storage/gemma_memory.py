"""Read-only prior study lineage and separate, bounded agent-study memory."""

import hashlib
import json
import os
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.directional import catalog
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock, context, terminals
from donghak_stock_vision.storage.krx_backfill import write_new


def derive(
    parent: Path,
    parent_pin: str,
    root: Path,
    pins: Path,
    model_digest: str,
    *,
    managed: bool = False,
) -> str:
    sha(model_digest)
    old, data = context(parent, parent_pin, code_check=False)
    require(old["version"] == "autonomous_plan_v1", "gemma_original_parent_required")
    request = json.loads((parent / "request.json").read_text())
    require(digest(request) == old["request_pin"], "gemma_parent_request_binding")
    database = Path(request["research"]["database"])
    with database.open("rb") as stream:
        current = hashlib.file_digest(stream, "sha256").hexdigest()
    require(current == request["research"]["pin"], "gemma_original_database_changed")
    require(
        all(
            p.is_absolute()
            and p.resolve() == p
            and not p.exists()
            and not p.is_relative_to(parent)
            and not p.is_relative_to(database.parent)
            for p in (root, pins)
        )
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root),
        "gemma_new_study_paths",
    )
    code = provenance()
    configurations = []
    for c in catalog(
        sorted(int(h) for h in data["goals"]),
        "management_masks_v1" if managed else "agent_masks_v1",
    ):
        identity = c | dict(
            training_sample_pin=data["goals"][str(c["goal"]["horizon"])]["sample_pin"],
            fold_pin=digest(data["folds"]),
            implementation_hash=code["implementation_hash"],
            versions=code["versions"],
        )
        configurations.append(dict(experiment_id=digest(identity), config=c))
    d = dict(
        parent=str(parent),
        parent_pin=parent_pin,
        model_digest=model_digest,
        version="gemma_study_derivation_v1",
    )
    plan = old | dict(
        version="autonomous_plan_v2",
        code=code,
        configurations=configurations,
        request_pin=digest(d),
        search_space="agent_masks_v1",
        memory_parent=dict(root=str(parent), pin=parent_pin),
        agent_model_digest=model_digest,
    )
    if managed:
        plan.update(
            version="autonomous_plan_v3",
            search_space="management_masks_v1",
            agent_policy="server_validated_managed_v1",
        )
        plan.pop("agent_model_digest")
    with closing(ReadOnlyAnalysisStore(parent / DB)) as store:
        snapshot = store.get("autonomous_snapshot", old["snapshot_id"])
        proof = store.all("autonomous_source_verification")
        require(
            len(proof) == 1 and snapshot["real_market_source"]["local_originals_verified"] is True,
            "gemma_parent_source_proof",
        )
    root.mkdir(mode=0o700, parents=True)
    pins.mkdir(mode=0o700, parents=True)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        store.put_many(
            [
                ("autonomous_snapshot", snapshot),
                ("autonomous_dataset", data),
                ("autonomous_source_verification", proof[0][1]),
                ("autonomous_plan", plan),
            ]
        )
    for name, value in (
        ("plan", digest(plan)),
        ("dataset", old["dataset_id"]),
        ("source", old["source_pin"]),
        ("snapshot", old["snapshot_id"]),
        ("ollama-model", model_digest),
    ):
        write_new(pins / (name + ".pin"), value + "\n")
    write_new(root / "request.json", json.dumps(d))
    return digest(plan)


def memory_view(
    root: Path,
    plan: dict[str, Any],
    data: dict[str, Any],
    done: dict[str, dict[str, Any]],
    choice: dict[str, Any],
) -> dict[str, Any]:
    horizon = choice["config"]["goal"]["horizon"]
    histories = []
    sources = [root]
    if plan["version"] in ("autonomous_plan_v2", "autonomous_plan_v3"):
        p = plan["memory_parent"]
        parent = Path(p["root"])
        _, parent_data = context(parent, p["pin"], code_check=False)
        require(parent_data == data, "gemma_memory_dataset_owner")
        sources.insert(0, parent)
    seen = set()
    for path in sources:
        with closing(ReadOnlyAnalysisStore(path / DB)) as store:
            previous = sorted(
                terminals(store).items(), key=lambda pair: pair[1].get("finished_at", "")
            )
            for key, v in previous:
                if v["config"]["goal"]["horizon"] != horizon:
                    continue
                semantic = digest(v["config"])
                seen.add(semantic)
                item = dict(experiment_id=key, config=v["config"], state=v["state"])
                if v["state"] == "completed":
                    r = store.get("autonomous_result", v["result_id"])
                    item.update(
                        mean_ap=r["mean_ap"],
                        stability_ap=r["stability_ap"],
                        overfit_gap=r["largest_train_gap"],
                        worst_balanced_accuracy=r["worst_balanced_accuracy"],
                        baseline_ap_gain=sum(
                            f["evaluation"]["metrics"]["pr_auc_ap"]
                            - max(b["pr_auc_ap"] for b in f["evaluation"]["baselines"].values())
                            for f in r["folds"]
                        )
                        / len(r["folds"]),
                    )
                else:
                    item["failure_reason"] = v["reason"]
                histories.append(item)
    candidates = [
        c
        for c in plan["configurations"]
        if c["experiment_id"] not in done
        and c["config"]["goal"]["horizon"] == horizon
        and digest(c["config"]) not in seen
    ]
    candidates.sort(key=lambda c: c["experiment_id"] != choice["experiment_id"])
    if plan["version"] == "autonomous_plan_v4":
        from donghak_stock_vision.models.research_v2 import rank

        candidates = rank(
            candidates,
            [
                v | dict(experiment_id=k)
                for k, v in done.items()
                if v["config"]["goal"]["horizon"] == horizon
            ],
        )[:12]
    require(bool(candidates), "gemma_semantic_search_exhausted")
    return dict(
        version="research_agent_view_v1",
        dataset_version=data["dataset_version"],
        goal=choice["config"]["goal"],
        candidates=candidates,
        history=histories[-24:],
        budget=len(candidates),
        leader=max(
            (h for h in histories if h["state"] == "completed"),
            key=lambda h: h["stability_ap"],
            default=None,
        ),
        performance_approved=False,
        pit_qualified=False,
        execution_authorized=False,
    )


def adopt(
    root: Path, plan: dict[str, Any], peer: Path, peer_pin: str, choice: dict[str, Any]
) -> bool:
    """Reuse exact matching results for paired-arm comparison, never refit a duplicate."""
    other, data = context(peer, peer_pin)
    require(peer != root and other == plan, "gemma_cache_owner")
    key = choice["experiment_id"]
    with closing(ReadOnlyAnalysisStore(peer / DB)) as source:
        prior = terminals(source).get(key)
        if prior is None or prior["state"] != "completed":
            return False
        result = source.get("autonomous_result", prior["result_id"])
        body = Path(prior["checkpoint"]).read_bytes()
    directory = root / "models"
    directory.mkdir(mode=0o700, exist_ok=True)
    require(directory.resolve() == directory, "gemma_cache_directory")
    output = directory / (key + ".checkpoint")
    if output.exists():
        require(
            hashlib.sha256(output.read_bytes()).hexdigest() == prior["model_sha256"],
            "gemma_cache_conflict",
        )
    else:
        temporary = directory / (uuid.uuid4().hex + ".cache.tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
        temporary.unlink()
    value = prior | dict(
        checkpoint=str(output),
        cache_source=dict(root=str(peer), plan=peer_pin, result=prior["result_id"]),
        adopted_at=clock(),
    )
    with closing(AnalysisStore(root / DB)) as store:
        store.put_many([("autonomous_result", result), ("autonomous_terminal", value)])
    return True
