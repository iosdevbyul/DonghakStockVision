"""CLI entry point for the read-only research API."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dsv-api")
    result.add_argument(
        "--snapshot",
        type=Path,
        help="validated research API snapshot JSON; defaults to DSV_API_SNAPSHOT_PATH",
    )
    result.add_argument("--analysis-publication", type=Path, help="reviewed per-security analysis JSON")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8000)
    return result


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parser().parse_args(argv)
    snapshot_path = args.snapshot
    if snapshot_path is None:
        configured = os.getenv("DSV_API_SNAPSHOT_PATH", "").strip()
        snapshot_path = Path(configured) if configured else None
    if snapshot_path is None:
        parser().error("--snapshot or DSV_API_SNAPSHOT_PATH is required")

    try:
        import uvicorn

        from donghak_stock_vision.api.app import create_app
        from donghak_stock_vision.api.snapshot import SnapshotReader
    except ImportError as error:
        raise SystemExit(
            "API dependencies are not installed; install donghak-stock-vision[api]"
        ) from error

    analysis_reader = None
    if args.analysis_publication:
        from donghak_stock_vision.api.stock_analysis import AnalysisPublicationReader
        analysis_reader = AnalysisPublicationReader.from_path(args.analysis_publication)
    reader = SnapshotReader.from_path(snapshot_path)
    uvicorn.run(create_app(reader), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
