"""Build a deterministic review-only index from acquired DART document bytes."""

import argparse
import json
from pathlib import Path
from typing import Any, cast

from donghak_stock_vision.backtest.corporate_action_acquisition import (
    build_document_plan,
)
from donghak_stock_vision.backtest.corporate_action_document_review import (
    build_document_review_index,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    require(type(value) is dict, "metadata_object_required")
    return cast(dict[str, Any], value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    require(not args.output.exists(), "output_must_be_new_file")
    parent = _read(
        args.root.resolve()
        / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json"
    )
    plan = build_document_plan(parent)
    review = build_document_review_index(plan, args.sources.resolve())
    artifact = review.to_dict()
    wrapped = {"artifact": artifact, "fingerprint": digest(artifact)}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(wrapped, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        + "\n"
    )

    print(
        json.dumps(
            {
                "fingerprint": wrapped["fingerprint"],
                "document_count": artifact["document_count"],
                "indexed_document_count": artifact["indexed_document_count"],
                "unreadable_document_count": artifact["unreadable_document_count"],
                "no_text_document_count": artifact["no_text_document_count"],
                "documents_with_event_terms": artifact["documents_with_event_terms"],
                "documents_with_date_candidates": artifact["documents_with_date_candidates"],
                "corporate_action_gate_verified": artifact["corporate_action_gate_verified"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
