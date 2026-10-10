"""Validate reviewer-authored decisions offline; emit aggregate metadata only."""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.corporate_action_semantic_review import build_semantic_review
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import canonical


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "output_must_be_new_file")
    index = FrozenJSON(args.index.read_text(encoding="utf-8"))
    decisions = FrozenJSON(args.decisions.read_text(encoding="utf-8"))
    review = build_semantic_review(index.to_dict(), decisions)
    wrapped = canonical({"artifact": review.to_dict(), "fingerprint": review.identifier}) + "\n"
    # Exclusive creation also protects against a competing writer after validation.
    with args.output.open("x", encoding="utf-8") as output:
        output.write(wrapped)
    value = review.to_dict()
    print(
        canonical(
            {
                "fingerprint": review.identifier,
                **{
                    key: value[key]
                    for key in (
                        "decision_record_count",
                        "reviewed_document_count",
                        "approved_event_count",
                        "unreviewed_document_count",
                        "unresolved_record_count",
                        "pending_document_security_pair_count",
                        "corporate_action_gate_verified",
                    )
                },
            }
        )
    )


if __name__ == "__main__":
    main()
