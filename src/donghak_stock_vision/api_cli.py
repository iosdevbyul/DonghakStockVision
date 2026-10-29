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
    result.add_argument(
        "--market-db", type=Path, help="optional immutable market database for OHLCV"
    )
    result.add_argument("--market-db-sha256", help="approved market database SHA-256")
    result.add_argument(
        "--operational-snapshot", type=Path, help="offline operational .snapshot.sqlite3 copy"
    )
    result.add_argument(
        "--operational-snapshot-sha256", help="approved offline copy SHA-256"
    )
    result.add_argument(
        "--security-metadata", type=Path, help="optional display metadata envelope"
    )
    result.add_argument(
        "--security-metadata-fingerprint", help="expected metadata artifact fingerprint"
    )
    result.add_argument("--metadata-evidence-root", type=Path, default=Path("."))
    result.add_argument(
        "--analysis-publication", type=Path, help="reviewed per-security analysis JSON"
    )
    result.add_argument(
        "--operations-root", type=Path, help="optional read-only DART evidence root"
    )
    result.add_argument(
        "--dart-acquisition-freshness-seconds",
        type=int,
        help="optional acquisition-age policy, not publication freshness",
    )
    result.add_argument("--portfolio-publications", type=Path, help="private publication root")
    result.add_argument("--portfolio-index-sha256", help="operator-approved publication index SHA")
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

    if bool(args.market_db) != bool(args.market_db_sha256):
        parser().error("--market-db and --market-db-sha256 must be specified together")
    if bool(args.operational_snapshot) != bool(args.operational_snapshot_sha256):
        parser().error(
            "--operational-snapshot and --operational-snapshot-sha256 must be supplied together"
        )
    if bool(args.security_metadata) != bool(args.security_metadata_fingerprint):
        parser().error(
            "--security-metadata and --security-metadata-fingerprint must be specified together"
        )
    portfolio_reader = None
    if bool(args.portfolio_publications) != bool(args.portfolio_index_sha256):
        parser().error(
            "--portfolio-publications and --portfolio-index-sha256 are required together"
        )
    if args.portfolio_publications:
        if args.host not in {"127.0.0.1", "::1"}:
            parser().error("portfolio publications require a loopback bind")
        from donghak_stock_vision.api.historical_portfolios import (
            PortfolioPublicationReader,
            PublicationError,
        )

        try:
            portfolio_reader = PortfolioPublicationReader(
                args.portfolio_publications, args.portfolio_index_sha256
            )
        except PublicationError as error:
            raise SystemExit(
                f"portfolio publication startup blocked: {error.code}; "
                "review the offline publication output and run dsv-portfolio-publication diagnose"
            ) from None
    ohlcv_reader = None
    if args.market_db:
        from donghak_stock_vision.api.ohlcv import OhlcvMarketReader

        ohlcv_reader = OhlcvMarketReader(args.market_db, expected_sha256=args.market_db_sha256)
    operational_reader = None
    if args.operational_snapshot:
        if args.host not in {"127.0.0.1", "::1"}:
            parser().error("operational snapshots require loopback bind")
        from donghak_stock_vision.api.operational_market import PinnedOperationalReader
operational_reader = PinnedOperationalReader(
            args.operational_snapshot, expected_sha256=args.operational_snapshot_sha256
        )
    analysis_reader = None
    if args.analysis_publication:
        from donghak_stock_vision.api.stock_analysis import AnalysisPublicationReader

        analysis_reader = AnalysisPublicationReader.from_path(args.analysis_publication)
    from donghak_stock_vision.api.snapshot import SnapshotValidationError

    try:
        reader = SnapshotReader.from_path(snapshot_path)
    except SnapshotValidationError:
        raise SystemExit(
            "research snapshot unavailable or invalid; supply a validated --snapshot"
        ) from None
    metadata_reader = None
    if args.security_metadata:
        from donghak_stock_vision.api.security_metadata import SecurityMetadataReader
        from donghak_stock_vision.api.snapshot import ResearchAPISnapshot

        metadata_reader = SecurityMetadataReader.from_path(
            args.security_metadata,
            expected_fingerprint=args.security_metadata_fingerprint,
            root=args.metadata_evidence_root,
            research=ResearchAPISnapshot.model_validate_json(
                snapshot_path.read_text(encoding="utf-8")
            ),
        )
    from donghak_stock_vision.api.operations import OperationsReader

    app = create_app(
        reader,
        portfolio_reader=portfolio_reader,
        ohlcv_reader=ohlcv_reader,
        operational_reader=operational_reader,
        analysis_reader=analysis_reader,
        security_metadata_reader=metadata_reader,
        operations_reader=OperationsReader(
            args.operations_root,
            reader,
            acquisition_threshold_seconds=args.dart_acquisition_freshness_seconds,
            market=ohlcv_reader,
        ),
    )
    if portfolio_reader is not None:
        uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
