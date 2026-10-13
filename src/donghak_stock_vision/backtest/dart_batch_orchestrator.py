"""Offline Prepare/Status composition; no execution or approval promotion interface."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest.dart_transport import ENDPOINT
from donghak_stock_vision.backtest.validation import require

BLOCK_REASONS = {
    "orchestrator_batch_size_invalid",
    "orchestrator_execution_active",
    "orchestrator_incomplete_execution",
    "orchestrator_unresolved_documents",
    "orchestrator_duplicate_approval_scope",
    "orchestrator_batch_sequence_invalid",
    "orchestrator_record_name_invalid",
    "orchestrator_approval_record_mismatch",
    "orchestrator_approved_execution_pending",
    "orchestrator_draft_record_mismatch",
    "orchestrator_orphan_draft",
    "orchestrator_draft_exists",
    "orchestrator_path_invalid",
    "batch_execution_active",
    "batch_plan_fingerprint_mismatch",
}
GATES = {"access_confirmed", "quota_confirmed", "approved_at"}


def draft_for(plan_fingerprint: str, receipts: list[str], identifier: str) -> dict[str, Any]:
    """Construct the existing exact schema with deliberately unconfirmed gates."""
    return {
        "version": batch.VERSION,
        "endpoint": ENDPOINT + "document.xml",
        "plan_fingerprint": plan_fingerprint,
        "receipts": receipts,
        "max_requests": len(receipts),
        "execution_path": f"{batch.DIRECTORY}/runs/{identifier}",
        "interval_seconds": 1,
        "retries": 0,
        "approved_at": None,
        "access_confirmed": False,
        "quota_confirmed": False,
    }


def draft_matches(draft: dict[str, Any], approved: dict[str, Any]) -> bool:
    return (
        set(draft) == batch.FIELDS
        and draft.get("approved_at") is None
        and draft.get("access_confirmed") is False
        and draft.get("quota_confirmed") is False
        and {k: v for k, v in draft.items() if k not in GATES}
        == {k: v for k, v in approved.items() if k not in GATES}
    )


@contextmanager
def snapshot_lock(root: Path) -> Iterator[None]:
    """Read an existing kernel lock without creating any files in Status mode."""
    lock = root / batch.DIRECTORY / "execution.lock"
    require(not lock.is_symlink(), "orchestrator_path_invalid")
    if not lock.exists():
        yield
        return
    require(os.name == "posix", "orchestrator_path_invalid")
    import fcntl

    with lock.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("orchestrator_execution_active") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def inspect_next(root: Path, size: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Compose validated inventory and immutable control records before selecting Pending."""
    require(type(size) is int and 1 <= size <= 25, "orchestrator_batch_size_invalid")
    require(not (root / ".data").is_symlink(), "orchestrator_path_invalid")
    plan = batch.plan_for(root)
    view = batch.inventory(root, plan)
    base = batch.summary(plan, view)
    require(
        all(base["counts"][s] == 0 for s in ("Attempted", "Failed", "Blocked")),
        "orchestrator_unresolved_documents",
    )
    units: dict[str, dict[str, Any]] = {}
    approved_receipts: set[str] = set()
    runs = root / batch.DIRECTORY / "runs"
    if runs.exists():
        for unit in sorted(runs.iterdir()):
            require(
                re.fullmatch(r"batch-[0-9]{4}", unit.name) is not None,
                "orchestrator_batch_sequence_invalid",
            )
            require((unit / "events/complete.json").is_file(), "orchestrator_incomplete_execution")
            record = batch.load(unit / "approval.json")
            batch.approval_check(plan, record)
            require(
                not approved_receipts.intersection(record["receipts"]),
                "orchestrator_duplicate_approval_scope",
            )
            approved_receipts.update(record["receipts"])
            units[unit.name] = record
    numbers = sorted(int(identifier[-4:]) for identifier in units)
    require(
        numbers == list(range(1, max(numbers, default=0) + 1)),
        "orchestrator_batch_sequence_invalid",
    )
    next_number = max(numbers, default=0) + 1
    require(next_number <= 9999, "orchestrator_batch_sequence_invalid")
    identifier = f"batch-{next_number:04d}"
    pending = [
        row["rcept_no"]
        for row in plan.to_dict()["documents"]
        if view["states"][row["rcept_no"]] == "Pending"
        and row["rcept_no"] not in approved_receipts
        and row["rcept_no"] not in view["claims"]
    ]
    selected = pending[:size]
    proposed = draft_for(plan.identifier, selected, identifier) if selected else None
    draft_path = root / ".data" / f"dart-{identifier}-draft.json"
    draft_exists = False
    for path in sorted((root / ".data").glob("dart-batch-*-approved.json")):
        match = re.fullmatch(r"dart-(batch-[0-9]{4})-approved\.json", path.name)
        require(match is not None, "orchestrator_record_name_invalid")
        assert match is not None
        record = batch.load(path)
        batch.approval_check(plan, record)
        require(
            record["execution_path"] == f"{batch.DIRECTORY}/runs/{match[1]}",
            "orchestrator_approval_record_mismatch",
        )
        require(match[1] in units, "orchestrator_approved_execution_pending")
        require(record == units[match[1]], "orchestrator_approval_record_mismatch")
    for path in sorted((root / ".data").glob("dart-batch-*-draft.json")):
        match = re.fullmatch(r"dart-(batch-[0-9]{4})-draft\.json", path.name)
        require(match is not None, "orchestrator_record_name_invalid")
        assert match is not None
        record = batch.load(path)
        if match[1] in units:
            require(draft_matches(record, units[match[1]]), "orchestrator_draft_record_mismatch")
        else:
            require(match[1] == identifier and proposed is not None, "orchestrator_orphan_draft")
            # An existing next draft remains its own unit; never skip it or silently rewrite scope.
            recorded_receipts = record.get("receipts")
            require(
                isinstance(recorded_receipts, list) and 1 <= len(recorded_receipts) <= 25,
                "orchestrator_draft_record_mismatch",
            )
            assert isinstance(recorded_receipts, list)
            expected = draft_for(plan.identifier, pending[: len(recorded_receipts)], identifier)
            require(record == expected, "orchestrator_draft_record_mismatch")
            proposed = record
            selected = recorded_receipts
            draft_exists = True
    report = {
        "total": base["document_count"],
        "counts": base["counts"],
        "plan_fingerprint": plan.identifier,
        "next_batch": identifier if selected else None,
        "selected_count": len(selected),
        "execution_path": proposed["execution_path"] if proposed else None,
        "draft_path": str(draft_path.relative_to(root)) if proposed else None,
        "draft_exists": draft_exists,
        "draft_created": False,
        "approval": "REQUIRED" if selected else "NOT REQUIRED",
        "execution": "NOT STARTED",
        "offline_replay": "VERIFIED",
        "blocked": False,
        "block_reason": None,
        "complete": not pending,
        "corporate_actions": "blocked",
        "corporate_action_coverage": "0/993",
        "runner_tail": "blocked",
        "run_input_freeze": "blocked",
    }
    return report, proposed


