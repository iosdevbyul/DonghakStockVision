"""Bounded parent approvals composing the existing executor; Prepare/Status are offline."""

from __future__ import annotations

import argparse
import os
import re
from contextvars import ContextVar
from pathlib import Path
from threading import get_ident
from typing import Any

import httpx

from donghak_stock_vision.backtest import dart_batch_orchestrator as orchestrator
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest

DIRECTORY = ".data/dart-multi-batch-sessions"
VERSION = "dart_multi_batch_session_v1"
FIELDS = {
    "version",
    "session_id",
    "plan_fingerprint",
    "endpoint",
    "receipts",
    "max_requests",
    "children",
    "execution_path",
    "interval_seconds",
    "retries",
    "approved_at",
    "access_confirmed",
    "quota_confirmed",
}
_ACTIVE: ContextVar[tuple[Path, str, int, int] | None] = ContextVar(
    "dart_parent_session", default=None
)


def records(root: Path) -> list[Path]:
    directory = root / DIRECTORY
    require(not directory.is_symlink(), "session_path_invalid")
    if not directory.exists():
        return []
    result = sorted(directory.iterdir())
    for index, path in enumerate(result, 1):
        require(
            path.name == f"session-{index:04d}" and path.is_dir() and not path.is_symlink(),
            "session_sequence_invalid",
        )
        require((path / "draft.json").is_file(), "session_partial_record")
    return result


def child_approvals(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**child, **{k: value[k] for k in orchestrator.GATES}} for child in value["children"]]


def validate(root: Path, value: dict[str, Any], *, approved: bool) -> str:
    require(set(value) == FIELDS and value["version"] == VERSION, "session_schema_invalid")
    identifier = value["session_id"]
    require(
        isinstance(identifier, str) and re.fullmatch(r"session-[0-9]{4}", identifier) is not None,
        "session_id_invalid",
    )
    require(value["execution_path"] == f"{DIRECTORY}/{identifier}", "session_path_invalid")
    plan = batch.plan_for(root)
    require(value["plan_fingerprint"] == plan.identifier, "session_plan_mismatch")
    budget = value["max_requests"]
    require(type(budget) is int and 1 <= budget <= 3107, "session_budget_invalid")
    children = value["children"]
    require(isinstance(children, list) and bool(children), "session_children_invalid")
    scope: list[str] = []
    numbers: list[int] = []
    for child in children:
        require(isinstance(child, dict) and set(child) == batch.FIELDS, "session_child_mismatch")
        require(
            child["approved_at"] is None
            and child["access_confirmed"] is False
            and child["quota_confirmed"] is False,
            "session_child_mismatch",
        )
        require(
            all(
                child[k] == value[k]
                for k in ("endpoint", "plan_fingerprint", "interval_seconds", "retries")
            ),
            "session_child_mismatch",
        )
        batch.draft_check(plan, child)
        require(child["max_requests"] == len(child["receipts"]), "session_child_mismatch")
        scope.extend(child["receipts"])
        numbers.append(int(child["execution_path"][-4:]))
    require(numbers == list(range(numbers[0], numbers[0] + len(numbers))), "session_child_mismatch")
    require(scope == value["receipts"] == sorted(set(scope)), "session_scope_mismatch")
    require(len(scope) == budget, "session_budget_invalid")
    if approved:
        for child in child_approvals(value):
            batch.approval_check(plan, child)
    else:
        require(
            value["approved_at"] is None
            and value["access_confirmed"] is False
            and value["quota_confirmed"] is False,
            "session_draft_required",
        )
    return digest(value)


def binding_for(approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_fingerprint": digest(approval),
        "children": {c["execution_path"]: digest(c) for c in child_approvals(approval)},
    }


def verified_inventory(root: Path) -> dict[str, Any]:
    try:
        return batch.inventory(root, batch.plan_for(root))
    except Exception:
        raise ValueError("session_existing_evidence_invalid") from None


