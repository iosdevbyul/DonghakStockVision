"""Approval-bound batches using existing DART transport and offline evidence contracts."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from donghak_stock_vision.backtest.collect_corporate_action_documents import (
    PILOT_DIRECTORY,
    PILOT_RECEIPT,
    read,
)
from donghak_stock_vision.backtest.corporate_action_acquisition import (
    build_document_plan,
    inspect_document_sources,
)
from donghak_stock_vision.backtest.dart_transport import (
    ENDPOINT,
    DartAPIError,
    DartEvidenceClient,
    DartTransferLimits,
    request_identity,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest

DIRECTORY = ".data/dart-document-batch-runs"
VERSION = "dart_document_batch_approval_v1"
FIELDS = {
    "version",
    "endpoint",
    "plan_fingerprint",
    "receipts",
    "max_requests",
    "execution_path",
    "interval_seconds",
    "retries",
    "approved_at",
    "access_confirmed",
    "quota_confirmed",
}


def load(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "batch_record_missing")
    # FrozenJSON rejects duplicate JSON keys before approval validation.
    return FrozenJSON(path.read_text()).to_dict()


def write_once(path: Path, value: dict[str, Any]) -> None:
    """Persist each immutable marker before proceeding; partial records fail closed."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def plan_for(root: Path) -> FrozenJSON:
    plan = build_document_plan(
        read(root / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json")
    )
    require(plan.to_dict()["document_count"] == 3108, "batch_plan_count_mismatch")
    return plan


def approval_check(plan: FrozenJSON, value: dict[str, Any]) -> str:
    require(set(value) == FIELDS and value["version"] == VERSION, "batch_approval_required")
    require(value["endpoint"] == ENDPOINT + "document.xml", "batch_endpoint_mismatch")
    require(value["plan_fingerprint"] == plan.identifier, "batch_plan_fingerprint_mismatch")
    require(
        value["access_confirmed"] is True and value["quota_confirmed"] is True,
        "batch_access_unconfirmed",
    )
    require(
        type(value["max_requests"]) is int and 1 <= value["max_requests"] <= 25,
        "batch_request_budget_invalid",
    )
    require(type(value["retries"]) is int and value["retries"] == 0, "batch_retries_forbidden")
    interval = value["interval_seconds"]
    require(
        type(interval) in (int, float) and math.isfinite(interval) and interval >= 1,
        "batch_interval_invalid",
    )
    stamp = value["approved_at"]
    require(isinstance(stamp, str), "batch_approval_time_required")
    instant = datetime.fromisoformat(stamp)
    offset = instant.utcoffset()
    require(
        offset is not None and offset.total_seconds() == 0 and instant <= datetime.now(UTC),
        "batch_approval_time_invalid",
    )
    receipts = value["receipts"]
    require(type(receipts) is list and 1 <= len(receipts) <= 25, "batch_scope_invalid")
    require(
        all(isinstance(r, str) and re.fullmatch(r"[0-9]{14}", r) for r in receipts),
        "batch_receipt_invalid",
    )
    require(receipts == sorted(set(receipts)), "batch_scope_not_deterministic")
    expected = {row["rcept_no"] for row in plan.to_dict()["documents"]}
    require(set(receipts) <= expected and PILOT_RECEIPT not in receipts, "batch_receipt_unapproved")
    path = value["execution_path"]
    require(
        isinstance(path, str)
        and re.fullmatch(re.escape(DIRECTORY) + r"/runs/batch-[0-9]{4}", path) is not None,
        "batch_execution_path_invalid",
    )
    return digest(value)


def source_hashes(plan: FrozenJSON, directory: Path) -> dict[str, str]:
    require(directory.is_dir() and not directory.is_symlink(), "batch_source_directory_invalid")
    for path in directory.iterdir():
        if path.suffix in {".json", ".gz"}:
            require(not path.is_symlink() and path.is_file(), "batch_source_file_invalid")
    result = inspect_document_sources(plan, directory, limits=DartTransferLimits()).to_dict()
    require(result["unexpected_document_count"] == 0, "batch_unexpected_source")
    for receipt in directory.glob("*.json"):
        meta = load(receipt)
        identity = request_identity("document.xml", meta["params"])
        require(
            all(meta.get(k) == v for k, v in identity.items()), "batch_source_identity_mismatch"
        )
    return dict(result["source_sha256"])


def inventory(root: Path, plan: FrozenJSON) -> dict[str, Any]:
    """Read immutable claims and revalidate every available source; never dispatch HTTP."""
    states = {row["rcept_no"]: "Pending" for row in plan.to_dict()["documents"]}
    pilot_dir = root / PILOT_DIRECTORY
    require(
        all(p.with_suffix(".json").is_file() for p in pilot_dir.glob("*.gz")),
        "batch_pilot_partial_source",
    )
    pilot = source_hashes(plan, pilot_dir)
    require(set(pilot) == {PILOT_RECEIPT}, "batch_verified_pilot_required")
    hashes = dict(pilot)
    states[PILOT_RECEIPT] = "Verified Local"
    ledger = root / DIRECTORY
    claims: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}
    owned_hashes: dict[str, dict[str, str]] = {}
    if ledger.exists():
        require(not ledger.is_symlink(), "batch_ledger_symlink")
        require(
            all(
                (ledger / name).is_dir() and not (ledger / name).is_symlink()
                for name in ("runs", "claims")
            ),
            "batch_ledger_directory_invalid",
        )
        for unit in sorted((ledger / "runs").iterdir()):
            require(unit.is_dir() and not unit.is_symlink(), "batch_run_directory_invalid")
            require(
                all(
                    (unit / name).is_dir() and not (unit / name).is_symlink()
                    for name in ("sources", "events", "attempts")
                ),
                "batch_run_directory_invalid",
            )
            approval = load(unit / "approval.json")
            fingerprint = approval_check(plan, approval)
            require(
                (root / approval["execution_path"]).resolve() == unit.resolve(),
                "batch_run_path_mismatch",
            )
            runs[fingerprint] = approval
            sources = unit / "sources"
            # An orphan body is an ambiguous partial save, never a cache miss.
            orphan = {p.stem for p in sources.glob("*.gz") if not p.with_suffix(".json").is_file()}
            for stem in orphan:
                matches = [
                    r
                    for r in approval["receipts"]
                    if digest(request_identity("document.xml", {"rcept_no": r})) == stem
                ]
                require(len(matches) == 1, "batch_unknown_orphan_body")
                states[matches[0]] = "Blocked"
            local = source_hashes(plan, sources)
            owned_hashes[fingerprint] = local
            require(set(local) <= set(approval["receipts"]), "batch_source_outside_approval")
            for receipt, sha in local.items():
                require(receipt not in hashes, "batch_duplicate_source")
                hashes[receipt] = sha
                states[receipt] = "Verified Local"
        for claim_path in sorted((ledger / "claims").glob("*.json")):
            claim = load(claim_path)
            receipt = str(claim.get("receipt", ""))
            fingerprint = str(claim.get("approval_fingerprint", ""))
            require(
                receipt in states and claim_path.stem == receipt and fingerprint in runs,
                "batch_claim_invalid",
            )
            approval = runs[fingerprint]
            require(
                receipt in approval["receipts"]
                and claim.get("request_identity")
                == digest(request_identity("document.xml", {"rcept_no": receipt})),
                "batch_claim_scope_mismatch",
            )
            claims[receipt] = claim
            result_path = root / approval["execution_path"] / "events" / f"{receipt}.json"
            if result_path.exists():
                result = load(result_path)
                require(
                    result.get("approval_fingerprint") == fingerprint
                    and result.get("receipt") == receipt
                    and result.get("state") in {"Verified Local", "Failed"},
                    "batch_result_invalid",
                )
                if result["state"] == "Verified Local":
                    require(
                        receipt in owned_hashes[fingerprint]
                        and result.get("sha256") == owned_hashes[fingerprint][receipt],
                        "batch_success_evidence_lost",
                    )
                else:
                    states[receipt] = "Failed" if states[receipt] != "Blocked" else "Blocked"
            elif states[receipt] != "Verified Local" and states[receipt] != "Blocked":
                states[receipt] = "Attempted"
        for fingerprint, approval in runs.items():
            unit = root / approval["execution_path"]
            for marker in (unit / "attempts").glob("*.json"):
                require(
                    marker.stem in claims and load(marker) == claims[marker.stem],
                    "batch_attempt_record_lost",
                )
            for event in (unit / "events").glob("*.json"):
                if event.name != "complete.json":
                    require(
                        event.stem in claims
                        and claims[event.stem]["approval_fingerprint"] == fingerprint,
                        "batch_result_claim_missing",
                    )
            complete = unit / "events/complete.json"
            if complete.exists():
                completed = load(complete)
                require(
                    completed.get("approval_fingerprint") == fingerprint
                    and completed.get("source_sha256")
                    == {r: hashes.get(r) for r in approval["receipts"]}
                    and all(r in hashes for r in approval["receipts"]),
                    "batch_completed_evidence_lost",
                )
            require(
                sum(c["approval_fingerprint"] == fingerprint for c in claims.values())
                <= approval["max_requests"],
                "batch_saved_budget_exceeded",
            )
            for receipt in owned_hashes[fingerprint]:
                require(
                    receipt in claims
                    and claims[receipt]["approval_fingerprint"] == fingerprint
                    and (unit / "attempts" / f"{receipt}.json").is_file(),
                    "batch_source_claim_missing",
                )
    return {"states": states, "hashes": hashes, "claims": claims}


