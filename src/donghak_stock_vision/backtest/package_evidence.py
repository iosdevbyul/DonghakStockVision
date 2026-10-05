"""Build a blocked-or-verified evidence package using read-only local inputs.

This command does not download data, deserialize models, or execute the runner.
The current official source inventory supplies principles, not complete date or
corporate-action coverage. Missing review documents remain explicit blockers.
"""

import argparse
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy
from donghak_stock_vision.backtest.baseline_quality import BaselineQualityPolicy
from donghak_stock_vision.backtest.baseline_runner import VERSION as RUNNER_VERSION
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import (
    ANCHOR_END,
    ANCHOR_START,
    AnchorRange,
    BaselineRunEvidencePackage,
    derive_boundaries,
)
from donghak_stock_vision.backtest.inspect_evidence import read
from donghak_stock_vision.backtest.run_evidence import (
    BaselineRunEvidence,
    audit_database,
    check,
    file_sha,
    identity_check,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import FEATURES, canonical, digest
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import HGBArtifact


def model_pin(models: Path, evaluation_lock: Path, expected: dict[str, Any]) -> FrozenJSON:
    """Reconstruct the already approved envelopes and verify bytes without load()."""
    locked = read(evaluation_lock)
    contract = locked["contract"]
    require(digest(contract) == locked["lock_id"], "evaluation_lock_mismatch")
    lock = read(models / "lock.json")
    artifacts = tuple(
        HGBArtifact(
            direction,
            contract["model_hashes"][direction],
            contract["experiment_lock_id"],
            contract["qualification_id"],
            lock["training_snapshot_id"],
            canonical(contract["model_parameters"][direction]),
            tuple((k, contract["versions"][k]) for k in ("numpy", "scikit-learn", "scipy")),
            tuple(contract["features"]),
            contract["feature_version"],
            contract["label_version"],
        )
        for direction in ("up", "down")
    )
    baseline = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
    quality = BaselineQualityPolicy()
    actual = dict(
        up_sha=artifacts[0].checkpoint_sha256,
        down_sha=artifacts[1].checkpoint_sha256,
        up_fingerprint=artifacts[0].fingerprint,
        down_fingerprint=artifacts[1].fingerprint,
        lock_id=contract["experiment_lock_id"],
        qualification_id=contract["qualification_id"],
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        signal_policy=quality.signal_policy().fingerprint,
        quality_policy=quality.fingerprint,
        baseline_policy=baseline.fingerprint,
        cost_policy=baseline.require_costs().fingerprint,
        runner_version=RUNNER_VERSION,
    )
    result = identity_check(actual, expected)
    require(not result.reasons, "approved_model_or_policy_pin_mismatch")
    resolver = TrustedHGBResolver(
        models,
        *artifacts,
        up_pin=expected["up_fingerprint"],
        down_pin=expected["down_fingerprint"],
    )
    resolver.verify()
    return FrozenJSON.freeze(
        dict(
            version="baseline_model_envelope_pin_v1",
            identities=actual,
            estimator_class="sklearn.ensemble.HistGradientBoostingClassifier",
            parameters=contract["model_parameters"],
            runtime_versions=contract["versions"],
            training_snapshot_id=lock["training_snapshot_id"],
            evaluation_lock_id=locked["lock_id"],
            checkpoint_deserialized=False,
            executable_direction="up",
            down_role="provenance_only",
        )
    )


def build(root: Path, approval_file: Path, source_file: Path) -> BaselineRunEvidencePackage:
    approval = read(approval_file)
    sources = read(source_file)
    for source in sources["documents"]:
        path = source_file.parent / source["file"]
        require(file_sha(path) == source["stored_sha256"], "source_file_hash_mismatch")
        require(
            hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == source["raw_sha256"],
            "source_raw_hash_mismatch",
        )
    market, analysis = root / ".data/market.sqlite3", root / ".data/analysis.sqlite3"
    models = root / ".data/phase2-improvement-20261001"
    frozen = root / ".data/oos-preparation-20261001/part2-freeze"
    evaluation = root / ".data/oos-evaluation-20261003/evaluation-lock.json"
    protected = (
        (market, analysis, evaluation, approval_file, source_file)
        + tuple(sorted(p for p in frozen.iterdir() if p.is_file()))
        + tuple(
            models / n
            for n in (
                "lock.json",
                "qualification.json",
                "plan.json",
                "up-research-only.pkl",
                "down-research-only.pkl",
            )
        )
    )
    before = {
        str(p.relative_to(root)) if p.is_relative_to(root) else p.name: file_sha(p)
        for p in protected
    }
    try:
        require(file_sha(market) == approval["market_sha256"], "source_database_mismatch")
        universe_doc = read(frozen / "universe.json")
        require(digest(universe_doc) == approval["universe_sha256"], "universe_mismatch")
        universe = tuple(universe_doc["tickers"])
        require(len(universe) == 993, "approved_universe_required")
        anchors = AnchorRange(
            date.fromisoformat(approval["start"]), date.fromisoformat(approval["end"])
        )
        require(not anchors.approval().reasons, "unapproved_anchor_range")
        exclusions = tuple(date.fromisoformat(d) for d in approval["excluded_dates"])
        require(
            exclusions == (date(2024, 3, 28), date(2025, 3, 21)), "approved_exclusions_required"
        )
        ordering, database = audit_database(
            market, universe, anchors.start, anchors.end, exclusions
        )
        details = ordering.details.to_dict()
        sessions = details.pop("sessions")
        details["per_ticker_sessions"] = {
            t: dict(
                count=len(ds),
                first=ds[0] if ds else None,
                last=ds[-1] if ds else None,
                fingerprint=digest(ds),
            )
            for t, ds in sessions.items()
        }
        ordering = check("SESSION_ORDERING", ordering.reasons, **details)
        identity = dict(
            scope="anchor_rows_only_pending_verified_warmup_and_tail",
            anchor_range=anchors.to_dict(),
            market_sha256=database["sha256_before"],
            rows_sha256=details["rows_sha256"],
            universe_sha256=digest(universe_doc),
            exclusions=approval["excluded_dates"],
            source="krx",
            market="KOSPI",
            adjustment="unadjusted",
        )
        pin = model_pin(models, evaluation, approval["model_and_policy_pins"])
        boundaries = derive_boundaries(anchors, exclusions, None, None)
        checks = (
            anchors.approval(),
            check(
                "DATASET_IDENTITY", (), **identity, dataset_id="baseline-anchor-" + digest(identity)
            ),
            ordering,
            check(
                "CALENDAR_EVIDENCE",
                ["complete_official_date_classification_not_retrieved"],
                verified_date_count=0,
                observation_is_not_calendar_proof=True,
            ),
            check(
                "CORPORATE_ACTION_EVIDENCE",
                ["complete_official_query_and_security_mapping_missing"],
                required_universe_count=len(universe),
                verified_ticker_count=0,
                returned_events=None,
                query_executed=False,
                required_period="warmup_through_execution_tail_unresolved",
            ),
            check(
                "SETTLEMENT_EVIDENCE",
                ["official_rule_found_but_date_mapping_unverified"],
                rule="kospi_cash_equity_T_plus_2_settlement_sessions",
                mapping=None,
            ),
            check("ARTIFACT_IDENTITY", (), pin_fingerprint=pin.identifier, **pin.to_dict()),
        )
        baseline = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
        evidence = BaselineRunEvidence(
            checks,
            FrozenJSON.freeze(
                dict(
                    dataset=identity,
                    source_database=database,
                    backtest_contract_fingerprint=baseline.contract(
                        ANCHOR_START, ANCHOR_END
                    ).fingerprint,
                    model_pin_fingerprint=pin.identifier,
                    protected_sha256_before=before,
                    protected_sha256_after=before,
                    protected_files_unchanged=True,
                    runner_integration_debt="separate_anchor_cutoff_from_execution_tail_before_run",
                )
            ),
        )
        return BaselineRunEvidencePackage(evidence, boundaries, FrozenJSON.freeze(sources), pin)
    finally:
        after = {
            str(p.relative_to(root)) if p.is_relative_to(root) else p.name: file_sha(p)
            for p in protected
        }
        require(before == after, "protected_input_changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root.resolve(), args.approval.resolve(), args.sources.resolve())
    print(
        json.dumps(
            dict(package=result.to_dict(), package_fingerprint=result.fingerprint),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