def unused_scope(root: Path, approval: dict[str, Any]) -> None:
    view = verified_inventory(root)
    require(
        all(not (root / c["execution_path"]).exists() for c in child_approvals(approval))
        and all(
            view["states"][r] == "Pending" and r not in view["claims"] for r in approval["receipts"]
        ),
        "session_partial_binding_has_execution",
    )


def numbered(path: Path) -> list[Path]:
    require(not path.is_symlink(), "session_path_invalid")
    if not path.exists():
        return []
    require(path.is_dir(), "session_path_invalid")
    files = sorted(path.iterdir())
    require(
        [p.name for p in files] == [f"{n:04d}.json" for n in range(1, len(files) + 1)],
        "session_journal_invalid",
    )
    return files


def stop_records(path: Path, fingerprint: str) -> list[str]:
    paths = ([path / "stopped.json"] if (path / "stopped.json").exists() else []) + numbered(
        path / "interruptions"
    )
    expected = {
        "parent_fingerprint": fingerprint,
        "reason": "session_execution_stopped_operator_review_required",
    }
    for record in paths:
        value = batch.load(record)
        require(
            value == expected
            or value == {**expected, "recovery_policy": "revalidate_evidence"}
            or value == {**expected, "recovery_policy": "blocked"},
            "session_stop_record_changed",
        )
    return [digest(batch.load(record)) for record in paths]


def recoverable_stops(path: Path, fingerprint: str) -> None:
    stop_records(path, fingerprint)
    paths = ([path / "stopped.json"] if (path / "stopped.json").exists() else []) + numbered(
        path / "interruptions"
    )
    require(
        all(batch.load(p).get("recovery_policy") != "blocked" for p in paths),
        "session_stop_permanently_blocked",
    )


def resume_check(root: Path, approval: dict[str, Any]) -> dict[str, Any]:
    """Only proven saved success or unreserved Pending work may consume the old scope."""
    view = verified_inventory(root)
    require(
        not any(s in {"Attempted", "Failed", "Blocked"} for s in view["states"].values()),
        "session_ambiguous_or_failed_attempt",
    )
    scope = approval["receipts"]
    owners = {r: digest(c) for c in child_approvals(approval) for r in c["receipts"]}
    require(
        all(
            r not in view["claims"] or view["claims"][r]["approval_fingerprint"] == owners[r]
            for r in scope
        ),
        "session_claim_owner_mismatch",
    )
    used = sum(r in view["claims"] for r in scope)
    pending = sum(view["states"][r] == "Pending" for r in scope)
    require(used + pending <= approval["max_requests"], "session_budget_exceeded")
    return view


def resume_records(root: Path, path: Path, approval: dict[str, Any]) -> list[str]:
    records = numbered(path / "resumes")
    if not records:
        return []
    view = verified_inventory(root)
    stops = stop_records(path, digest(approval))
    history: list[str] = []
    for record in records:
        value = batch.load(record)
        require(
            set(value)
            == {
                "parent_fingerprint",
                "previous_resume",
                "stop_fingerprints",
                "source_sha256",
                "claim_sha256",
            },
            "session_journal_invalid",
        )
        require(
            value["parent_fingerprint"] == digest(approval)
            and value["previous_resume"] == (history[-1] if history else None),
            "session_journal_invalid",
        )
        source, claims, observed_stops = (
            value["source_sha256"],
            value["claim_sha256"],
            value["stop_fingerprints"],
        )
        require(
            isinstance(source, dict)
            and isinstance(claims, dict)
            and isinstance(observed_stops, list),
            "session_journal_invalid",
        )
        require(
            observed_stops == stops[: len(observed_stops)] and len(observed_stops) <= len(stops),
            "session_journal_invalid",
        )
        require(
            set(source) <= set(approval["receipts"])
            and all(view["hashes"].get(r) == sha for r, sha in source.items()),
            "session_replay_failed",
        )
        require(
            set(claims) <= set(approval["receipts"])
            and all(
                r in view["claims"] and digest(view["claims"][r]) == sha
                for r, sha in claims.items()
            ),
            "session_claim_owner_mismatch",
        )
        history.append(digest(value))
    return history


