"""Offline official-evidence normalization and read-only market comparison.

Input HTTP responses must have been collected separately through public interfaces.
No credential, model loading, prediction or runner entry point is used here.
"""

import argparse
import gzip
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import (
    ANCHOR_END,
    ANCHOR_START,
    AnchorRange,
    BaselineRunEvidencePackage,
    derive_boundaries,
)
from donghak_stock_vision.backtest.official_evidence import (
    extract_action_rows,
    merge_months,
    normalize_month,
    semantic_source,
)
from donghak_stock_vision.backtest.run_evidence import (
    BaselineRunEvidence,
    EvidenceCheck,
    audit_database,
    calendar_check,
    check,
    file_sha,
    settlement_check,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


def read(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))


def wrapped(value: dict[str, Any]) -> dict[str, Any]:
    return dict(artifact=value, fingerprint=digest(value))


def verify(root: Path, raw_dir: Path) -> dict[str, dict[str, Any]]:
    parent = read(root / "docs/baseline-run-evidence-package.json")
    require(digest(parent["package"]) == parent["package_fingerprint"], "parent_package_mismatch")
    records = read(raw_dir / "retrieval.json")["requests"]
    market = root / ".data/market.sqlite3"
    protected = parent["package"]["evidence"]["identities"]["protected_sha256_before"]
    before = {p: file_sha(root / p) for p in protected}
    require(before == protected, "protected_inputs_mismatch")
    try:
        sources = []
        months = []
        actions = []
        for source in records:
            raw = gzip.decompress((raw_dir / (source["file"] + ".gz")).read_bytes())
            require(hashlib.sha256(raw).hexdigest() == source["raw_sha256"], "source_hash_mismatch")
            sources.append(semantic_source(source))
            if source["file"].startswith("calendar-"):
                months.append(normalize_month(raw, source))
            elif source["file"].startswith("corporate-"):
                actions.append(extract_action_rows(raw, source))
        sources.sort(key=digest)
        actions.sort(key=lambda a: a.identifier)
        rule = parent["package"]["official_sources"]["documents"][0]
        calendar = merge_months(tuple(months), FrozenJSON.freeze(rule))
        exclusions = (date(2024, 3, 28), date(2025, 3, 21))
        boundaries = derive_boundaries(
            AnchorRange(ANCHOR_START, ANCHOR_END), exclusions, calendar, calendar.identifier
        )
        b = boundaries.to_dict()
        require(b["status"] == "VERIFIED", "incomplete_official_boundary_evidence")
        start = date.fromisoformat(b["warmup_sessions"][0])
        end = date.fromisoformat(b["execution_tail"][-1])
        universe_doc = read(root / ".data/oos-preparation-20261001/part2-freeze/universe.json")
        universe = tuple(universe_doc["tickers"])
        ordering, db = audit_database(market, universe, start, end, exclusions)
        d = ordering.details.to_dict()
        sessions = d.pop("sessions")
        comparison = calendar_check(sessions, start, end, exclusions, calendar, calendar.identifier)
        observed = sorted({x for ds in sessions.values() for x in ds})
        aggregate = calendar_check(
            {"entire_market": observed}, start, end, exclusions, calendar, calendar.identifier
        )
        trades = tuple(date.fromisoformat(t) for t in b["settlement_mapping"])
        settlement = settlement_check(trades, calendar, calendar.identifier)
        rows = [row for a in actions for row in a.to_dict()["rows"]]
        kospi = [row for row in rows if row["kospi_marker"]]
        # Five-character issuer IDs do not prove a security's six-character ticker,
        # preferred/common share class, or historical mapping effective interval.
        mapping = dict(
            version="official_security_mapping_review_v1",
            start=start.isoformat(),
            end=end.isoformat(),
            verified_count=0,
            universe_count=len(universe),
            securities={
                t: dict(
                    status="BLOCKED_ON_IDENTITY_MAPPING",
                    official_security_id=None,
                    effective_period=None,
                    ambiguous=None,
                )
                for t in universe
            },
            candidate_issuer_ids=sorted({r["issuer_code"] for r in kospi}),
        )
        corporate = dict(
            version="official_corporate_action_review_v1",
            start=start.isoformat(),
            end=end.isoformat(),
            verified_count=0,
            universe_count=len(universe),
            query_results=[wrapped(a.to_dict()) for a in actions],
            official_schedule_rows=len(rows),
            kospi_schedule_rows=len(kospi),
            unique_kospi_disclosure_references=len({r["disclosure_id"] for r in kospi}),
            verified_universe_events=None,
            prices_adjusted=False,
            securities={
                t: dict(
                    status="BLOCKED_ON_IDENTITY_MAPPING",
                    additional_blockers=["BLOCKED_ON_SOURCE_COVERAGE"],
                    verified_events=None,
                )
                for t in universe
            },
            limitations=[
                "issuer_codes_are_not_security_mappings",
                "market_filter_returned_multiple_markets",
                "calendar_rows_do_not_certify_complete_no_event_semantics",
                "listing_termination_relisting_and_all_other_actions_not_covered",
                "schedule_dates_and_filing_dates_are_not_verified_effective_dates",
                "latest_public_responses_not_point_in_time_evidence",
            ],
            alternative_route=dict(
                status="BLOCKED_ON_DART_CREDENTIAL",
                credential="DART Open API authentication key (crtfc_key)",
                application_url="https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do",
                credential_sufficient_for_complete_coverage=False,
            ),
        )
        corporate_check = check(
            "CORPORATE_ACTION_EVIDENCE",
            [
                "unverified_historical_security_mapping",
                "incomplete_event_class_and_no_event_coverage",
                "BLOCKED_ON_DART_CREDENTIAL",
            ],
            artifact_fingerprint=digest(corporate),
        )
        old = parent["package"]["evidence"]
        checks = []
        for c in old["checks"]:
            replacement = {
                "SESSION_ORDERING": check(
                    "SESSION_ORDERING",
                    ordering.reasons,
                    **d,
                    scope="verified_warmup_through_execution_tail",
                ),
                "CALENDAR_EVIDENCE": comparison,
                "CORPORATE_ACTION_EVIDENCE": corporate_check,
                "SETTLEMENT_EVIDENCE": settlement,
            }.get(c["category"])
            checks.append(
                replacement
                or EvidenceCheck(
                    c["category"], tuple(c["reasons"]), FrozenJSON.freeze(c["details"])
                )
            )
        # Preserve all locked input identities and anchor-only dataset identity.
        evidence = BaselineRunEvidence(tuple(checks), FrozenJSON.freeze(old["identities"]))
        package = BaselineRunEvidencePackage(
            evidence,
            boundaries,
            FrozenJSON.freeze(
                dict(parent_sources=parent["package"]["official_sources"], new_sources=sources)
            ),
            FrozenJSON.freeze(parent["package"]["model_pin"]),
        )
        comparison_doc = dict(
            version="official_calendar_comparison_v1",
            start=start.isoformat(),
            end=end.isoformat(),
            database=db,
            ordering=d,
            aggregate=aggregate.to_dict(),
            per_security=comparison.to_dict(),
            missing_security_count=len(comparison.details.to_dict().get("missing_sessions", {})),
            unexplained_absence_is_not_holiday=True,
        )
        result = {
            "official-calendar": wrapped(calendar.to_dict()),
            "exact-boundaries": wrapped(b),
            "security-mapping": wrapped(mapping),
            "corporate-actions": wrapped(corporate),
            "calendar-verification": wrapped(comparison_doc),
            "settlement-calendar": wrapped(
                dict(
                    calendar_fingerprint=calendar.identifier,
                    official_rule_source=rule,
                    verification=settlement.to_dict(),
                    possible_trade_sessions=len(trades),
                )
            ),
            "integrated-evidence-package": dict(
                package=package.to_dict(), package_fingerprint=package.fingerprint
            ),
        }
        result["official-evidence-manifest"] = wrapped(
            dict(
                version="baseline_official_evidence_v1",
                parent_package_fingerprint=parent["package_fingerprint"],
                artifacts={
                    k: v.get("fingerprint", v.get("package_fingerprint")) for k, v in result.items()
                },
                ready=package.to_dict()["ready"],
                protected_sha256_before=before,
                protected_sha256_after=before,
                no_prediction_or_simulation=True,
            )
        )
        return result
    finally:
        require(before == {p: file_sha(root / p) for p in protected}, "protected_input_changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "new_output_directory_required")
    result = verify(args.root.resolve(), args.raw_dir.resolve())
    args.output.mkdir(parents=True)
    for name, value in result.items():
        (args.output / (name + ".json")).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n"
        )


if __name__ == "__main__":
    main()
