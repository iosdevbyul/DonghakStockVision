"""Offline operator command. Never invoked by the read-only HTTP service."""

import argparse
from pathlib import Path

from donghak_stock_vision.api.historical_portfolios import PublicationError, publish, revoke


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dsv-portfolio-publication")
    result.add_argument(
        "--root", required=True, type=Path, help="existing canonical private directory"
    )
    result.add_argument(
        "--index-sha256", help="current trusted index SHA; omit only for first publish"
    )
    actions = result.add_subparsers(dest="action", required=True)
    add = actions.add_parser("publish")
    add.add_argument("--archive", required=True, help="flat filename inside root, never a path")
    add.add_argument("--result-hash", required=True)
    add.add_argument("--report-hash", required=True)
    remove = actions.add_parser("revoke")
    remove.add_argument("--report-id", required=True)
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.action == "publish":
            report_id, index_hash = publish(
                args.root,
                args.archive,
                expected_index_hash=args.index_sha256,
                expected_result_hash=args.result_hash,
                expected_report_hash=args.report_hash,
            )
            print(f"reportId={report_id}")
        else:
            if args.index_sha256 is None:
                parser().error("revocation requires --index-sha256")
            index_hash = revoke(args.root, args.report_id, expected_index_hash=args.index_sha256)
        print(f"indexSha256={index_hash}")
    except PublicationError as error:
        raise SystemExit(error.code) from None


if __name__ == "__main__":
    main()
