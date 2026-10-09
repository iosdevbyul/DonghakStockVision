"""CLI for deterministic read-only Tris Market Lens snapshot export."""

import argparse
from pathlib import Path

from donghak_stock_vision.api.exporter import SnapshotExportError, export_snapshot


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dsv-api-export")
    result.add_argument("--root", type=Path, default=Path("."))
    result.add_argument("--market-db", type=Path, required=True)
    result.add_argument(
        "--publication",
        type=Path,
        default=Path("docs/research-api/model-publication.json"),
    )
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    publication = args.publication
    if not publication.is_absolute():
        publication = root / publication
    try:
        fingerprint = export_snapshot(
            market_db=args.market_db,
            root=root,
            publication_path=publication,
            output=args.output,
        )
    except SnapshotExportError as error:
        raise SystemExit(f"snapshot export blocked: {error}") from error
    print(f"snapshot_sha256={fingerprint}")


if __name__ == "__main__":
    main()
