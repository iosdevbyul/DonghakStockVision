"""Integration fixtures never fit models or predict production/OOS data."""

import hashlib
import json
import pickle
from dataclasses import replace
from datetime import datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.data.learning import (
    FEATURES,
    AnalysisError,
    canonical,
    digest,
    event_contract,
)
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import HGBArtifact, ResearchPolicy
from donghak_stock_vision.signals.domain import SignalPolicy
from donghak_stock_vision.signals.integration import (
    HGBResearchSignalService,
    ResearchSignalRequest,
    resolve_snapshot,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.learning.helpers import CUTOFF, bars, seed
from tests.learning.test_inference import StubEstimator


def bundle(root: Path, *, snapshot: str = "training-snapshot") -> TrustedHGBResolver:
    root.mkdir()
    versions = tuple(sorted((p, version(p)) for p in ("scikit-learn", "numpy", "scipy")))
    common = {
        "features": list(FEATURES),
        **event_contract("observed_bars_unverified"),
        "seed": 42,
        "research_calendar": None,
    }
    params = {d: StubEstimator(n).get_params() for d, n in (("up", 7), ("down", 15))}
    plan = {
        **common,
        "hgb": {
            k: params["up"][k]
            for k in (
                "learning_rate",
                "max_iter",
                "min_samples_leaf",
                "l2_regularization",
                "max_bins",
                "early_stopping",
                "random_state",
                "class_weight",
            )
        },
        "provenance": {"versions": dict(versions)},
    }
    directions: dict[str, Any] = {}
    for d, n in (("up", 7), ("down", 15)):
        blob = pickle.dumps(StubEstimator(n))
        (root / f"{d}-research-only.pkl").write_bytes(blob)
        directions[d] = {
            "candidate": {
                "name": f"hist_leaves{n}",
                "family": "hist_gradient_boosting",
                "value": n,
            },
            "estimator_hash": hashlib.sha256(blob).hexdigest(),
        }
    lock = {
        **common,
        "plan_id": digest(plan),
        "training_snapshot_id": snapshot,
        "directions": directions,
        "usage_restriction": "research_only",
        "operational_status": "unregistered",
    }
    lid = digest(lock)
    qual = {
        "lock_id": lid,
        "usage_restriction": "research_only",
        "operational_status": "unregistered",
        "directions": {
            d: {"candidate": directions[d]["candidate"], "qualification": "validation_gate_passed"}
            for d in directions
        },
    }
    qid = digest(qual)
    for filename, value in (
        ("plan", plan),
        ("lock", {**lock, "lock_id": lid}),
        ("qualification", {**qual, "report_id": qid}),
    ):
        (root / f"{filename}.json").write_text(canonical(value))
    artifacts = [
        HGBArtifact(
            d,
            directions[d]["estimator_hash"],
            lid,
            qid,
            snapshot,
            canonical(params[d]),
            versions,
            FEATURES,
            "ohlcv_value_v1",
            "reversal_barrier_v1",
        )
        for d in ("up", "down")
    ]
    return TrustedHGBResolver(
        root, *artifacts, up_pin=artifacts[0].fingerprint, down_pin=artifacts[1].fingerprint
    )


@pytest.fixture
def resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TrustedHGBResolver:
    instance = bundle(tmp_path / "trusted")
    monkeypatch.setattr("sklearn.ensemble.HistGradientBoostingClassifier", StubEstimator)
    return instance


@pytest.fixture
def signal_request(store: SQLiteStore) -> ResearchSignalRequest:
    source = bars(30, tickers=1)
    seed(store, source)
    anchor = source[24].trading_date
    return ResearchSignalRequest(
        "000001",
        datetime.combine(anchor + timedelta(days=1), time.min, SEOUL),
        source[0].trading_date,
        CUTOFF,
        True,
        True,
        None,
    )


def policy(store: SQLiteStore, signal_request: ResearchSignalRequest) -> SignalPolicy:
    flags = resolve_snapshot(store, signal_request).context.quality_flags
    return SignalPolicy("explicit-fixture", ResearchPolicy("fixture", frozenset(flags)))


def test_valid_deterministic(
    store: SQLiteStore, signal_request: ResearchSignalRequest, resolver: TrustedHGBResolver
) -> None:
    service = HGBResearchSignalService(store, resolver)
    p = policy(store, signal_request)
    first = service.research(signal_request, p)
    assert first.fingerprint == service.research(signal_request, p).fingerprint
    assert first.candidate.context.input_hash
    assert first.candidate.to_dict()["executable"] is False
    assert (
        first.to_dict()["models"]["up"]["checkpoint_sha256"]
        == resolver.artifacts[0].checkpoint_sha256
    )
    signals = (first.candidate.up, first.candidate.down)
    assert sum(s.raw_score is not None for s in signals) == 1
    assert {s.status for s in signals} == {"scored", "context_mismatch"}
    assert (
        first.fingerprint
        != service.research(signal_request, replace(p, version="new-policy")).fingerprint
    )


@pytest.mark.parametrize(
    "name", ["up-research-only.pkl", "lock.json", "qualification.json", "plan.json"]
)
def test_missing_artifact(resolver: TrustedHGBResolver, name: str) -> None:
    (resolver.root / name).unlink()
    with pytest.raises(AnalysisError, match="missing_artifact"):
        resolver.verify()


def test_checkpoint_mismatch(resolver: TrustedHGBResolver) -> None:
    (resolver.root / "up-research-only.pkl").write_bytes(b"bad")
    with pytest.raises(AnalysisError, match="checkpoint_mismatch"):
        resolver.verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"features": list(reversed(FEATURES))},
        {"feature_version": "bad"},
        {"training_snapshot_id": "other"},
    ],
)
def test_lock_tampering(resolver: TrustedHGBResolver, changes: dict[str, Any]) -> None:
    path = resolver.root / "lock.json"
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(canonical(data))
    with pytest.raises(AnalysisError, match="provenance_hash_mismatch"):
        resolver.verify()


