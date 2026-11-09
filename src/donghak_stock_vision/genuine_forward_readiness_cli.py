"""Offline operator readiness review over a pinned Phase 6 E2E manifest."""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.prediction_outcome_e2e_cli import _pinned, _read, inputs
from donghak_stock_vision.storage.genuine_forward_readiness import (
    ReadinessInputs,
    assess_readiness,
    verify_readiness,
)
from donghak_stock_vision.storage.prospective_anchor_governance import AnchorSnapshot
from donghak_stock_vision.storage.prospective_evidence_audit import MAX_INPUT_BYTES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-pin", required=True)
    parser.add_argument("--as-of", type=timestamp, required=True)
    parser.add_argument("--anchor", type=Path)
    parser.add_argument("--anchor-pin")
    parser.add_argument("--issuer")
    parser.add_argument("--key-id")
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--report-pin")
    args = parser.parse_args(argv)
    try:
        require(
            (args.anchor is None)
            == (args.anchor_pin is None and args.issuer is None and args.key_id is None),
            "readiness_anchor_arguments_required_together",
        )
        require(
            (args.verify_report is None) == (args.report_pin is None),
            "readiness_report_arguments_required_together",
        )
        original = inputs(_read(args.input, MAX_INPUT_BYTES), args.input_pin)
        anchor = None
        if args.anchor is not None:
            require(
                args.issuer is not None and args.key_id is not None,
                "readiness_anchor_identity_required",
            )
            owner = _read(args.anchor, MAX_INPUT_BYTES)
            anchor = AnchorSnapshot(owner, args.anchor_pin)
        source = ReadinessInputs(original, anchor, args.issuer, args.key_id)
        if args.verify_report is None:
            report = assess_readiness(source, args.as_of)
        else:
            assert args.report_pin is not None
            report = _read(args.verify_report, MAX_INPUT_BYTES)
            verify_readiness(report, args.report_pin, source, args.as_of)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f"offline readiness blocked: {error}\n")
    print(report.payload_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
