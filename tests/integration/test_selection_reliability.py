"""Offline policy/reader tests; synthetic prices are never real research performance."""

import copy
import hashlib
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.features.research import FEATURES_V2
from donghak_stock_vision.models.experiment import (
    estimator_bytes,
    fit_candidate,
    predict_candidate,
)
from donghak_stock_vision.models.selection_reliability import paired_gain, select
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage.research_checkpoint import verified_estimator
from donghak_stock_vision.storage.selection_review import retain_assessment, selection_view


def candidate_report(name: str, ap: float, ba: float = 0.57) -> dict[str, Any]:
    metrics = dict(pr_auc_ap=ap, balanced_accuracy=ba, brier_score=0.20)
    robust = dict(
        version="historical_robust_lexicographic_v1",
        sufficient_folds=4,
        passes=4,
        worst_balanced_accuracy=ba,
        worst_baseline_ap_gain=ap - 0.5,
        negative_dispersion=0.0,
        worst_regime_balanced_accuracy=0.5,
        negative_overfit_gap=-0.03,
        heldout_or_diagnostic_used=False,
    )
    return dict(
        experiment_id=digest(name),
        config=dict(goal=dict(horizon=5), stock_policy="heldout"),
        mean_seen_cv_ap=ap,
        overfit_gap=0.03,
        robust_summary=robust,
        fold_seen_metrics=[dict(metrics) for _ in range(4)],
        fold_baselines=[
            dict(training_prior=dict(pr_auc_ap=0.5, brier_score=0.25)) for _ in range(4)
        ],
        fold_generalization=[
            dict(
                seen_symbols=dict(counts=dict(dates=80)),
                heldout_symbols=dict(metrics={"score": 999}),
            )
            for _ in range(4)
        ],
        nonoverlapping=[
            dict(calendar_dates=[f"{2020 + i}-01-{j:02d}" for j in range(1, 7)]) for i in range(4)
        ],
        deferred_diagnostic={"score": 999},
    )


def test_selection_abstention_and_q3_firewall() -> None:
    a, b = candidate_report("stable", 0.62), candidate_report("other", 0.57)
    chosen = select([selection_view(a), selection_view(b)])
    assert chosen["stable_selection"] == a["experiment_id"] and chosen["rank_agreement"] == 1
    altered = copy.deepcopy(a)
    altered["deferred_diagnostic"] = {"score": -999}
    for f in altered["fold_generalization"]:
        f["heldout_symbols"] = {"score": -999}
    assert select([selection_view(altered), selection_view(b)]) == chosen
    a["overfit_gap"] = 0.4
    rejected = select([selection_view(a), selection_view(b)])
    assert rejected["state"] == "ABSTAIN" and "overfit" in rejected["reasons"]
    # The maximum AP point is a separate comparator, never the recorded selection.
    a["mean_seen_cv_ap"] = 0.99
    assert select([selection_view(a), selection_view(b)])["highest_search_ap"] == a["experiment_id"]


def test_calendar_not_stock_rows_controls_significance() -> None:
    small = paired_gain([0.1] * 8, [0.1, 0.1], 0.02)
    assert (
        small["status"] == "IMPROVEMENT_NOT_ESTABLISHED"
        and not small["sufficient_calendar_evidence"]
    )
    enough = paired_gain([0.1] * 40, [0.1, 0.1], 0.02)
    assert enough["status"] == "LOCAL_PAIRED_GAIN" and not enough["performance_approved"]
    assert paired_gain([0.1] * 40, [0.1, -0.01], 0.02)["status"] == "IMPROVEMENT_NOT_ESTABLISHED"
    assert paired_gain([-0.1] * 40, [-0.1, -0.1], 0.02)["status"] == "IMPROVEMENT_NOT_ESTABLISHED"
    assert paired_gain([0.1] * 40, [0.1, 0.1], -0.2)["status"] == "IMPROVEMENT_NOT_ESTABLISHED"
    assert paired_gain([0.1] * 40, [0.1, 0.1], 0.02) == enough


@pytest.mark.parametrize("family", ["linear", "random_forest", "hist_gradient_boosting"])
def test_checkpoint_inference_without_refit(
    tmp_path: Path, family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(12)
    samples = []
    for i in range(256):
        day = date(2020, 1, 1) + timedelta(days=i)
        values = tuple(rng.random() for _ in FEATURES_V2)
        samples.append(
            Sample(
                "000001",
                day,
                datetime(2020, 1, 1, tzinfo=UTC),
                values,
                "up",
                int(values[0] > 0.5),
                day + timedelta(days=2),
                day + timedelta(days=6),
                datetime(2024, 1, 1, tzinfo=UTC),
                "labeled",
                digest(i),
                tuple(day + timedelta(days=j) for j in range(2, 7)),
            )
        )
    from donghak_stock_vision.models.research_v2 import candidates

    c = next(c for c in candidates() if c.family == family)
    fitted = fit_candidate(c, samples, feature_names=list(FEATURES_V2))
    body = estimator_bytes(fitted)
    path = tmp_path / "fixture.checkpoint"
    path.write_bytes(body)
    path.chmod(0o600)
    expected = predict_candidate(c, fitted, samples[:12])

    def forbidden(*a: Any, **k: Any) -> None:
        raise AssertionError("refit forbidden")

    monkeypatch.setattr("donghak_stock_vision.models.experiment.fit_candidate", forbidden)
    restored = verified_estimator(
        path, hashlib.sha256(body).hexdigest(), family, provenance()["versions"]
    )
    assert predict_candidate(c, restored, samples[:12]) == expected
    with pytest.raises(ValueError, match="pin_mismatch"):
        verified_estimator(path, "0" * 64, family, provenance()["versions"])
    path.write_bytes(b"\x80\x05cos\nsystem\n.")
    bad = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="constructor_forbidden"):
        verified_estimator(path, bad, family, provenance()["versions"])


