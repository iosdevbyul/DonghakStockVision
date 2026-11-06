"""Read-only prospective evidence-structure audit. No performance or network access."""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prospective_evidence_audit import (
    MAX_INPUT_BYTES,
    audit_evidence,
    input_cases,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-pin", required=True)
    parser.add_argument("--as-of", type=timestamp, required=True)
    args = parser.parse_args(argv)
    try:
        if args.input.is_symlink() or not args.input.is_file():
            raise ValueError("audit_regular_input_required")
        # Bound the actual read, including a file that grows after a stat check.
        with args.input.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("audit_input_byte_budget")
        cases = input_cases(FrozenJSON(raw.decode("utf-8")), args.input_pin)
        report = audit_evidence(cases, args.as_of)
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "audit_input_invalid\n")
    print(report.payload_json)
    return 2 if report.to_dict()["counts"]["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
