"""Offline future-capture inspection; no prediction grading or provider requests."""

import argparse
import sqlite3
from datetime import date
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.future_observation import resolve_observation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--request-pin")
    parser.add_argument("--target", type=date.fromisoformat, required=True)
    for name in ("recorded-at", "not-before", "available-by", "as-of"):
        parser.add_argument(f"--{name}", type=timestamp, required=True)
    args = parser.parse_args(argv)
    try:
        request = None
        root = None
        pin = None
        if args.as_of >= args.not_before:
            if any(v is not None for v in (args.capture_root, args.request, args.request_pin)):
                if args.capture_root is None or args.request is None or args.request_pin is None:
                    raise ValueError("future_capture_references_required_together")
                if (
                    args.request.is_symlink()
                    or not args.request.is_file()
                    or args.request.stat().st_size > 256 * 1024
                ):
                    raise ValueError("bounded_future_request_required")
                request = FrozenJSON(args.request.read_text())
                root, pin = args.capture_root, args.request_pin
        result = resolve_observation(
            root,
            request,
            pin,
            recorded_at=args.recorded_at,
            target_day=args.target,
            not_before=args.not_before,
            available_by=args.available_by,
            as_of=args.as_of,
        )
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"future observation blocked: {error}\n")
    print(result.payload_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