def summary(plan: FrozenJSON, view: dict[str, Any]) -> dict[str, Any]:
    counts = {
        name: sum(state == name for state in view["states"].values())
        for name in ("Pending", "Verified Local", "Attempted", "Failed", "Blocked")
    }
    return {
        "plan_fingerprint": plan.identifier,
        "document_count": 3108,
        "counts": counts,
        "documents": view["states"],
        "corporate_actions": "blocked",
        "corporate_action_coverage": "0/993",
        "runner_tail": "blocked",
        "run_input_freeze": "blocked",
        "event_approval_performed": False,
    }


@contextmanager
def execution_lock(root: Path) -> Iterator[Path]:
    # The local Mac/Linux executor uses a kernel lock, released even on process death.
    require(os.name == "posix", "batch_platform_lock_unavailable")
    import fcntl

    ledger = root / DIRECTORY
    for path in (root / ".data", ledger, ledger / "runs", ledger / "claims"):
        require(not path.is_symlink(), "batch_ledger_symlink")
        path.mkdir(exist_ok=True)
    lock = ledger / "execution.lock"
    require(not lock.is_symlink(), "batch_ledger_symlink")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("batch_execution_active") from None
        try:
            yield ledger
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def execute(
    root: Path,
    approval: dict[str, Any],
    output: Path,
    *,
    resume: bool = False,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """Only an exact attested record permits bounded requests; exceptions stop the unit."""
    root = root.resolve()
    approval = FrozenJSON.freeze(approval).to_dict()
    plan = plan_for(root)
    fingerprint = approval_check(plan, approval)
    expected_output = root / approval["execution_path"]
    require(
        output.resolve() == expected_output.resolve() and not expected_output.is_symlink(),
        "batch_output_scope_mismatch",
    )
    key = dotenv_values(root / ".env").get("DART_API_KEY")
    require(isinstance(key, str) and len(key) == 40, "batch_key_not_configured")
    assert isinstance(key, str)
    require(key not in json.dumps(approval), "batch_unsafe_approval")
    with execution_lock(root) as ledger:
        view = inventory(root, plan)
        require(
            not any(s == "Blocked" for s in view["states"].values()),
            "batch_existing_evidence_blocked",
        )
        require(resume == expected_output.exists(), "batch_duplicate_or_missing_execution")
        if resume:
            require(
                load(expected_output / "approval.json") == approval,
                "batch_resume_approval_mismatch",
            )
            require(
                not (expected_output / "events/complete.json").exists(), "batch_execution_complete"
            )
        receipts = approval["receipts"]
        require(
            all(view["states"][r] in {"Pending", "Verified Local"} for r in receipts),
            "batch_ambiguous_or_failed_attempt",
        )
        claims = view["claims"]
        require(
            all(
                r not in claims
                or claims[r]["approval_fingerprint"] == fingerprint
                or view["states"][r] == "Verified Local"
                for r in receipts
            ),
            "batch_document_already_attempted",
        )
        used = sum(c["approval_fingerprint"] == fingerprint for c in claims.values())
        pending = [r for r in receipts if view["states"][r] == "Pending"]
        require(used + len(pending) <= approval["max_requests"], "batch_approved_budget_exceeded")
        if not resume:
            expected_output.mkdir()
            (expected_output / "sources").mkdir()
            (expected_output / "events").mkdir()
            (expected_output / "attempts").mkdir()
            write_once(expected_output / "approval.json", approval)
        # Reconcile complete source+receipt after a crash; never repeat its request.
        for receipt in receipts:
            result_path = expected_output / "events" / f"{receipt}.json"
            if (
                receipt in claims
                and claims[receipt]["approval_fingerprint"] == fingerprint
                and not result_path.exists()
            ):
                write_once(
                    result_path,
                    {
                        "approval_fingerprint": fingerprint,
                        "receipt": receipt,
                        "state": "Verified Local",
                        "sha256": view["hashes"][receipt],
                    },
                )
        owned = http is None
        if pending and http is None:
            http = httpx.Client(
                timeout=45, transport=httpx.HTTPTransport(retries=0), trust_env=False
            )
        failure = False
        try:
            for receipt in pending:
                require(used < approval["max_requests"], "batch_approved_budget_exceeded")
                write_once(
                    ledger / "claims" / f"{receipt}.json",
                    {
                        "receipt": receipt,
                        "request_identity": digest(
                            request_identity("document.xml", {"rcept_no": receipt})
                        ),
                        "approval_fingerprint": fingerprint,
                        "reserved_at": datetime.now(UTC).isoformat(),
                    },
                )
                write_once(
                    expected_output / "attempts" / f"{receipt}.json",
                    load(ledger / "claims" / f"{receipt}.json"),
                )
                used += 1
                # At least one second after the previous response, also on resumption.
                time.sleep(approval["interval_seconds"])
                require(http is not None, "batch_http_client_required")
                assert http is not None
                client = DartEvidenceClient(
                    key,
                    expected_output / "sources",
                    http,
                    max_attempts=1,
                    limits=DartTransferLimits(),
                )
                event: dict[str, Any] = {"approval_fingerprint": fingerprint, "receipt": receipt}
                try:
                    _, meta = client.fetch("document.xml", {"rcept_no": receipt})
                    verified = source_hashes(plan, expected_output / "sources")
                    require(
                        verified.get(receipt) == meta["raw_sha256"], "batch_saved_source_unverified"
                    )
                    event.update(state="Verified Local", sha256=meta["raw_sha256"])
                except Exception as error:
                    failure = True
                    event.update(state="Failed", failure_code="batch_acquisition_stopped")
                    if isinstance(error, DartAPIError):
                        event.update(api_status=error.status, api_category=error.category)
                event.update(client.last_response)
                write_once(expected_output / "events" / f"{receipt}.json", event)
                if failure:
                    break
        finally:
            if owned and http is not None:
                http.close()
        view = inventory(root, plan)
        if not failure:
            write_once(
                expected_output / "events/complete.json",
                {
                    "approval_fingerprint": fingerprint,
                    "reserved_requests": used,
                    "source_sha256": {r: view["hashes"][r] for r in receipts},
                },
            )
        return {
            **summary(plan, view),
            "success": not failure,
            "reserved_requests": used,
            "selected_replay_complete": all(
                view["states"][r] == "Verified Local" for r in receipts
            ),
            "full_document_sources_complete": len(view["hashes"]) == 3108,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only DART batch inventory; no API requests")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = plan_for(args.root)
        view = inventory(args.root, plan)
        report = summary(plan, view)
        pending = [r for r, state in view["states"].items() if state == "Pending"][:25]
        report["unapproved_template"] = {
            "version": VERSION,
            "endpoint": ENDPOINT + "document.xml",
            "plan_fingerprint": plan.identifier,
            "receipts": pending,
            "max_requests": len(pending),
            "execution_path": DIRECTORY + "/runs/batch-0001",
            "interval_seconds": 1,
            "retries": 0,
            "approved_at": None,
            "access_confirmed": False,
            "quota_confirmed": False,
        }
        print(json.dumps(report, sort_keys=True))
    except Exception:
        print(
            json.dumps(
                {
                    "state": "Blocked",
                    "failure_code": "offline_evidence_inspection_failed",
                    "corporate_actions": "blocked",
                }
            )
        )
        raise SystemExit("DART_BATCH_BLOCKED: offline evidence inspection failed") from None


if __name__ == "__main__":
    main()