def record_resume(root: Path, path: Path, approval: dict[str, Any]) -> None:
    recoverable_stops(path, digest(approval))
    view = resume_check(root, approval)
    history = resume_records(root, path, approval)
    directory = path / "resumes"
    directory.mkdir(exist_ok=True)
    batch.write_once(
        directory / f"{len(history) + 1:04d}.json",
        {
            "parent_fingerprint": digest(approval),
            "previous_resume": history[-1] if history else None,
            "stop_fingerprints": stop_records(path, digest(approval)),
            "source_sha256": {
                r: view["hashes"][r] for r in approval["receipts"] if r in view["hashes"]
            },
            "claim_sha256": {
                r: digest(view["claims"][r]) for r in approval["receipts"] if r in view["claims"]
            },
        },
    )


def record_stop(root: Path, path: Path, approval: dict[str, Any]) -> None:
    fingerprint = digest(approval)
    try:
        resume_check(root, approval)
        policy = "revalidate_evidence"
    except Exception:
        policy = "blocked"
    value = {
        "parent_fingerprint": fingerprint,
        "reason": "session_execution_stopped_operator_review_required",
        "recovery_policy": policy,
    }
    if not (path / "stopped.json").exists():
        batch.write_once(path / "stopped.json", value)
    else:
        directory = path / "interruptions"
        existing = numbered(directory)
        directory.mkdir(exist_ok=True)
        batch.write_once(directory / f"{len(existing) + 1:04d}.json", value)


