"""Verify explicitly supplied runner-tail evidence offline, without publishing readiness."""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.runner_tail import INPUT_VERSIONS, verify_runner_tail
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import canonical


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--expected-pins-fingerprint", required=True)
    parser.add_argument("--artifact", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "output_must_be_new_file")
    inputs = {}
    for specification in args.artifact:
        name, separator, path = specification.partition("=")
        require(
            bool(separator) and name in INPUT_VERSIONS and name not in inputs,
            "invalid_or_duplicate_artifact_argument",
        )
        inputs[name] = FrozenJSON(Path(path).read_text(encoding="utf-8"))
    pins = FrozenJSON(args.pins.read_text(encoding="utf-8"))
    result = verify_runner_tail(
        inputs, pins=pins, expected_pins_fingerprint=args.expected_pins_fingerprint
    )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(
            canonical({"artifact": result.to_dict(), "fingerprint": result.identifier}) + "\n"
        )
    value = result.to_dict()
    print(
        canonical(
            {
                "fingerprint": result.identifier,
                **{
                    key: value[key]
                    for key in (
                        "runner_tail_integrated",
                        "blockers",
                        "warmup_session_count",
                        "signal_session_count",
                        "execution_tail_end",
                        "final_settlement_date",
                        "run_input_frozen",
                        "backtest_authorized",
                    )
                },
            }
        )
    )


if __name__ == "__main__":
    main()
