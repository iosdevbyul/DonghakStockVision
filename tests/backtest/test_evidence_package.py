"""Synthetic evidence only: no prediction, fitting or simulation."""

import ast
import inspect
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from typing import Any

import pytest

from donghak_stock_vision.backtest import evidence_package, package_evidence
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import (
    ACTION_CLASSES,
    ANCHOR_END,
    ANCHOR_START,
    AnchorRange,
    BaselineRunEvidencePackage,
    corporate_coverage,
    derive_boundaries,
)
from donghak_stock_vision.backtest.run_evidence import (
    CATEGORIES,
    BaselineRunEvidence,
    calendar_check,
    check,
    identity_check,
)
from donghak_stock_vision.features import engine

H = "a" * 64
START, END = date(2025, 3, 20), date(2025, 3, 24)
UNIVERSE = ("005930",)
EXCLUDED = (date(2025, 3, 21),)


def official_fixture(**changes: Any) -> FrozenJSON:
    """Explicit synthetic reviewed calendar; never claimed as real evidence."""
    days = {}
    cursor = date(2025, 2, 1)
    while cursor <= date(2025, 4, 15):
        days[cursor.isoformat()] = "weekend" if cursor.weekday() >= 5 else "session"
        cursor += timedelta(days=1)
    days["2025-03-25"] = "holiday"
    value = dict(
        days=days,
        source_urls=["https://official.example.test/calendar"],
        source_content_hashes=[H],
        review_reference="synthetic_test_only",
        effective_start="2025-02-01",
        effective_end="2025-04-15",
        rule="kospi_cash_equity_T_plus_2_settlement_sessions",
    )
    value.update(changes)
    return FrozenJSON.freeze(value)


def test_exact_approved_range_and_identity() -> None:
    approved = AnchorRange(ANCHOR_START, ANCHOR_END)
    assert approved.approval().status == "VERIFIED"
    changed = replace(approved, start=date(2024, 7, 2))
    assert changed.approval().status == "BLOCKED_ON_RUN_RANGE"
    assert changed.fingerprint != approved.fingerprint
    assert not approved.contains_anchor(date(2024, 6, 28))
    assert not approved.contains_anchor(date(2025, 7, 1))
    assert approved.approval().details.to_dict()["untouched_final_test"] is False


def test_feature_context_matches_existing_slices() -> None:
    source = inspect.getsource(engine.feature_vector)
    assert "history[-12:]" in source and "history[-11:]" in source
    assert evidence_package.PRECEDING_CONTEXT_SESSIONS == 11


def test_tail_and_settlement_are_not_signal_anchors() -> None:
    doc = official_fixture()
    anchors = AnchorRange(START, END)
    result = derive_boundaries(anchors, EXCLUDED, doc, doc.identifier).to_dict()
    assert result["status"] == "VERIFIED"
    assert len(result["warmup_sessions"]) == 11
    assert result["execution_tail"] == [
        "2025-03-26",
        "2025-03-27",
        "2025-03-28",
        "2025-03-31",
        "2025-04-01",
    ]
    assert result["settlement_tail"] == ["2025-04-02", "2025-04-03"]
    assert result["settlement_mapping"]["2025-03-24"] == "2025-03-27"
    assert result["tail_generates_signals"] is False
    assert result["warmup_generates_signals"] is False
    assert all(
        not anchors.contains_anchor(date.fromisoformat(d))
        for d in result["warmup_sessions"] + result["execution_tail"]
    )


@pytest.mark.parametrize(
    "defect", ["missing", "pin", "gap", "warmup", "tail", "holiday", "rule", "cost"]
)
def test_incomplete_boundary_evidence_blocks(defect: str) -> None:
    data = official_fixture().to_dict()
    if defect == "gap":
        del data["days"]["2025-03-25"]
    if defect == "warmup":
        data["days"] = {d: k for d, k in data["days"].items() if d >= "2025-03-19"}
    if defect == "tail":
        data["days"] = {d: k for d, k in data["days"].items() if d <= "2025-03-28"}
    if defect == "holiday":
        data["days"]["2025-03-21"] = "holiday"
    if defect == "rule":
        data["rule"] = "calendar_days_plus_two"
    if defect == "cost":
        data["effective_end"] = "2025-03-24"
    doc = FrozenJSON.freeze(data)
    result = derive_boundaries(
        AnchorRange(START, END),
        EXCLUDED,
        None if defect == "missing" else doc,
        H if defect == "pin" else doc.identifier,
    )
    assert result.to_dict()["status"] == "BLOCKED_ON_CALENDAR_EVIDENCE"
    assert result.to_dict()["execution_tail"] is None