def test_feature_contract(resolver: TrustedHGBResolver) -> None:
    with pytest.raises(AnalysisError, match="incompatible_artifact"):
        replace(resolver.artifacts[0], features=tuple(reversed(FEATURES)))
    with pytest.raises(AnalysisError, match="untrusted_artifact"):
        TrustedHGBResolver(resolver.root, *resolver.artifacts, up_pin="0" * 64, down_pin="0" * 64)


def test_cutoff_and_window(store: SQLiteStore, signal_request: ResearchSignalRequest) -> None:
    frozen = resolve_snapshot(store, signal_request)
    rows = json.loads(frozen.snapshot_json)["rows"][signal_request.ticker]
    assert len(rows) == 12
    assert max(r["trading_date"] for r in rows) == signal_request.anchor_date.isoformat()
    source = bars(30, tickers=1)
    future = source[-1]
    seed(
        store,
        [
            replace(
                future,
                volume=future.volume + 1,
                collected_at=future.collected_at + timedelta(hours=1),
            )
        ],
    )
    assert resolve_snapshot(store, signal_request) == frozen
    current = source[24]
    seed(
        store,
        [
            replace(
                current,
                volume=current.volume + 1,
                collected_at=current.collected_at + timedelta(hours=1),
            )
        ],
    )
    assert resolve_snapshot(store, signal_request).context.snapshot_id != frozen.context.snapshot_id


def test_short_history(store: SQLiteStore, signal_request: ResearchSignalRequest) -> None:
    with pytest.raises(AnalysisError, match="insufficient_history"):
        resolve_snapshot(store, replace(signal_request, history_start=bars(30, 1)[20].trading_date))


def test_missing_anchor(store: SQLiteStore, signal_request: ResearchSignalRequest) -> None:
    with pytest.raises(AnalysisError, match="missing_anchor_bar"):
        resolve_snapshot(store, replace(signal_request, ticker="999999"))
    with pytest.raises(AnalysisError, match="missing_anchor_bar"):
        resolve_snapshot(store, replace(signal_request, source_cutoff=signal_request.as_of))


