"""Offline replay of official DART/KIND evidence; no model or market execution."""

import argparse
import gzip
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.dart_evidence import (
    REQUIRED_END,
    REQUIRED_START,
    corporation_mapping,
    document_candidate,
    filing_pages,
    reconcile_missing,
)
from donghak_stock_vision.backtest.dart_transport import request_identity
from donghak_stock_vision.backtest.run_evidence import file_sha
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


def read(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))


def wrapped(data: dict[str, Any]) -> dict[str, Any]:
    return dict(artifact=data, fingerprint=digest(data))


def source_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def semantic_metadata(value: Any) -> Any:
    """Transport paths, retrieval clocks and retries do not identify evidence."""
    excluded = {
        "file",
        "source_file",
        "path",
        "local_path",
        "absolute_path",
        "retrieved_at",
        "retrieval_timestamp",
        "timestamp",
        "attempts",
    }
    if isinstance(value, dict):
        require(
            not ({"crtfc_key", "DART_API_KEY", "AUTH_KEY"} & set(value)), "secret_field_in_evidence"
        )
        return {k: semantic_metadata(v) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [semantic_metadata(v) for v in value]
    return value


def source_inventory(base: Path) -> dict[str, Any]:
    """Content-address all raw/support evidence without filesystem-location identity."""
    identities = set()
    for directory in (base / "raw", base / "support"):
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if path.suffix == ".gz":
                identities.add(hashlib.sha256(gzip.decompress(raw)).hexdigest())
            elif path.suffix == ".json":
                identities.add(digest(semantic_metadata(json.loads(raw))))
            else:
                identities.add(hashlib.sha256(raw).hexdigest())
    return dict(
        version="official_source_content_inventory_v1", content_identities=sorted(identities)
    )


def reviewed_intervals(base: Path) -> list[dict[str, Any]]:
    """Review assertions are bound to preserved source bytes and literal excerpts."""
    path = base / "support/lifecycle-review.json"
    if not path.exists():
        return []
    review = read(path)
    intervals = review["intervals"]
    for item in intervals:
        raw = gzip.decompress((base / "support" / item["source_file"]).read_bytes())
        require(
            hashlib.sha256(raw).hexdigest() == item["source"]["raw_sha256"], "review_source_hash"
        )
        text = source_text(raw)
        require(item["source_excerpt"] in text, "review_excerpt_mismatch")
        require(item["identity_excerpt"] in text, "review_security_excerpt_mismatch")
    return [{k: v for k, v in item.items() if k != "source_file"} for item in intervals]


def verify(root: Path, base: Path) -> dict[str, dict[str, Any]]:
    parent_dir = root / "docs/evidence/baseline-official/artifacts"
    parent = read(parent_dir / "integrated-evidence-package.json")
    require(
        digest(parent["package"]) == parent["package_fingerprint"], "parent_fingerprint_mismatch"
    )
    locked = read(root / "docs/baseline-run-evidence-package.json")
    protected = locked["package"]["evidence"]["identities"]["protected_sha256_before"]
    require(
        all(file_sha(root / p) == sha for p, sha in protected.items()), "protected_input_mismatch"
    )
    universe = tuple(
        read(root / ".data/oos-preparation-20261001/part2-freeze/universe.json")["tickers"]
    )
    raw_corp = (base / "raw/corpCode.zip").read_bytes()
    mapping = corporation_mapping(raw_corp, universe).to_dict()
    initial = json.loads((base / "support/initial-retrieval.json").read_text())
    source = [m for m in initial if m["file"] == "corpCode.zip"]
    require(
        len(source) == 1 and source[0]["sha256"] == mapping["source_sha256"],
        "corporation_source_mismatch",
    )
    require(
        source[0]["endpoint"] == "https://opendart.fss.or.kr/api/corpCode.xml"
        and source[0]["status"] == 200,
        "official_corporation_source_required",
    )
    securities = mapping["securities"]
    current = {
        r["ticker"]: r["matches"][0]
        for r in securities
        if r["current_status"] == "CURRENT_IDENTITY_VERIFIED"
    }
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    receipts = []
    for path in sorted((base / "raw/requests").glob("*.json")):
        meta = read(path)
        identity = request_identity("list.json", meta["params"])
        require(
            digest(identity) == path.stem and meta["endpoint"] == identity["endpoint"],
            "request_identity_mismatch",
        )
        raw = gzip.decompress(path.with_suffix(".gz").read_bytes())
        require(hashlib.sha256(raw).hexdigest() == meta["raw_sha256"], "raw_response_hash")
        require(meta["http_status"] == 200, "http_failure")
        params = {k: v for k, v in meta["params"].items() if k not in {"page_no", "page_count"}}
        groups[json.dumps(params, sort_keys=True)].append(
            (int(meta["params"]["page_no"]), json.loads(raw))
        )
        receipts.append(
            {**identity, "raw_sha256": meta["raw_sha256"], "http_status": meta["http_status"]}
        )
    queries = []
    filings: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    corp_queries: dict[str, str] = {}
    for key, group in sorted(groups.items()):
        params = json.loads(key)
        result = filing_pages(tuple(p for _, p in sorted(group)), params).to_dict()
        if params.get("corp_code"):
            corp_queries[params["corp_code"]] = result["status"]
        for row in result["rows"]:
            ref = row["rcept_no"]
            if ref in filings and filings[ref] != row:
                conflicts.add(ref)
            filings[ref] = row
        queries.append(
            dict(
                params=params,
                status=result["status"],
                reasons=result["reasons"],
                row_count=len(result["rows"]),
                rows_fingerprint=digest(result["rows"]),
                pages=len(group),
                empty_means=result["empty_means"],
            )
        )
    candidates = []
    for ref, row in sorted(filings.items()):
        candidate = document_candidate(row)
        if candidate:
            candidate["repository_ticker"] = (
                row.get("stock_code") if row.get("stock_code") in universe else None
            )
            candidate["identity_conflict"] = ref in conflicts
            candidates.append(candidate)
    intervals = reviewed_intervals(base)
    calendar = read(parent_dir / "calendar-verification.json")
    missing = calendar["artifact"]["per_security"]["details"]["missing_sessions"]
    reconciled = [
        reconcile_missing(t, tuple(ds), tuple(intervals)).to_dict()
        for t, ds in sorted(missing.items())
    ]
    for item in reconciled:
        item["review_queue_receipts"] = sorted(
            c["filing"]["rcept_no"]
            for c in candidates
            if c["repository_ticker"] == item["ticker"] and c["lifecycle_review"]
        )
        item["listing_boundary_evidence"] = [
            i["review_reference"] for i in intervals if i["ticker"] == item["ticker"]
        ]
        item["bar_absence_used_as_lifecycle_evidence"] = False
    rec = {r["ticker"]: r for r in reconciled}
    statuses = []
    for ticker in sorted(universe):
        corp = current.get(ticker, {}).get("corp_code")
        lifecycle = rec.get(ticker)
        statuses.append(
            dict(
                ticker=ticker,
                corp_code=corp,
                current_identity="CURRENT_IDENTITY_VERIFIED"
                if corp
                else "BLOCKED_ON_IDENTITY_MAPPING",
                identity="BLOCKED_ON_HISTORICAL_IDENTITY",
                calendar_lifecycle=lifecycle["status"]
                if lifecycle
                else "VERIFIED_OBSERVED_SESSIONS",
                corporate_action_coverage="BLOCKED_ON_IDENTITY_MAPPING",
                filing_query_status=corp_queries.get(corp, "NO_RESOLVED_CORPORATION_QUERY"),
                complete_event_class_coverage=False,
            )
        )
    # KIND document locators can be compared to actual DART receipt IDs. This is
    # a source-reference cross-check, not a historical issuer/security mapping.
    cross = []
    content_dir = base / "support/kind-content"
    source_path = content_dir / "sources.json"
    if source_path.exists():
        for entry in json.loads(source_path.read_text()):
            refs: list[str] = []
            if entry.get("http_status") == 200:
                raw = gzip.decompress((content_dir / entry["file"]).read_bytes())
                require(hashlib.sha256(raw).hexdigest() == entry["raw_sha256"], "kind_source_hash")
                match = re.search(
                    r"parent.setPath\('[^']*','([^']+)'", raw.decode("utf-8", errors="replace")
                )
                if match:
                    refs = re.findall(r"/([0-9]{14})/", match[1])
            matched = [ref for ref in refs if ref in filings]
            cross.append(
                dict(
                    kind_reference=entry["kind_acptno"],
                    kind_document=entry["params"]["docNo"],
                    dart_receipts=matched,
                    raw_sha256=entry.get("raw_sha256"),
                    status="MATCHED_DOCUMENT_LOCATOR_NOT_SECURITY_MAPPING"
                    if matched
                    else "UNRESOLVED_REFERENCE",
                    ticker=[filings[ref].get("stock_code") for ref in matched],
                    historical_identity_verified=False,
                )
            )
    explained = sum(len(r["explained"]) for r in reconciled)
    unresolved = sum(len(r["unexplained"]) + len(r["conflicting"]) for r in reconciled)
    artifacts = {
        "source-inventory": source_inventory(base),
        "dart-corporation-mapping": mapping,
        "filing-query-coverage": dict(
            version="dart_filing_queries_v1",
            queries=queries,
            unique_filing_count=len(filings),
            conflicting_receipts=sorted(conflicts),
            sources=sorted(receipts, key=digest),
        ),
        "historical-security-mapping": dict(
            version="historical_security_review_v1",
            full_period_verified=0,
            boundary_evidence=intervals,
            securities=statuses,
        ),
        "corporate-action-events": dict(
            version="dart_document_review_queue_v1",
            candidates=candidates,
            verified_complete_security_count=0,
            verified_economic_event_count=None,
            effective_date_review_required=True,
            prices_adjusted=False,
        ),
        "security-lifecycle": dict(
            version="lifecycle_review_v1", intervals=intervals, securities=statuses
        ),
        "missing-session-explanations": dict(
            version="missing_session_review_v1",
            parent_calendar_fingerprint=calendar["fingerprint"],
            total_missing=explained + unresolved,
            explained=explained,
            unresolved=unresolved,
            securities=reconciled,
        ),
        "kind-dart-cross-reference": dict(
            version="cross_reference_v1", links=sorted(cross, key=digest)
        ),
    }
    out = {name: wrapped(data) for name, data in artifacts.items()}
    blockers = ["BLOCKED_ON_CORPORATE_ACTION_EVIDENCE"]
    if unresolved:
        blockers.append("BLOCKED_ON_CALENDAR_EVIDENCE")
    manifest = dict(
        version="baseline_dart_evidence_v1",
        required_start=REQUIRED_START,
        required_end=REQUIRED_END,
        parent_official_package_fingerprint=parent["package_fingerprint"],
        universe_fingerprint=digest(sorted(universe)),
        artifacts={k: v["fingerprint"] for k, v in out.items()},
        current_identity_count=len(current),
        historical_identity_count=0,
        complete_corporate_coverage_count=0,
        explained_missing_sessions=explained,
        unresolved_missing_sessions=unresolved,
        credential_verified_by_successful_api=True,
        blockers=sorted(blockers),
        ready=False,
        settlement_evidence_fingerprint=read(parent_dir / "settlement-calendar.json")[
            "fingerprint"
        ],
    )
    out["dart-evidence-manifest"] = wrapped(manifest)
    out["integrated-official-evidence"] = wrapped(
        dict(
            version="baseline_official_dart_integration_v1",
            parent_package=parent["package_fingerprint"],
            dart_manifest=out["dart-evidence-manifest"]["fingerprint"],
            locked_identities=locked["package"]["evidence"]["identities"],
            blockers=sorted(blockers),
            ready=False,
            policy_weakening=False,
            predictions_or_simulations_executed=False,
        )
    )
    require(
        all(file_sha(root / p) == sha for p, sha in protected.items()), "protected_input_changed"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "output_must_be_new_directory")
    artifacts = verify(args.root, args.evidence)
    args.output.mkdir(parents=True)
    for name, value in artifacts.items():
        (args.output / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        )
    print(json.dumps({k: v["fingerprint"] for k, v in artifacts.items()}, sort_keys=True))


if __name__ == "__main__":
    main()
