"""Batch-safe DART document acquisition for corporate-action review."""

import argparse
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values

from donghak_stock_vision.backtest.corporate_action_acquisition import build_document_plan
from donghak_stock_vision.backtest.dart_transport import DartAPIError, DartEvidenceClient
from donghak_stock_vision.backtest.validation import require


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
    corporate_actions = read(
        root / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json"
    )
    plan = build_document_plan(corporate_actions).to_dict()
    receipts = [row["rcept_no"] for row in plan["documents"]]
    selected = receipts[offset : offset + limit]

    with httpx.Client(timeout=45) as http:
        client = DartEvidenceClient(
            dotenv_values(root / ".env").get("DART_API_KEY"),
            output,
            http,
            max_attempts=max_attempts,
        )
        for receipt in selected:
            client.fetch("document.xml", {"rcept_no": receipt})

    return {
        "planned_documents": len(receipts),
        "batch_offset": offset,
        "batch_limit": limit,
        "selected_documents": len(selected),
        "preserved_receipts": len(tuple(output.glob("*.json"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--max-attempts", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    try:
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