def checked_records(root: Path) -> list[Path]:
    paths = records(root)
    for path in paths:
        require(
            all(
                not (path / name).is_symlink()
                for name in (
                    "draft.json",
                    "approval.json",
                    "approval-intent.json",
                    "binding.json",
                    "stopped.json",
                    "complete.json",
                )
            ),
            "session_path_invalid",
        )
        draft = batch.load(path / "draft.json")
        validate(root, draft, approved=False)
        require(root / draft["execution_path"] == path, "session_path_invalid")
        if (path / "approval.json").exists():
            approval = batch.load(path / "approval.json")
            fingerprint = validate(root, approval, approved=True)
            require(
                {k: v for k, v in approval.items() if k not in orchestrator.GATES}
                == {k: v for k, v in draft.items() if k not in orchestrator.GATES},
                "session_approval_changed",
            )
            if (path / "approval-intent.json").exists():
                require(
                    batch.load(path / "approval-intent.json")
                    == {"parent_fingerprint": fingerprint, "draft_fingerprint": digest(draft)},
                    "session_approval_changed",
                )
            if (path / "binding.json").exists():
                require(
                    batch.load(path / "binding.json") == binding_for(approval),
                    "session_approval_changed",
                )
            else:
                unused_scope(root, approval)
            stop_records(path, fingerprint)
            history = resume_records(root, path, approval)
            for child in child_approvals(approval):
                unit = root / child["execution_path"]
                if unit.exists():
                    require(batch.load(unit / "approval.json") == child, "session_child_mismatch")
            if (path / "complete.json").exists():
                recoverable_stops(path, fingerprint)
                view = verified_inventory(root)
                expected = {r: view["hashes"].get(r) for r in approval["receipts"]}
                require(all(expected.values()), "session_replay_failed")
                completed = {"parent_fingerprint": fingerprint, "source_sha256": expected}
                if history:
                    completed["recovery_fingerprint"] = history[-1]
                require(
                    not (path / "stopped.json").exists() or bool(history),
                    "session_terminal_record_conflict",
                )
                require(batch.load(path / "complete.json") == completed, "session_replay_failed")
                for child in child_approvals(approval):
                    unit = root / child["execution_path"]
                    require(
                        batch.load(unit / "approval.json") == child
                        and (unit / "events/complete.json").is_file(),
                        "session_child_mismatch",
                    )
        else:
            if (path / "approval-intent.json").exists():
                intent = batch.load(path / "approval-intent.json")
                require(
                    set(intent) == {"parent_fingerprint", "draft_fingerprint"}
                    and intent["draft_fingerprint"] == digest(draft)
                    and isinstance(intent["parent_fingerprint"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", intent["parent_fingerprint"]) is not None,
                    "session_approval_changed",
                )
            require(
                not any(
                    (path / name).exists()
                    for name in ("binding.json", "complete.json", "stopped.json")
                ),
                "session_partial_record",
            )
    return paths


def execution_guard(root: Path, approval: dict[str, Any]) -> None:
    """Direct single-batch execution cannot bypass a pending/failed parent session."""
    for path in checked_records(root):
        if not (path / "complete.json").exists():
            require(
                _ACTIVE.get() == (root.resolve(), path.name, os.getpid(), get_ident()),
                "session_operator_action_required",
            )
            parent = batch.load(path / "approval.json")
            require(approval in child_approvals(parent), "session_child_mismatch")


def report(root: Path) -> dict[str, Any]:
    view = verified_inventory(root)
    paths = checked_records(root)
    active = paths[-1] if paths else None
    value = batch.load(active / "draft.json") if active else None
    started = active is not None and any(
        (active / name).exists() for name in ("approval.json", "approval-intent.json")
    )
    recovery = (
        "PARTIAL APPROVAL: MATCHING RECORD REQUIRED"
        if started and active and not (active / "approval.json").exists()
        else "NOT REQUIRED"
    )
    if active and (active / "approval.json").exists() and not (active / "complete.json").exists():
        try:
            approved = batch.load(active / "approval.json")
            recoverable_stops(active, digest(approved))
            resume_check(root, approved)
            recovery = "EXPLICIT RESUME ALLOWED"
        except Exception:
            recovery = "BLOCKED: OPERATOR REVIEW REQUIRED"
    return {
        "recovery": recovery,
        "resume_history_count": len(numbered(active / "resumes")) if active else 0,
        "stop_history_count": (
            int((active / "stopped.json").exists()) + len(numbered(active / "interruptions"))
        )
        if active
        else 0,
        "binding": "RECOVERY REQUIRED"
        if active and (active / "approval.json").exists() and not (active / "binding.json").exists()
        else "VERIFIED"
        if active and (active / "binding.json").exists()
        else "NOT CREATED",
        "execution": "NOT STARTED"
        if not active or not started
        else "COMPLETE"
        if (active / "complete.json").exists()
        else "STOPPED"
        if (active / "stopped.json").exists()
        else "OPERATOR RESUME REQUIRED",
        "total": 3108,
        "plan_fingerprint": batch.plan_for(root).identifier,
        "max_requests": value["max_requests"] if value else 0,
        "reserved_requests": sum(r in view["claims"] for r in value["receipts"]) if value else 0,
        "execution_path": value["execution_path"] if value else None,
        "stop_reason": "session_execution_stopped_operator_review_required"
        if active and (active / "stopped.json").exists()
        else None,
        "batch_count": len(value["children"]) if value else 0,
        "draft_path": str((active / "draft.json").relative_to(root)) if active else None,
        "parent_fingerprint": digest(batch.load(active / "approval.json"))
        if active and (active / "approval.json").exists()
        else None,
        "counts": batch.summary(batch.plan_for(root), view)["counts"],
        "session": active.name if active else None,
        "state": (
            "COMPLETE"
            if (active / "complete.json").exists()
            else "STOPPED"
            if (active / "stopped.json").exists()
            else "APPROVAL REQUIRED"
            if not started
            else "OPERATOR RESUME REQUIRED"
        )
        if active
        else "NOT PREPARED",
        "offline_replay": "VERIFIED",
        "corporate_actions": "blocked",
        "corporate_action_coverage": "0/993",
        "runner_tail": "blocked",
        "run_input_freeze": "blocked",
    }


def prepare(root: Path, max_requests: int) -> dict[str, Any]:
    root = root.resolve()
    require(type(max_requests) is int and 1 <= max_requests <= 3107, "session_budget_invalid")
    with batch.execution_lock(root):
        paths = checked_records(root)
        require(
            all((p / "complete.json").exists() for p in paths), "session_operator_action_required"
        )
        base, first = orchestrator.inspect_next(root, min(25, max_requests))
        if first is None:
            return report(root)
        view = verified_inventory(root)
        pending = [r for r, state in view["states"].items() if state == "Pending"]
        selected = pending[:max_requests]
        require(
            selected[: len(first["receipts"])] == first["receipts"],
            "session_existing_draft_conflict",
        )
        require(len(selected) >= len(first["receipts"]), "session_existing_draft_conflict")
        start = int(base["next_batch"][-4:])
        children = [
            orchestrator.draft_for(
                first["plan_fingerprint"], selected[i : i + 25], f"batch-{start + i // 25:04d}"
            )
            for i in range(0, len(selected), 25)
        ]
        require(children[0] == first, "session_existing_draft_conflict")
        identifier = f"session-{len(paths) + 1:04d}"
        value = {
            "version": VERSION,
            "session_id": identifier,
            "execution_path": f"{DIRECTORY}/{identifier}",
            "children": children,
            **{
                k: first[k]
                for k in (
                    "endpoint",
                    "plan_fingerprint",
                    "interval_seconds",
                    "retries",
                    "approved_at",
                    "access_confirmed",
                    "quota_confirmed",
                )
            },
            "receipts": selected,
            "max_requests": len(selected),
        }
        validate(root, value, approved=False)
        directory = root / DIRECTORY
        directory.mkdir(exist_ok=True)
        path = directory / identifier
        path.mkdir()
        batch.write_once(path / "draft.json", value)
        return {
            **report(root),
            "selected_count": len(selected),
            "batch_count": len(children),
            "draft_path": str((path / "draft.json").relative_to(root)),
            "existing_batch_draft_reused": base["draft_exists"],
            "actual_api_requests_this_operation": 0,
        }


def execute(
    root: Path,
    approval: dict[str, Any],
    *,
    confirmed: bool = False,
    resume: bool = False,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    require(confirmed is True, "session_execution_confirmation_required")
    approval = FrozenJSON.freeze(approval).to_dict()
    fingerprint = validate(root, approval, approved=True)
    path = root / approval["execution_path"]
    with batch.execution_lock(root):
        paths = checked_records(root)
        require(path in paths and path == paths[-1], "session_id_invalid")
        require(
            all((p / "complete.json").exists() for p in paths[:-1]),
            "session_operator_action_required",
        )
        draft = batch.load(path / "draft.json")
        require(
            {k: v for k, v in draft.items() if k not in orchestrator.GATES}
            == {k: v for k, v in approval.items() if k not in orchestrator.GATES},
            "session_approval_changed",
        )
        require(not (path / "complete.json").exists(), "session_stopped_or_complete")
        require(
            resume
            == ((path / "approval.json").exists() or (path / "approval-intent.json").exists()),
            "session_duplicate_or_missing_execution",
        )
        intent = {"parent_fingerprint": fingerprint, "draft_fingerprint": digest(draft)}
        if resume:
            if (path / "approval.json").exists():
                require(batch.load(path / "approval.json") == approval, "session_approval_changed")
            else:
                require(
                    batch.load(path / "approval-intent.json") == intent, "session_approval_changed"
                )
                unused_scope(root, approval)
                batch.write_once(path / "approval.json", approval)
            recoverable_stops(path, fingerprint)
            resume_check(root, approval)
            if not (path / "binding.json").exists():
                unused_scope(root, approval)
                if not (path / "approval-intent.json").exists():
                    batch.write_once(path / "approval-intent.json", intent)
                batch.write_once(path / "binding.json", binding_for(approval))
            record_resume(root, path, approval)
        else:
            # Validate the entire original Pending scope before persisting approval.
            _, first = orchestrator.inspect_next(root, len(approval["children"][0]["receipts"]))
            require(first == approval["children"][0], "session_scope_changed")
            view = verified_inventory(root)
            require(
                approval["receipts"]
                == [r for r, state in view["states"].items() if state == "Pending"][
                    : approval["max_requests"]
                ],
                "session_scope_changed",
            )
            require(
                all(
                    view["states"][r] == "Pending" and r not in view["claims"]
                    for r in approval["receipts"]
                ),
                "session_scope_changed",
            )
            batch.write_once(path / "approval-intent.json", intent)
            batch.write_once(path / "approval.json", approval)
            batch.write_once(path / "binding.json", binding_for(approval))
        token = _ACTIVE.set((root, path.name, os.getpid(), get_ident()))
        lock_token = batch._SESSION_LOCK.set((root, os.getpid(), get_ident()))
        try:
            for child in child_approvals(approval):
                execution_guard(root, child)
                plan = batch.plan_for(root)
                view = batch.inventory(root, plan)
                require(
                    not any(
                        s in {"Attempted", "Failed", "Blocked"} for s in view["states"].values()
                    ),
                    "session_ambiguous_or_failed_attempt",
                )
                scope = set(approval["receipts"])
                used = sum(r in scope for r in view["claims"])
                pending = sum(view["states"][r] == "Pending" for r in scope)
                require(used + pending <= approval["max_requests"], "session_budget_exceeded")
                unit = root / child["execution_path"]
                if (unit / "events/complete.json").exists():
                    require(batch.load(unit / "approval.json") == child, "session_child_mismatch")
                    continue
                if not unit.exists():
                    _, next_draft = orchestrator.inspect_next(root, len(child["receipts"]))
                    require(
                        next_draft == approval["children"][child_approvals(approval).index(child)],
                        "session_scope_changed",
                    )
                result = batch.execute(root, child, unit, resume=unit.exists(), http=http)
                require(
                    result["success"] and result["selected_replay_complete"], "session_child_failed"
                )
            view = verified_inventory(root)
            require(
                all(view["states"][r] == "Verified Local" for r in approval["receipts"]),
                "session_replay_failed",
            )
            completed = {
                "parent_fingerprint": fingerprint,
                "source_sha256": {r: view["hashes"][r] for r in approval["receipts"]},
            }
            history = resume_records(root, path, approval)
            if history:
                completed["recovery_fingerprint"] = history[-1]
            batch.write_once(path / "complete.json", completed)
        except Exception:
            record_stop(root, path, approval)
            raise ValueError("session_execution_stopped_operator_review_required") from None
        finally:
            batch._SESSION_LOCK.reset(lock_token)
            _ACTIVE.reset(token)
        return report(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--execute", type=Path)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--confirm-execution", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        require(
            args.execute is not None or not (args.confirm_execution or args.resume),
            "session_execution_confirmation_required",
        )
        if args.execute:
            result = execute(
                args.root,
                batch.load(args.execute),
                confirmed=args.confirm_execution,
                resume=args.resume,
            )
        elif args.prepare:
            result = prepare(args.root, args.max_requests)
        else:
            with orchestrator.snapshot_lock(args.root.resolve()):
                result = report(args.root.resolve())
        print("DART Multi-Batch Session")
        for key, value in result.items():
            print(f"{key}: {value}")
    except Exception:
        raise SystemExit(
            "DART_SESSION_BLOCKED: operator review required; no automatic retry"
        ) from None


if __name__ == "__main__":
    main()
