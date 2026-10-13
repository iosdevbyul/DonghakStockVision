"""Batch-safe DART document acquisition for corporate-action review."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import httpx
from dotenv import dotenv_values

from donghak_stock_vision.backtest.corporate_action_acquisition import (
    build_document_plan,
    inspect_document_sources,
)
from donghak_stock_vision.backtest.dart_transport import (
    ENDPOINT,
    DartAPIError,
    DartEvidenceClient,
    DartTransferLimits,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require

PILOT_RECEIPT = "20240603800041"
PREVIOUS_PILOT_DIRECTORY = ".data/dart-document-live-pilot-20261013"
PILOT_DIRECTORY = ".data/dart-document-live-pilot-retry-20261013"


def read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    require(type(value) is dict, "metadata_object_required")
    return dict(value)


def collect(
    root: Path,
    output: Path,
    *,
    offset: int,
    limit: int,
    max_attempts: int = 3,
) -> dict[str, int]:
    """Collect one deterministic batch without approving any corporate action."""

    require(offset >= 0, "invalid_document_batch_offset")
    require(limit > 0, "invalid_document_batch_limit")
    require(
        not any(
            output.resolve().is_relative_to((root / directory).resolve())
            for directory in (PREVIOUS_PILOT_DIRECTORY, PILOT_DIRECTORY)
        ),
        "pilot_path_requires_single_request_mode",
    )
    require(False, "batch_approval_record_required")
    return {}  # Unreachable: legacy unapproved network dispatch is deliberately disabled.


def pilot(
    root: Path, *, access_confirmed: bool, http: httpx.Client | None = None
) -> dict[str, object]:
    """One fixed receipt, one attempt, fresh directory; only after operator confirmation."""

    require(access_confirmed is True, "pilot_access_and_quota_not_confirmed")
    output = root / PILOT_DIRECTORY
    require(not output.exists() and not output.is_symlink(), "pilot_output_must_be_new")
    key = dotenv_values(root / ".env").get("DART_API_KEY")
    require(isinstance(key, str) and len(key) == 40, "pilot_local_key_not_configured")
    parent = read(root / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json")
    plan = build_document_plan(parent)
    selected = [r for r in plan.to_dict()["documents"] if r["rcept_no"] == PILOT_RECEIPT]
    require(len(selected) == 1, "pilot_receipt_missing_from_plan")
    pilot_plan = FrozenJSON.freeze(
        {
            **plan.to_dict(),
            "documents": selected,
            "document_count": 1,
            "pilot_parent_plan_fingerprint": plan.identifier,
        }
    )
    limits = DartTransferLimits()
    # Directory creation is also a persistent one-shot latch, including after a crash.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    owned_http = http is None
    client: DartEvidenceClient | None = None
    report: dict[str, object] = {
        "version": "dart_document_live_pilot_v1",
        "endpoint": ENDPOINT + "document.xml",
        "rcept_no": PILOT_RECEIPT,
        "limits": asdict(limits),
        "access_confirmation": "operator_attested",
        "success": False,
        "api_status": None,
        "api_category": None,
        "normal_zip_verified": False,
        "corporate_action_gate_verified": False,
        "historical_identity_verified": False,
        "prediction_or_backtest_invoked": False,
        "attempt_count": 0,
        "http_status": None,
        "bytes_read": 0,
        "response_complete": False,
    }
    try:
        if http is None:
            http = httpx.Client(
                timeout=45, transport=httpx.HTTPTransport(retries=0), trust_env=False
            )
        client = DartEvidenceClient(key, output, http, max_attempts=1, limits=limits)
        raw, metadata = client.fetch("document.xml", {"rcept_no": PILOT_RECEIPT})
        replay = inspect_document_sources(pilot_plan, output, limits=limits).to_dict()
        full_replay = inspect_document_sources(plan, output, limits=limits).to_dict()
        report.update(
            success=True,
            normal_zip_verified=True,
            response_bytes=len(raw),
            response_sha256=metadata["raw_sha256"],
            offline_replay_complete=replay["complete"],
            pilot_preserved_document_count=replay["preserved_document_count"],
            full_plan_complete=full_replay["complete"],
            full_plan_missing_document_count=full_replay["missing_document_count"],
        )
    except DartAPIError as error:
        report.update(api_status=error.status, api_category=error.category)
    except ValueError as error:
        code = str(error)
        known = {
            "DART_RESPONSE_LIMIT_EXCEEDED",
            "DART_CONTENT_ENCODING_BLOCKED",
            "DART_TRANSPORT_BLOCKED",
            "DART_OFFICIAL_ERROR",
            "DART_DOCUMENT_MIME_BLOCKED",
            "unsafe_response_not_preserved",
        }
        report["failure_code"] = code if code in known else "pilot_validation_blocked"
    except Exception:
        report["failure_code"] = "pilot_unexpected_failure"
    finally:
        if owned_http and http is not None:
            try:
                http.close()
            except Exception:
                report["client_close_failed"] = True
        if client is not None:
            report.update(client.last_response)
        audit = output / "audit"
        audit.mkdir()
        with (audit / "pilot-report.json").open("x", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True)
            handle.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--max-attempts", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--live-pilot", action="store_true")
    parser.add_argument("--access-confirmed", action="store_true")
    parser.add_argument("--batch-approval", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        if args.live_pilot:
            require(args.batch_approval is None and not args.resume, "pilot_batch_scope_conflict")
            require(
                args.output.resolve() == (args.root.resolve() / PILOT_DIRECTORY),
                "pilot_output_path_mismatch",
            )
            report = pilot(args.root.resolve(), access_confirmed=args.access_confirmed)
            print(json.dumps(report, sort_keys=True))
            if report["success"] is not True:
                raise SystemExit("DART_DOCUMENT_PILOT_BLOCKED: inspect safe pilot audit")
            return
        if args.batch_approval is not None:
            from donghak_stock_vision.backtest.dart_document_batch import execute, load

            require(
                args.offset == 0 and args.limit == 25 and args.max_attempts == 3,
                "batch_legacy_flags_forbidden",
            )
            report = execute(args.root, load(args.batch_approval), args.output, resume=args.resume)
            print(json.dumps(report, sort_keys=True))
            if not report["success"]:
                raise SystemExit("DART_BATCH_BLOCKED: inspect safe execution records")
            return
        require(not args.resume, "batch_approval_record_required")
        result = collect(
            args.root.resolve(),
            args.output.resolve(),
            offset=args.offset,
            limit=args.limit,
            max_attempts=args.max_attempts,
        )
    except DartAPIError as error:
        raise SystemExit(f"CORPORATE_ACTION_DOCUMENT_ACQUISITION_BLOCKED: {error}") from None
    except Exception:
        raise SystemExit(
            "CORPORATE_ACTION_DOCUMENT_ACQUISITION_BLOCKED: inspect sanitized receipts"
        ) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