def test_calendar_missing_unexpected_and_exclusion() -> None:
    doc = official_fixture()
    assert not calendar_check(
        {"005930": ["2025-03-20", "2025-03-24"]}, START, END, EXCLUDED, doc, doc.identifier
    ).reasons
    missing = calendar_check({"005930": ["2025-03-20"]}, START, END, EXCLUDED, doc, doc.identifier)
    assert "unexplained_missing_ticker_sessions" in missing.reasons
    unexpected = calendar_check(
        {"005930": ["2025-03-22"]}, START, END, EXCLUDED, doc, doc.identifier
    )
    assert "observed_non_session" in unexpected.reasons


def corporate_fixture(**changes: Any) -> FrozenJSON:
    return official_fixture(
        universe=list(UNIVERSE),
        start=START.isoformat(),
        end=END.isoformat(),
        action_classes=list(ACTION_CLASSES),
        complete_query=True,
        all_pages_retrieved=True,
        security_mapping={"005930": "official_security_fixture"},
        events=[],
        basis="official_query",
        **changes,
    )


def test_complete_no_event_review_and_missing_review() -> None:
    doc = corporate_fixture()
    assert not corporate_coverage(UNIVERSE, START, END, doc, doc.identifier).reasons
    assert corporate_coverage(UNIVERSE, START, END, None, None).reasons


@pytest.mark.parametrize(
    "key,value",
    [
        ("universe", []),
        ("start", "2025-03-21"),
        ("end", "2025-03-23"),
        ("action_classes", []),
        ("complete_query", False),
        ("all_pages_retrieved", False),
        ("security_mapping", {}),
        ("events", None),
        ("basis", "no_price_jump"),
    ],
)
def test_incomplete_corporate_review_blocks(key: str, value: Any) -> None:
    data = corporate_fixture().to_dict()
    data[key] = value
    doc = FrozenJSON.freeze(data)
    assert corporate_coverage(UNIVERSE, START, END, doc, doc.identifier).reasons


def test_event_preserved_but_not_economically_approved() -> None:
    data = corporate_fixture().to_dict()
    data["events"] = [
        dict(
            ticker="005930", kind="split", official_reference="official_event_fixture", raw_sha256=H
        )
    ]
    doc = FrozenJSON.freeze(data)
    result = corporate_coverage(UNIVERSE, START, END, doc, doc.identifier)
    assert "event_requires_existing_quality_resolution" in result.reasons
    assert result == corporate_coverage(UNIVERSE, START, END, doc, doc.identifier)


def package() -> BaselineRunEvidencePackage:
    evidence = BaselineRunEvidence(tuple(check(c, ()) for c in CATEGORIES), FrozenJSON.freeze({}))
    return BaselineRunEvidencePackage(
        evidence,
        FrozenJSON.freeze(dict(status="VERIFIED")),
        FrozenJSON.freeze({}),
        FrozenJSON.freeze({}),
    )


def test_package_identity_audit_metadata_and_immutability() -> None:
    first = package()
    assert first == package()
    assert first.fingerprint == package().fingerprint
    assert (
        first.report(retrieved_at="a", local_root="/first")["package_fingerprint"]
        == (first.report(retrieved_at="b", local_root="/second")["package_fingerprint"])
    )
    assert (
        replace(first, sources=FrozenJSON.freeze(dict(source=H))).fingerprint != first.fingerprint
    )
    assert (
        replace(first, model_pin=FrozenJSON.freeze(dict(model=H))).fingerprint != first.fingerprint
    )
    with pytest.raises(FrozenInstanceError):
        first.version = "other"  # type: ignore[misc]


def test_unresolved_blocker_prevents_readiness() -> None:
    value = package()
    checks = list(value.evidence.checks)
    checks[3] = check("CALENDAR_EVIDENCE", ["missing"])
    blocked = replace(value, evidence=replace(value.evidence, checks=tuple(checks)))
    assert blocked.to_dict()["ready"] is False
    assert (
        replace(value, boundaries=FrozenJSON.freeze(dict(status="BLOCKED"))).to_dict()["ready"]
        is False
    )