def test_assessment_recovery_and_rehashed_tampering(tmp_path: Path) -> None:
    root = tmp_path / "review"
    pins = tmp_path / "pins"
    root.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    plan = dict(pins_root=str(pins))
    result = dict(version="fixture", new_training=0)
    key = digest(result)
    retain_assessment(root, plan, key, result)
    retain_assessment(root, plan, key, result)
    (root / "assessment.json").unlink()
    retain_assessment(root, plan, key, result)
    (root / "assessment.json").write_text('{"version":"tampered"}')
    with pytest.raises(ValueError, match="tampered"):
        retain_assessment(root, plan, key, result)


def test_labeled_readout_starts_after_context() -> None:
    from tests.learning.helpers import CUTOFF, bars

    from donghak_stock_vision.data.snapshot import capture
    from donghak_stock_vision.models.research_v2 import build
    from donghak_stock_vision.storage.real_market_dataset import _Panel

    source = bars(350, tickers=1)
    ticker = source[0].ticker
    snap = capture(
        _Panel({ticker: source}),
        [ticker],
        source[0].trading_date,
        source[-1].trading_date,
        CUTOFF,
        "historical_research",
        acknowledge=True,
        synthetic=True,
    )
    old = build(snap, 5)
    assert old == build(snap, 5, label_start=None)
    start = source[250].trading_date
    new = build(snap, 5, label_start=start)
    assert new[0] and all(s.trading_date >= start for s in new[0])
    old_rows = {digest(s.to_dict()): s for s in old[0]}
    assert all(digest(s.to_dict()) in old_rows for s in new[0])


def test_source_integrity_context_only_and_no_missing_data_fill(tmp_path: Path) -> None:
    import json

    from tests.integration.test_real_market_training import source

    from donghak_stock_vision.data.learning import FEATURES
    from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
    from donghak_stock_vision.storage.selection_assessment_source import prepare

    src = source(tmp_path / "archive", 220, universe_size=3, first_day=date(2026, 1, 2))
    protocol = dict(
        split=dict(
            version=1,
            training=dict(start="2026-01-02", end="2026-03-01"),
            validation=dict(start="2026-03-02", end="2026-03-31"),
            final_test=dict(start="2026-04-01", end="2026-04-30"),
        ),
        research_calendar=dict(
            version=1,
            reason="known_incomplete_trading_date",
            action="exclude_research_session",
            incomplete_dates=[],
        ),
        features=list(FEATURES),
        feature_version="ohlcv_value_v1",
        label_version="reversal_barrier_v1",
        horizon_sessions=5,
    )
    pp = tmp_path / "protocol.json"
    pp.write_text(json.dumps(protocol))
    pp.chmod(0o600)
    op = tmp_path / "operational"
    OperationalMarketStore.initialize(op)
    d: dict[str, Any] = dict(
        research=dict(
            database=str(src.database),
            pin=hashlib.sha256(src.database.read_bytes()).hexdigest(),
            source_contract="receipt_archive_v1",
        ),
        protocol=dict(path=str(pp), sha256=hashlib.sha256(pp.read_bytes()).hexdigest()),
        operational=dict(
            root=str(op), pin=hashlib.sha256((op / DB).read_bytes()).hexdigest(), evidence=[]
        ),
        context_window=dict(start="2026-08-01", end="2026-09-30"),
        evaluation_window=dict(start="2026-10-01", end="2026-10-30"),
        cutoff=src.cutoff.isoformat(),
    )
    before = src.database.read_bytes()
    result = prepare(d, list(src.tickers))
    assert result["source_rows"] > 0 and not result["q3_labels_generated"]
    assert all(not g["samples"] for g in result["goals"].values())
    assert src.database.read_bytes() == before
    altered = copy.deepcopy(d)
    altered["evaluation_window"]["start"] = "2026-09-30"
    with pytest.raises(ValueError, match="q3_or_final_test"):
        prepare(altered, list(src.tickers))
    altered = copy.deepcopy(d)
    altered["research"]["pin"] = "0" * 64
    with pytest.raises(ValueError, match="pin_mismatch"):
        prepare(altered, list(src.tickers))
