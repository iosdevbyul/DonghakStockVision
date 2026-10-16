"""Offline metadata compilation/diagnostics; no acquisition or DB access."""

import argparse
from pathlib import Path

from pydantic import ValidationError

from donghak_stock_vision.api.security_metadata import (
    build_metadata,
    diagnose,
    export_metadata,
)
from donghak_stock_vision.api.snapshot import ResearchAPISnapshot
from donghak_stock_vision.data.learning import canonical


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m donghak_stock_vision.security_metadata_cli")
    result.add_argument("--root", type=Path, default=Path("."))
    result.add_argument("--snapshot", type=Path, required=True)
    result.add_argument(
        "--output", type=Path, help="new envelope path; existing files are never replaced"
    )
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        research = ResearchAPISnapshot.model_validate_json(
            args.snapshot.read_text(encoding="utf-8")
        )
        if args.output:
            print("metadata_fingerprint=" + export_metadata(args.root, research, args.output))
        else:
            print(canonical(diagnose(build_metadata(args.root, research), research)))
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
        raise SystemExit(f"security metadata blocked: {error}") from error


if __name__ == "__main__":
    main()
