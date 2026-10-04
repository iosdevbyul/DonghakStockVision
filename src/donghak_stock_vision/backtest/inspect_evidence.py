"""Inspect existing frozen inputs, never approve a new run range or execute a model.

Run with python -m donghak_stock_vision.backtest.inspect_evidence --help.
JSON is written only to stdout; callers may save it as a new review artifact.
"""

import argparse
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy
from donghak_stock_vision.backtest.baseline_quality import BaselineQualityPolicy
from donghak_stock_vision.backtest.baseline_runner import VERSION as RUNNER_VERSION
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.run_evidence import (
    BaselineRunEvidence,
    audit_database,
    calendar_check,
    check,
    corporate_check,
    dataset_check,
    file_sha,
    identity_check,
    settlement_check,
)
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import FEATURES, canonical, digest
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import HGBArtifact


def read(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text())
    require(type(result) is dict, "metadata_object_required")
    return dict(result)


def inspect(
    market: Path,
    analysis: Path,
    frozen: Path,
    models: Path,
    evaluation_lock: Path,
    expected_dataset_id: str,
    expected_source_sha: str,
    calendar_review: Path | None = None,
) -> BaselineRunEvidence:
    metadata_paths = (
        tuple(
            frozen / name
            for name in (
                "manifest.json",
                "policy.json",
                "provenance.json",
                "universe.json",
                "availability.json",
            )
        )
        + tuple(
            models / name
            for name in (
                "lock.json",
                "plan.json",
                "qualification.json",
                "up-research-only.pkl",
                "down-research-only.pkl",
            )
        )
        + (evaluation_lock, analysis)
    )
    if calendar_review is not None:
        metadata_paths += (calendar_review,)
    before = {str(p): file_sha(p) for p in metadata_paths}
    manifest, policy, provenance, universe, availability = (read(p) for p in metadata_paths[:5])
    identity = manifest["identity"]
    # This is inspection of an existing frozen range, not a baseline run-range approval.
    start, end = date.fromisoformat(identity["start"]), date.fromisoformat(identity["end"])
    tickers = tuple(universe["tickers"])
    exclusions = tuple(
        date.fromisoformat(d) for d in policy["research_calendar"]["incomplete_dates"]
    )
    ordering, db = audit_database(market, tickers, start, end, exclusions)
    full_ordering, full_db = audit_database(
        market,
        tickers,
        date.fromisoformat(db["first_date"]),
        date.fromisoformat(db["last_date"]),
        exclusions,
    )
    require(full_db["sha256_before"] == db["sha256_before"], "source_changed_between_audits")
    full_details = full_ordering.details.to_dict()
    full_details.pop("sessions")
    details = ordering.details.to_dict()
    actual = dict(
        db_sha256=db["sha256_before"],
        rows_sha256=details["rows_sha256"],
        universe_sha256=digest(universe),
        dataset_id="oos-" + digest(identity),
        rows=details["rows"],
    )
    expected = dict(
        db_sha256=expected_source_sha,
        rows_sha256=identity["rows_sha256"],
        universe_sha256=identity["universe_sha256"],
        dataset_id=expected_dataset_id,
        rows=identity["rows"],
    )
    dataset = dataset_check(actual, expected)
    links = (
        manifest["dataset_id"] == expected_dataset_id,
        manifest["dataset_fingerprint"] == digest(identity),
        digest(policy) == identity["policy_sha256"],
        digest(provenance) == manifest["provenance_sha256"],
        digest(availability) == identity["availability_sha256"],
        provenance["source_db_sha256"] == expected_source_sha,
        universe["count"] == identity["universe_count"] == len(tickers),
        policy["start"] == identity["start"] and policy["end"] == identity["end"],
    )
    if not all(links):
        dataset = check(
            "DATASET_IDENTITY",
            (*dataset.reasons, "frozen_manifest_link_mismatch"),
            **dataset.details.to_dict(),
        )
    quality = BaselineQualityPolicy()
    baseline = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
    actual_models: dict[str, Any] = dict(
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        quality_policy=quality.fingerprint,
        baseline_policy=baseline.fingerprint,
        signal_policy=quality.signal_policy().fingerprint,
        cost_policy=baseline.require_costs().fingerprint,
        runner_version=RUNNER_VERSION,
    )
    locked = read(evaluation_lock)
    contract = locked["contract"]
    lock = read(models / "lock.json")
    extra_reasons = []
    try:
        require(digest(contract) == locked["lock_id"], "evaluation_contract_mismatch")
        require(contract["dataset_id"] == expected_dataset_id, "evaluation_dataset_mismatch")
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
        # Consistency of reconstructed envelopes only, NOT independent run-pin approval.
        resolver = TrustedHGBResolver(
            models, *artifacts, up_pin=artifacts[0].fingerprint, down_pin=artifacts[1].fingerprint
        )
        verified = resolver.verify()  # Bytes/hash/provenance only. Never call .load().
        for item in verified:
            artifact = item.artifact
            actual_models[artifact.direction + "_sha"] = artifact.checkpoint_sha256
            actual_models[artifact.direction + "_fingerprint"] = artifact.fingerprint
        actual_models.update(lock_id=lock["lock_id"], qualification_id=contract["qualification_id"])
    except (ValueError, KeyError, TypeError, OSError) as error:
        extra_reasons.append("artifact_verification_failed:" + type(error).__name__)
    expected_models = dict(
        up_sha=contract["model_hashes"]["up"],
        down_sha=contract["model_hashes"]["down"],
        lock_id=contract["experiment_lock_id"],
        qualification_id=contract["qualification_id"],
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        quality_policy="c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c",
        baseline_policy="89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8",
        cost_policy="c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef",
        signal_policy=quality.signal_policy().fingerprint,
        runner_version="baseline_runner_v1",
    )
    artifacts_check = identity_check(actual_models, expected_models)
    if extra_reasons:
        artifacts_check = check(
            "ARTIFACT_IDENTITY",
            (*artifacts_check.reasons, *extra_reasons),
            **artifacts_check.details.to_dict(),
        )
    review_comparison: dict[str, Any] = {"status": "not_supplied"}
    if calendar_review is not None:
        review = read(calendar_review)
        observed = {d for ds in details["sessions"].values() for d in ds}
        absent_weekdays = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5 and cursor.isoformat() not in observed:
                absent_weekdays.append(cursor.isoformat())
            cursor += timedelta(days=1)
        review_comparison = dict(
            status="secondary_cross_check_not_authoritative_calendar",
            review_sha256=file_sha(calendar_review),
            absent_observed_weekdays=absent_weekdays,
            not_in_prior_review=sorted(set(absent_weekdays) - set(review["dates"])),
            source_urls=review["sources"],
        )
    calendar = calendar_check(details["sessions"], start, end, exclusions, None, None)
    corporate = corporate_check(expected_dataset_id, digest(universe), start, end, None, None)
    settlement = settlement_check((), None, None)
    # Retain deterministic session identities without copying the entire market dataset.
    details["per_ticker_sessions"] = {
        t: dict(
            count=len(ds),
            first=ds[0] if ds else None,
            last=ds[-1] if ds else None,
            sha256=digest(ds),
        )
        for t, ds in details.pop("sessions").items()
    }
    ordering = check("SESSION_ORDERING", (*ordering.reasons, *full_ordering.reasons), **details)
    after = {str(p): file_sha(p) for p in metadata_paths}
    require(before == after, "protected_artifact_changed")
    return BaselineRunEvidence(
        (
            check(
                "RUN_RANGE",
                ["oos_dataset_range_is_not_baseline_run_authorization"],
                inspected_start=start.isoformat(),
                inspected_end=end.isoformat(),
                approved_run_range=None,
            ),
            dataset,
            ordering,
            calendar,
            corporate,
            settlement,
            artifacts_check,
        ),
        FrozenJSON.freeze(
            dict(
                inspection_purpose="existing_frozen_dataset_not_run_input_freeze",
                source_database=db,
                existing_calendar_review=review_comparison,
                full_database_session_audit=full_details,
                dataset_id=expected_dataset_id,
                universe_sha256=digest(universe),
                approved_exclusions=[d.isoformat() for d in exclusions],
                source_metadata={k: v for k, v in provenance.items() if k == "source_metadata"},
                protected_files_unchanged=True,
                analysis_db_sha256=before[str(analysis)],
                metadata_hashes={p.name: before[str(p)] for p in metadata_paths if p != analysis},
                model_and_policy_identities=actual_models,
                source_revisions="latest_only_not_PIT",
                baseline_run_approved=False,
            )
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("market-db", "analysis-db", "frozen-dir", "models-dir", "evaluation-lock"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--calendar-review", type=Path)
    parser.add_argument("--expected-dataset-id", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    args = parser.parse_args()
    result = inspect(
        args.market_db,
        args.analysis_db,
        args.frozen_dir,
        args.models_dir,
        args.evaluation_lock,
        args.expected_dataset_id,
        args.expected_source_sha,
        args.calendar_review,
    )
    print(
        json.dumps(
            dict(evidence=result.to_dict(), evidence_fingerprint=result.fingerprint),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
