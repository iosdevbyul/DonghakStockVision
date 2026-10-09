"""Offline lifecycle evidence replay from preserved official KIND responses.

This module imports no inference, strategy, execution or market-store service.
It reads an existing gap artifact, never recalculates gaps from market bars.
"""

import argparse
import gzip
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.security_lifecycle import (
    LifecycleEvidence,
    LifecycleReason,
    reconcile_lifecycle,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.backtest.verify_dart import read, semantic_metadata, source_text, wrapped
from donghak_stock_vision.data.learning import digest

VERSION = "baseline_security_lifecycle_v1"


def source(base: Path, name: str) -> tuple[str, dict[str, Any]]:
    """Validate preserved bytes before using a source as positive evidence."""
    metadata = read(base / "sources" / (name + ".json"))
    raw = gzip.decompress((base / "sources" / (name + ".html.gz")).read_bytes())
    require(hashlib.sha256(raw).hexdigest() == metadata["raw_sha256"], "source_hash_mismatch")
    require(metadata["status"] == 200, "source_http_failure")
    require(metadata["endpoint"].startswith("https://kind.krx.co.kr/"), "official_source_required")
    require(metadata["credential_used"] is False, "unexpected_credential")
    return raw.decode("utf-8"), dict(
        url=metadata["endpoint"],
        raw_sha256=metadata["raw_sha256"],
        document_reference=name,
        request_parameters=metadata["parameters"],
        method=metadata["method"],
    )


def official_identity(text: str) -> tuple[str, str]:
    """Read literal six-digit code and ISIN; never transform KIND's issuer key."""
    codes = re.findall(r"\uc885\ubaa9\ucf54\ub4dc\s+([0-9]{6})\b", text)
    isins = re.findall(r"\ud45c\uc900\ucf54\ub4dc\s+(KR[A-Z0-9]{10})\b", text)
    require(len(codes) == len(isins) == 1, "ambiguous_or_missing_security_identity")
    return codes[0], isins[0]


def table_events(base: Path) -> list[dict[str, Any]]:
    """Normalize effective listing/delisting dates from reviewed official tables."""
    output = []
    for name, kind in (("delisting-history", "delisting"), ("listing-history-complete", "listing")):
        html, ref = source(base, name)
        rows = re.findall(r"(<tr\b[^>]*>.*?</tr>)", html, flags=re.S)
        found = 0
        for row in rows:
            link = re.search(
                r"companysummary_open\('([^']+)'"
                if kind == "delisting"
                else r"fnDetailView\('([^']+)','([^']+)'",
                row,
            )
            if link is None:
                continue
            found += 1
            issuer = link.group(1)
            text = source_text(row.encode())
            dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
            require(len(dates) == 1, "ambiguous_effective_date")
            identity_html, identity_ref = source(base, "overview-" + issuer)
            identity_text = source_text(identity_html.encode())
            ticker, isin = official_identity(identity_text)
            effective = dates[0]
            if kind == "listing":
                require(
                    "\uc0c1\uc7a5\uc77c " + effective in identity_text,
                    "listing_date_cross_source_mismatch",
                )
            output.append(
                dict(
                    ticker=ticker,
                    security_identity=isin,
                    kind=kind,
                    effective_date=effective,
                    filing_date=None,
                    reason=(
                        LifecycleReason.DELISTED.value
                        if kind == "delisting"
                        else LifecycleReason.RELISTING_TRANSITION.value
                        if "\uc7ac\uc0c1\uc7a5" in text
                        else LifecycleReason.NOT_YET_LISTED.value
                    ),
                    market_scope="KOSPI",
                    source=ref,
                    identity_source=identity_ref,
                    source_excerpt=text,
                    identity_excerpt=identity_text.split("\uc790\ubcf8\uae08")[0],
                    historical_continuity_verified=False,
                    review_reference="kind_effective_boundary_and_exact_overview_v1",
                )
            )
        total = re.search(r"\uc804\uccb4 ([\d,]+) \uac74", source_text(html.encode()))
        require(total is not None and int(total[1].replace(",", "")) == found, "incomplete_table")
    return sorted(output, key=lambda r: (r["ticker"], r["effective_date"], r["kind"]))


def original_delistings(base: Path) -> list[dict[str, Any]]:
    """Exact share-class notices, not issuer-level guesses or decision dates."""
    events = []
    for path in sorted((base / "sources").glob("original-*.json")):
        html, ref = source(base, path.stem)
        text = source_text(html.encode())
        codes = re.findall(r"\bA([0-9]{6})\b", text)
        dates = re.findall(r"3\.\s*\uc0c1\uc7a5\ud3d0\uc9c0\uc77c\s+(\d{4}-\d{2}-\d{2})", text)
        require(len(codes) == len(dates) == 1, "ambiguous_share_class_notice")
        require(text.startswith("\uc0c1\uc7a5\ud3d0\uc9c0 "), "effective_notice_required")
        ticker = codes[0]
        events.append(
            dict(
                ticker=ticker,
                security_identity="KRX_SHORT_CODE:A" + ticker,
                kind="delisting",
                effective_date=dates[0],
                filing_date=date.fromisoformat(path.stem.split("-")[1][:8]).isoformat(),
                reason="DELISTED",
                market_scope="KOSPI",
                source=ref,
                identity_source=ref,
                source_excerpt=text,
                identity_excerpt="A" + ticker,
                historical_continuity_verified=False,
                review_reference="kind_share_class_effective_delisting_notice_v1",
            )
        )
    return events


def verify(root: Path, base: Path) -> dict[str, dict[str, Any]]:
    parent_dir = root / "docs/evidence/baseline-dart/artifacts"
    parent = read(parent_dir / "missing-session-explanations.json")
    require(digest(parent["artifact"]) == parent["fingerprint"], "parent_hash_mismatch")
    integrated = read(parent_dir / "integrated-official-evidence.json")
    require(digest(integrated["artifact"]) == integrated["fingerprint"], "parent_package_mismatch")
    gaps = {
        r["ticker"]: r["unexplained"] for r in parent["artifact"]["securities"] if r["unexplained"]
    }
    require(
        len(gaps) == 43 and sum(map(len, gaps.values())) == 8118, "unexpected_starting_inventory"
    )
    mapping = read(parent_dir / "dart-corporation-mapping.json")
    require(digest(mapping["artifact"]) == mapping["fingerprint"], "mapping_hash_mismatch")
    universe = {r["ticker"] for r in mapping["artifact"]["securities"]}
    require(len(universe) == 993 and set(gaps) <= universe, "universe_mismatch")
    events = sorted(
        table_events(base) + original_delistings(base),
        key=lambda e: (e["ticker"], e["effective_date"], e["kind"]),
    )
    intervals = []
    for event in events:
        ticker = event["ticker"]
        if ticker not in gaps:
            continue
        boundary = date.fromisoformat(event["effective_date"])
        start, end = date.fromisoformat(gaps[ticker][0]), date.fromisoformat(gaps[ticker][-1])
        if event["reason"] == LifecycleReason.DELISTED:
            start = max(start, boundary)
        else:
            end = min(end, boundary - timedelta(days=1))
        if start > end:
            continue
        intervals.append(
            LifecycleEvidence(
                ticker,
                event["security_identity"],
                LifecycleReason(event["reason"]),
                start,
                end,
                boundary,
                FrozenJSON.freeze(event["source"]),
                FrozenJSON.freeze(event["identity_source"]),
                True,
                True,
                event["review_reference"],
            )
        )
    reconciliation = reconcile_lifecycle(FrozenJSON.freeze(gaps), tuple(intervals)).to_dict()
    sources = []
    for path in sorted((base / "sources").glob("*.json")):
        metadata = read(path)
        raw = gzip.decompress(path.with_suffix(".html.gz").read_bytes())
        require(hashlib.sha256(raw).hexdigest() == metadata["raw_sha256"], "source_hash_mismatch")
        sources.append(semantic_metadata(metadata))
    unresolved = [
        dict(
            ticker=t,
            dates=[
                r["date"]
                for r in reconciliation["sessions"]
                if r["ticker"] == t and not r["verified"]
            ],
            reason="UNEXPLAINED_DATA_GAP",
        )
        for t in reconciliation["unresolved_securities"]
    ]
    result = {
        "security-lifecycle-sources": dict(version=VERSION, sources=sorted(sources, key=digest)),
        "historical-security-identities": dict(
            version=VERSION,
            boundary_identities=[
                dict(
                    ticker=e["ticker"],
                    security_identity=e["security_identity"],
                    effective_boundary=e["effective_date"],
                    source=e["identity_source"],
                    status="BOUNDARY_IDENTITY_VERIFIED",
                    whole_period_continuity_verified=False,
                )
                for e in events
                if e["ticker"] in universe
            ],
            current_corporation_mapping_fingerprint=mapping["fingerprint"],
            current_corporations_verified=sum(
                r["current_status"] == "CURRENT_IDENTITY_VERIFIED"
                for r in mapping["artifact"]["securities"]
            ),
            whole_period_historical_identities_verified=0,
        ),
        "listing-history": dict(
            version=VERSION, events=[e for e in events if e["kind"] == "listing"]
        ),
        "delisting-history": dict(
            version=VERSION, events=[e for e in events if e["kind"] == "delisting"]
        ),
        "trading-suspensions": dict(
            version=VERSION,
            verified_intervals=[],
            limitation="Current suspension list cannot establish historical bounded intervals.",
        ),
        "missing-session-reconciliation": reconciliation,
        "unresolved-security-gaps": dict(version=VERSION, securities=unresolved),
    }
    result["security-lifecycle-manifest"] = dict(
        version=VERSION,
        parent_gap_fingerprint=parent["fingerprint"],
        parent_integrated_fingerprint=integrated["fingerprint"],
        parent_mapping_fingerprint=mapping["fingerprint"],
        starting_unresolved_securities=len(gaps),
        starting_unexplained_sessions=8118,
        newly_explained=reconciliation["explained"],
        unexplained=reconciliation["unexplained"],
        artifact_fingerprints={k: digest(v) for k, v in sorted(result.items())},
        corporate_action_coverage_upgraded=False,
        protected_inputs_written=False,
    )
    result["integrated-security-evidence"] = dict(
        version=VERSION,
        parent_integrated_fingerprint=integrated["fingerprint"],
        manifest_fingerprint=digest(result["security-lifecycle-manifest"]),
        exchange_calendar="VERIFIED",
        settlement_mapping="VERIFIED",
        security_calendar="VERIFIED"
        if reconciliation["status"] == "VERIFIED"
        else "BLOCKED_ON_CALENDAR_EVIDENCE",
        corporate_action="BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
        ready_for_baseline_run_input_freeze=False,
        policy_override=False,
        prediction_or_backtest_invoked=False,
    )
    return {k: wrapped(v) for k, v in result.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.root / "docs/evidence/baseline-security-lifecycle"
    artifacts = verify(args.root, base)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in artifacts.items():
        (args.output / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        )
    print(
        json.dumps(
            {name: value["fingerprint"] for name, value in artifacts.items()}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