@pytest.mark.parametrize(
    "field",
    [
        "up_sha",
        "down_sha",
        "features",
        "qualification_id",
        "lock_id",
        "baseline_policy",
        "quality_policy",
        "up_fingerprint",
    ],
)
def test_model_pin_mismatch_fails_closed(field: str) -> None:
    expected = dict(
        up_sha=H,
        down_sha=H,
        up_fingerprint=H,
        down_fingerprint=H,
        lock_id=H,
        qualification_id=H,
        features=["fixture"],
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        signal_policy=H,
        quality_policy=H,
        baseline_policy=H,
        cost_policy=H,
        runner_version="baseline_runner_v1",
    )
    assert not identity_check(expected, expected).reasons
    actual = {**expected, field: "changed"}
    assert "mismatch:" + field in identity_check(actual, expected).reasons


def test_no_model_or_runner_execution_calls() -> None:
    for module in (evidence_package, package_evidence):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"predict", "predict_proba", "fit", "load", "run"}


def test_model_envelope_verification_without_deserialization(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import pickle
    from decimal import Decimal

    from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
    from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy
    from donghak_stock_vision.backtest.baseline_quality import BaselineQualityPolicy
    from donghak_stock_vision.backtest.validation import ContractError
    from donghak_stock_vision.data.learning import FEATURES, digest
    from tests.learning.test_signal_integration import bundle

    root = tmp_path / "models"
    resolver = bundle(root)
    up, down = resolver.artifacts
    policy = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
    quality = BaselineQualityPolicy()
    expected = dict(
        up_sha=up.checkpoint_sha256,
        down_sha=down.checkpoint_sha256,
        up_fingerprint=up.fingerprint,
        down_fingerprint=down.fingerprint,
        lock_id=up.experiment_lock_id,
        qualification_id=up.qualification_id,
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        signal_policy=quality.signal_policy().fingerprint,
        quality_policy=quality.fingerprint,
        baseline_policy=policy.fingerprint,
        cost_policy=policy.require_costs().fingerprint,
        runner_version="baseline_runner_v1",
    )
    contract = dict(
        model_hashes={a.direction: a.checkpoint_sha256 for a in (up, down)},
        experiment_lock_id=up.experiment_lock_id,
        qualification_id=up.qualification_id,
        model_parameters={a.direction: json.loads(a.parameters_json) for a in (up, down)},
        versions=dict(up.runtime_versions),
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
    )
    path = tmp_path / "evaluation-lock.json"
    path.write_text(json.dumps(dict(contract=contract, lock_id=digest(contract))))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Model deserialization is forbidden")

    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    first = package_evidence.model_pin(root, path, expected)
    assert first == package_evidence.model_pin(root, path, expected)
    assert first.to_dict()["checkpoint_deserialized"] is False
    for field in ("up_sha", "down_sha", "up_fingerprint", "qualification_id", "baseline_policy"):
        with pytest.raises(ContractError, match="pin_mismatch"):
            package_evidence.model_pin(root, path, {**expected, field: "b" * 64})
    contract["features"] = list(reversed(FEATURES))
    path.write_text(json.dumps(dict(contract=contract, lock_id=digest(contract))))
    with pytest.raises(ValueError):
        package_evidence.model_pin(root, path, expected)


def test_published_package_and_sources_integrity() -> None:
    import gzip
    import hashlib
    import json
    from pathlib import Path

    from donghak_stock_vision.data.learning import digest

    docs = Path(__file__).resolve().parents[2] / "docs"
    report = json.loads((docs / "baseline-run-evidence-package.json").read_text())
    assert digest(report["package"]) == report["package_fingerprint"]
    assert report["package"]["ready"] is False
    for source in report["package"]["official_sources"]["documents"]:
        compressed = (docs / "evidence/baseline-run" / source["file"]).read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == source["stored_sha256"]
        assert hashlib.sha256(gzip.decompress(compressed)).hexdigest() == source["raw_sha256"]
    identities = report["package"]["evidence"]["identities"]
    assert identities["protected_sha256_before"] == identities["protected_sha256_after"]


def test_unsupported_tax_schedule_date_blocks_settlement() -> None:
    from donghak_stock_vision.backtest.run_evidence import settlement_check

    doc = official_fixture(
        days={"2030-01-07": "session", "2030-01-08": "session", "2030-01-09": "session"},
        effective_start="2030-01-01",
        effective_end="2030-12-31",
    )
    result = settlement_check((date(2030, 1, 7),), doc, doc.identifier)
    assert result.status == "BLOCKED_ON_SETTLEMENT_EVIDENCE"
    assert result.details.to_dict()["mapping"] == {}