def blocked_report(error: Exception) -> dict[str, Any]:
    # Never display arbitrary filesystem/server/JSON messages or a full request URL.
    reason = str(error)
    return {
        "total": 3108,
        "counts": None,
        "next_batch": None,
        "selected_count": 0,
        "plan_fingerprint": None,
        "execution_path": None,
        "draft_path": None,
        "approval": "REQUIRED",
        "execution": "NOT STARTED",
        "offline_replay": "NOT VERIFIED",
        "blocked": True,
        "block_reason": reason if reason in BLOCK_REASONS else "evidence_validation_failed",
        "corporate_actions": "blocked",
        "corporate_action_coverage": "0/993",
        "runner_tail": "blocked",
        "run_input_freeze": "blocked",
    }


def status(root: Path, *, size: int = 25) -> dict[str, Any]:
    try:
        with snapshot_lock(root.resolve()):
            report, _ = inspect_next(root.resolve(), size)
        return report
    except Exception as error:
        return blocked_report(error)


def prepare_next(root: Path, *, size: int = 25) -> dict[str, Any]:
    """Create only a new unapproved draft under the existing executor's global lock."""
    root = root.resolve()
    try:
        # Preflight avoids creating even ledger parents when evidence/control records are blocked.
        with snapshot_lock(root):
            inspect_next(root, size)
        with batch.execution_lock(root):
            report, proposed = inspect_next(root, size)
            if proposed is None:
                return report
            require(not report["draft_exists"], "orchestrator_draft_exists")
            path = root / report["draft_path"]
            require(not path.exists() and not path.is_symlink(), "orchestrator_draft_exists")
            batch.write_once(path, proposed)
            report.update(draft_created=True, draft_exists=True)
            return report
    except Exception as error:
        return blocked_report(error)


def render(report: dict[str, Any]) -> str:
    lines = ["DART Batch Status", "-----------------", f"Total: {report['total']}"]
    counts = report["counts"]
    for label, state in [
        ("Verified", "Verified Local"),
        ("Pending", "Pending"),
        ("Attempted", "Attempted"),
        ("Failed", "Failed"),
        ("Blocked", "Blocked"),
    ]:
        lines.append(f"{label}: {counts[state] if counts is not None else 'UNVERIFIED'}")
    lines.extend(
        [
            "",
            f"Next batch: {report['next_batch'] or 'NONE'}",
            f"Selected: {report['selected_count']}",
            f"Plan fingerprint: {report['plan_fingerprint'] or 'UNVERIFIED'}",
            f"Execution path: {report['execution_path'] or 'NONE'}",
            f"Draft: {report['draft_path'] or 'NONE'}",
            f"Approval: {report['approval']}",
            f"Execution: {report['execution']}",
            f"Offline Replay: {report['offline_replay']}",
            f"Block reason: {report['block_reason'] or 'NONE'}",
            "Corporate Actions: blocked (0/993)",
            "Runner Tail / Run Input Freeze: blocked",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-next", action="store_true")
    modes.add_argument("--status", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    report = (
        prepare_next(args.root, size=args.limit)
        if args.prepare_next
        else status(args.root, size=args.limit)
    )
    print(render(report))
    if report["blocked"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