def test_quality_blocked_no_unpickle(
    store: SQLiteStore,
    signal_request: ResearchSignalRequest,
    resolver: TrustedHGBResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("deserialization not allowed")

    monkeypatch.setattr(pickle, "loads", forbidden)
    result = HGBResearchSignalService(store, resolver).research(
        signal_request, SignalPolicy("strict", ResearchPolicy("strict", frozenset()))
    )
    assert result.candidate.up.status == result.candidate.down.status == "quality_blocked"


def test_no_trade_not_repaired(store: SQLiteStore, signal_request: ResearchSignalRequest) -> None:
    b = bars(30, 1)[24]
    seed(
        store,
        [
            replace(
                b,
                volume=0,
                trading_value=0,
                open=0,
                high=0,
                low=0,
                collected_at=b.collected_at + timedelta(hours=1),
            )
        ],
    )
    with pytest.raises(AnalysisError, match="no_trade_bar"):
        resolve_snapshot(store, signal_request)


def test_bad_repository_range(
    store: SQLiteStore, signal_request: ResearchSignalRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "read", lambda *args: bars(30, 1))
    with pytest.raises(AnalysisError, match="invalid_market_range"):
        resolve_snapshot(store, signal_request)


@pytest.mark.parametrize("trend", [-1, 0, 1])
def test_directional_population(
    store: SQLiteStore,
    signal_request: ResearchSignalRequest,
    resolver: TrustedHGBResolver,
    trend: int,
) -> None:
    source = bars(30, 1)
    changed = []
    for i, bar in enumerate(source):
        price = 10000 + trend * i * 20 + (i % 5) * 3
        changed.append(
            replace(
                bar,
                open=price,
                close=price,
                high=price + 10,
                low=price - 10,
                collected_at=bar.collected_at + timedelta(hours=1),
            )
        )
    seed(store, changed)
    candidate = (
        HGBResearchSignalService(store, resolver)
        .research(signal_request, policy(store, signal_request))
        .candidate
    )
    assert candidate.up.raw_score == (0.7 if trend < 0 else None)
    assert candidate.down.raw_score == (0.7 if trend > 0 else None)


def test_model_identity_changes(
    store: SQLiteStore,
    signal_request: ResearchSignalRequest,
    resolver: TrustedHGBResolver,
    tmp_path: Path,
) -> None:
    other = bundle(tmp_path / "other", snapshot="another-training-snapshot")
    p = policy(store, signal_request)
    first = HGBResearchSignalService(store, resolver).research(signal_request, p)
    second = HGBResearchSignalService(store, other).research(signal_request, p)
    assert first.candidate.up.raw_score == second.candidate.up.raw_score
    assert first.candidate.fingerprint != second.candidate.fingerprint


def test_calendar_and_anchor_exclusion(
    store: SQLiteStore,
    signal_request: ResearchSignalRequest,
    resolver: TrustedHGBResolver,
) -> None:
    from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy

    excluded = replace(
        signal_request, research_calendar=ResearchCalendarPolicy((signal_request.anchor_date,))
    )
    with pytest.raises(AnalysisError, match="known_incomplete_trading_date"):
        resolve_snapshot(store, excluded)
    different = replace(signal_request, research_calendar=ResearchCalendarPolicy(()))
    with pytest.raises(AnalysisError, match="research_calendar_mismatch"):
        HGBResearchSignalService(store, resolver).research(different, policy(store, different))


def test_missing_root_and_symlink(resolver: TrustedHGBResolver, tmp_path: Path) -> None:
    with pytest.raises(AnalysisError, match="missing_artifact_root"):
        TrustedHGBResolver(
            tmp_path / "missing",
            *resolver.artifacts,
            up_pin=resolver.pins[0],
            down_pin=resolver.pins[1],
        )
    path = resolver.root / "up-research-only.pkl"
    other = tmp_path / "other.pkl"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(AnalysisError, match="invalid_artifact_path"):
        resolver.verify()


def test_malformed_json(resolver: TrustedHGBResolver) -> None:
    (resolver.root / "lock.json").write_text("[")
    with pytest.raises(AnalysisError, match="malformed_artifact"):
        resolver.verify()


def test_gap_not_filled(
    store: SQLiteStore, signal_request: ResearchSignalRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = bars(30, 1)[:25]
    monkeypatch.setattr(store, "read", lambda *args: source[:15] + source[21:])
    with pytest.raises(AnalysisError, match="suspected_gap"):
        resolve_snapshot(store, signal_request)


def test_estimator_full_configuration_mismatch(resolver: TrustedHGBResolver) -> None:
    up, down = resolver.artifacts
    parameters = json.loads(up.parameters_json)
    parameters["max_depth"] = 3
    changed = replace(up, parameters_json=canonical(parameters))
    altered = TrustedHGBResolver(
        resolver.root, changed, down, up_pin=changed.fingerprint, down_pin=down.fingerprint
    )
    verified, _ = altered.verify()
    with pytest.raises(AnalysisError, match="invalid_checkpoint"):
        verified.load()
