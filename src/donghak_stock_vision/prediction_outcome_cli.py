"""Read-only original prediction -> future capture binding. No provider or orders."""

import argparse
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.prediction_binding import (
    FutureSource,
    PredictionSource,
    bind_outcome,
)


def _read(path: Path) -> FrozenJSON:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError("bounded_original_request_required")
    return FrozenJSON(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--journal-head", required=True)
    parser.add_argument("--entry-pin", required=True)
    parser.add_argument("--prediction-id", required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--request-pin", required=True)
    parser.add_argument("--policy-pin", required=True)
    parser.add_argument("--as-of", type=timestamp, required=True)
    parser.add_argument("--future-capture-root", type=Path)
    parser.add_argument("--future-request", type=Path)
    parser.add_argument("--future-request-pin")
    args = parser.parse_args(argv)
    try:
        original = PredictionSource(
            args.journal,
            args.journal_head,
            args.entry_pin,
            args.prediction_id,
            args.capture_root,
            _read(args.request),
            args.request_pin,
            args.policy_pin,
        )
        result = bind_outcome(original, args.as_of)
        refs = (args.future_capture_root, args.future_request, args.future_request_pin)
        if result.to_dict()["status"] == "unavailable" and any(v is not None for v in refs):
            if (
                args.future_capture_root is None
                or args.future_request is None
                or args.future_request_pin is None
            ):
                raise ValueError("future_capture_references_required_together")
            if args.future_request.exists():
                future = FutureSource(
                    args.future_capture_root, _read(args.future_request), args.future_request_pin
                )
                result = bind_outcome(original, args.as_of, future)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"binding input blocked: {error}\n")
    print(result.payload_json)
    return 2 if result.to_dict()["status"] == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
