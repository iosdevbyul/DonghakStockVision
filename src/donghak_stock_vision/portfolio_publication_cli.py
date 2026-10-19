"""Offline operator commands. Never invoked by the read-only HTTP service."""

import argparse
import json
from pathlib import Path
from typing import Any

from donghak_stock_vision.api.historical_portfolio_projection import RESULT
from donghak_stock_vision.api.historical_portfolios import (
    LocalFiles,
    PortfolioPublicationReader,
    PublicationError,
    check,
    publish,
    revoke,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.receipt_archive import DEFAULT_LIMITS, encode_result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dsv-portfolio-publication")
    result.add_argument(
        "--root", required=True, type=Path, help="existing canonical private directory"
    )
    result.add_argument(
        "--index-sha256", help="current trusted index SHA; omit only for first publish"
    )
    actions = result.add_subparsers(dest="action", required=True)
    compact = actions.add_parser(
        "encode", help="compact an existing result; not publication approval"
    )
    compact.add_argument("--result", required=True, help="flat result filename inside root")
    compact.add_argument("--archive", required=True, help="flat output filename; never overwrite")
    add = actions.add_parser(
        "publish", help="authoritative replay and explicit offline registration"
    )
    add.add_argument("--archive", required=True, help="flat filename inside root, never a path")
    add.add_argument("--result-hash", required=True)
    add.add_argument("--report-hash", required=True)
    remove = actions.add_parser("revoke")
    remove.add_argument("--report-id", required=True)
    diagnostic = actions.add_parser(
        "diagnose", help="bounded integrity/availability check; no replay"
    )
    diagnostic.add_argument(
        "--report-id", help="inspect this registered ID, otherwise first active ID"
    )
    return result


def encode(root: Path, result_name: str, archive_name: str) -> dict[str, Any]:
    """Existing encoder with safe flat-file I/O; publish remains the replay gate."""
    check(result_name != archive_name, "invalid_storage_locator")
    files = LocalFiles(root)
    raw = files.read(result_name, DEFAULT_LIMITS.expanded_bytes)
    try:
        source = FrozenJSON(raw.decode("utf-8"))
        value = source.to_dict()
        check(value.get("version") == RESULT, "unsupported_archive_version")
        archive = encode_result(source)
    except PublicationError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as error:
        raise PublicationError("result_validation_required") from error
    files.write(archive_name, archive.document.payload_json.encode("utf-8"))
    return {
        "resultHash": source.identifier,
        "reportHash": value["detailed_report_hash"],
        "archiveTransportHash": archive.transport_hash,
        "archiveValidation": "transport_verified_only",
        "publicationAuthorized": False,
        "resultBytes": len(raw),
        "archiveBytes": len(archive.document.payload_json.encode("utf-8")),
    }


def diagnose(root: Path, index_hash: str | None, report_id: str | None = None) -> dict[str, Any]:
    """Read the operator-pinned publication; never discover or approve a new pin."""
    check(index_hash is not None, "missing_trusted_index_sha")
    assert index_hash is not None
    reader = PortfolioPublicationReader(root, index_hash)
    if report_id is None:
        page = reader.collection(limit=1)
        if not page["items"]:
            return {"status": "no_published_archive", "researchOnly": True, "total": page["total"]}
        report_id = page["items"][0]["reportId"]
    summary = reader.summary(report_id)
    codes = ["research_only_not_pit_oos_or_trading_authorization"]
    if summary["acceptedOrderCount"] == 0:
        codes.append("no_accepted_orders")
    if summary["fillCount"] == 0:
        codes.append("no_fills")
    if not summary["portfolioValuationAvailable"]:
        codes.append("valuation_evidence_unavailable")
    codes.extend(
        p["reason"] for p in summary["finalValuation"]["positions"] if p.get("reason") is not None
    )
    return {
        "status": "published_integrity_verified",
        "reportId": summary["reportId"],
        "resultHash": summary["resultHash"],
        "reportHash": summary["reportHash"],
        "acceptedOrderCount": summary["acceptedOrderCount"],
        "fillCount": summary["fillCount"],
        "portfolioValuationAvailable": summary["portfolioValuationAvailable"],
        "researchOnly": summary["researchOnly"],
        "diagnostics": sorted(set(codes)),
    }


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.action == "encode":
            print(json.dumps(encode(args.root, args.result, args.archive), sort_keys=True))
        elif args.action == "diagnose":
            print(
                json.dumps(diagnose(args.root, args.index_sha256, args.report_id), sort_keys=True)
            )
        elif args.action == "publish":
            report_id, index_hash = publish(
                args.root,
                args.archive,
                expected_index_hash=args.index_sha256,
                expected_result_hash=args.result_hash,
                expected_report_hash=args.report_hash,
            )
            print(f"reportId={report_id}")
            print(f"indexSha256={index_hash}")
        else:
            if args.index_sha256 is None:
                parser().error("revocation requires --index-sha256")
            index_hash = revoke(args.root, args.report_id, expected_index_hash=args.index_sha256)
            print(f"indexSha256={index_hash}")
    except PublicationError as error:
        raise SystemExit(error.code) from None
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        # Bad document shapes must not leak an upstream traceback to the operator.
        code = (
            "report_projection_incomplete"
            if args.action == "diagnose"
            else "result_validation_required"
        )
        raise SystemExit(code) from None


if __name__ == "__main__":
    main()
