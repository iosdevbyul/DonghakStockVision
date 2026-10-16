"""Genuine HGB scoring on synthetic fixtures: mechanics, never performance evidence."""

import hashlib
import json
import pickle
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.hgb_provider import HGBHistoricalProvider
from donghak_stock_vision.backtest.ledger import initialize
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.service import freeze_request, run_backtest_with_report
from donghak_stock_vision.backtest.trading_report import report_backtest
from donghak_stock_vision.data.learning import (
    FEATURES,
    AnalysisError,
    canonical,
    digest,
    event_contract,
)
from donghak_stock_vision.models.artifact_resolver import TrustedHGBResolver
from donghak_stock_vision.models.inference import ResearchPolicy
from donghak_stock_vision.signals.domain import SignalPolicy
from donghak_stock_vision.storage.sqlite import SQLiteStore
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.engine import decide
from tests.backtest.test_ledger import command
from tests.backtest.test_performance import valuation_policy
from tests.backtest.test_public_inference import scored_runner
from tests.learning.helpers import CUTOFF
from tests.learning.test_signal_integration import bundle


def genuine_resolver(root: Path) -> TrustedHGBResolver:
    """Fixed training seed/configuration, independent of the trading window/outcomes."""
    np = pytest.importorskip("numpy")
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits

    template = bundle(root)
    plan = json.loads((root / "plan.json").read_text())
    lock = json.loads((root / "lock.json").read_text())
    qual = json.loads((root / "qualification.json").read_text())
    for obj in (plan, lock):
        obj.update(event_contract("verified_sessions"))
    rng = np.random.default_rng(42)
    x = rng.normal(size=(640, len(FEATURES)))
    lock["training_snapshot_id"] = digest({"x": x.tolist(), "labels": (x[:, 0] > 0).tolist()})
    models = []
    for artifact in template.artifacts:
        estimator = HistGradientBoostingClassifier(**json.loads(artifact.parameters_json))
        with threadpool_limits(limits=1):
            estimator.fit(x, (x[:, 0] > 0).astype(int))
        raw = pickle.dumps(estimator)
        (root / f"{artifact.direction}-research-only.pkl").write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        lock["directions"][artifact.direction]["estimator_hash"] = sha
        models.append(replace(artifact, checkpoint_sha256=sha))
    lock.pop("lock_id")
    lock["plan_id"] = digest(plan)
    lid = digest(lock)
    qual.pop("report_id")
    qual["lock_id"] = lid
    qid = digest(qual)
    models = [
        replace(
            a,
            experiment_lock_id=lid,
            qualification_id=qid,
            source_snapshot_id=lock["training_snapshot_id"],
        )
        for a in models
    ]
    for name, obj in (
        ("plan", plan),
        ("lock", {**lock, "lock_id": lid}),
        ("qualification", {**qual, "report_id": qid}),
    ):
        (root / f"{name}.json").write_text(canonical(obj))
    return TrustedHGBResolver(
        root, *models, up_pin=models[0].fingerprint, down_pin=models[1].fingerprint
    )


def hgb_runner(path: Path) -> BacktestRunner:
    base = scored_runner(path)
    market = SQLiteStore(path / "market.db")
    resolver = genuine_resolver(path / "trusted")
    flags = base.analyses[0].bundle.to_dict()["signal"]["quality_flags"]
    provider = HGBHistoricalProvider(
        resolver,
        SignalPolicy("synthetic-fixed", ResearchPolicy("synthetic-fixed", frozenset(flags))),
        CUTOFF,
    )
    steps = base.config.to_dict()["decision_steps"]
    bundles = [
        provider.capture(
            market,
            ticker="005930",
            as_of=datetime.fromisoformat(step["market"]["received_at"]),
            history_start=datetime.fromisoformat(
                base.analyses[0].bundle.to_dict()["snapshot"]["start"]
            ).date(),
            source_cutoff=CUTOFF,
            quality=base.tape.quality.to_dict(),
            calendar=None,
            synthetic=True,
        )
        for step in steps
    ]
    market.close()
    m = base.tape.manifest.to_dict()

    def ref(identity: str) -> dict[str, Any]:
        return {**m["models"][0], "artifact_id": identity, "content_hash": identity}

    plan = base.tape.release_plan.to_dict()
    plan["analysis_releases"] = [
        {
            **r,
            "analysis_id": b["analysis_id"],
            "snapshot_id": b["snapshot_id"],
            "model_id": b["model_id"],
        }
        for r, b in zip(plan["analysis_releases"], bundles, strict=True)
    ]
    frozen_plan = FrozenJSON.freeze(plan)
    ep = base.tape.policy.to_dict()
    ep["availability_policy"]["content_hash"] = frozen_plan.identifier
    tape_policy = type(base.tape.policy).from_dict(ep)
    m.update(
        availability_assumption_id=frozen_plan.identifier,
        execution_policy_id=tape_policy.identifier,
        models=[ref(provider.model_id)],
        analyses=[ref(b["analysis_id"]) for b in bundles],
        market_snapshots=m["market_snapshots"] + [ref(b["snapshot_id"]) for b in bundles],
    )
    manifest = RunManifest.from_dict(m)
    events = []
    for event in base.tape.events:
        e = event.to_dict()
        e.update(run_id=manifest.identifier, availability_assumption_id=frozen_plan.identifier)
        events.append(MarketEvent.from_dict(e))
    tape = FrozenTape(
        manifest, tape_policy, tuple(events), base.tape.sources, frozen_plan, base.tape.quality
    )
    analyses = tuple(FrozenAnalysis(manifest, frozen_plan, FrozenJSON.freeze(b)) for b in bundles)
    for step, b in zip(steps, bundles, strict=True):
        step["analysis_id"] = b["analysis_id"]
    c = base.config.to_dict()
    c.update(run_id=manifest.identifier, decision_steps=steps)
    dp = base.decision_policy.to_dict()
    dp["allowed_models"] = [provider.model_id]
    initial = initialize(
        manifest,
        tape.identifier,
        base.initial.policy,
        command("initialize", 0, {}, effective=c["start_at"], known=c["start_at"]),
        cutoff=c["start_at"],
    )
    return replace(
        base,
        tape=tape,
        analyses=analyses,
        initial=initial,
        decision_policy=DecisionPolicy.from_dict(dp),
        config=FrozenJSON.freeze(c),
    )


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> BacktestRunner:
    return hgb_runner(tmp_path_factory.mktemp("genuine-hgb"))


def test_genuine_hgb_trades_history_performance_and_report(runner: BacktestRunner) -> None:
    result = report_backtest(runner, valuation_policy())
    d, p, report = result.run.to_dict(), result.performance.to_dict(), result.details.to_dict()
    assert [x["result"]["action"] for e in d["events"] for x in e["decisions"]] == [
        "BUY",
        "HOLD",
        "SELL",
        "BUY",
        "HOLD",
    ]
    assert len(d["final_account"]["fills"]) == 3
    assert d["position_history"]["complete"] is True
    assert p["completed_trades"] == 1 and p["fill_count"] == 3
    assert all(row["hgb_provenance"]["model_pair"]["up"] for row in report["timeline"])
    assert all(row["feature_contributions"] is None for row in report["timeline"])
    assert all(
        row["unavailable"]["feature_contributions"] == "hgb_attribution_not_provided"
        for row in report["timeline"]
    )
    request = freeze_request(runner, valuation_policy())
    replay = run_backtest_with_report(
        request,
        start=runner.config.to_dict()["start_at"],
        end=runner.config.to_dict()["end_at"],
        initial_cash="1000000",
        tickers=["005930"],
    )
    assert replay == result
    assert replay.details.identifier == result.details.identifier
    assert result.performance == report_backtest(runner, valuation_policy()).performance


def test_real_score_and_context_reach_existing_engine(runner: BacktestRunner) -> None:
    initial = runner.initial
    for step, analysis in zip(
        runner.config.to_dict()["decision_steps"], runner.analyses, strict=True
    ):
        clock = VirtualClock.at(step["market"]["received_at"], step["sequence"])
        inputs = runner.project(initial, step, analysis, clock)
        result = decide(inputs, runner.decision_policy).to_dict()
        signal = analysis.bundle.to_dict()["signal"]
        assert result["evidence"]["analysis"] == signal
        side = "up" if signal["context"] == "prior_decline" else "down"
        assert 0 <= signal[f"{side}_score"] <= 1
        assert signal[f"{'down' if side == 'up' else 'up'}_status"] == "context_mismatch"
        assert result["checks"]["analysis"] == "passed"
    dp = runner.decision_policy.to_dict()
    dp["thresholds"] = {"buy": "1", "sell": "1"}
    first = runner.config.to_dict()["decision_steps"][0]
    a = runner.analyses[0]
    inputs = runner.project(
        initial, first, a, VirtualClock.at(first["market"]["received_at"], first["sequence"])
    )
    assert decide(inputs, DecisionPolicy.from_dict(dp)).to_dict()["action"] == "WAIT"
    b = inputs.to_dict()
    b.pop("hgb_evidence")
    assert (
        decide(type(inputs).from_dict(b), runner.decision_policy).to_dict()["status"] == "blocked"
    )


@pytest.mark.parametrize(
    "field", ["up_score", "snapshot_id", "feature_hash", "context", "model_version"]
)
def test_frozen_tampering_fails_closed(runner: BacktestRunner, field: str) -> None:
    original = runner.analyses[0]
    b = original.bundle.to_dict()
    b["signal"][field] = "tampered"
    with pytest.raises(ValueError):
        replace(original, bundle=FrozenJSON.freeze(b))


def test_public_trusted_loader_failures(tmp_path: Path) -> None:
    resolver = genuine_resolver(tmp_path / "trusted")
    for verified in resolver.verify():
        assert 0 <= verified.load().score(FEATURES, (0.01,) * 8) <= 1
    bad = replace(
        resolver.artifacts[0],
        runtime_versions=tuple((p, "0") for p, _ in resolver.artifacts[0].runtime_versions),
    )
    with pytest.raises(AnalysisError, match="runtime_mismatch"):
        from donghak_stock_vision.models.inference import HGBInference

        HGBInference(
            bad,
            (resolver.root / "up-research-only.pkl").read_bytes(),
            trusted_artifact_fingerprint=bad.fingerprint,
        )
    (resolver.root / "up-research-only.pkl").write_bytes(b"tampered")
    with pytest.raises(AnalysisError, match="checkpoint_mismatch"):
        resolver.verify()


def test_bounded_historical_provider_and_separate_output(tmp_path: Path) -> None:
    """Synthetic rows marked as a real-provider fixture exercise research routing only."""
    from donghak_stock_vision.backtest.historical import ASSUMPTION
    from donghak_stock_vision.backtest.historical_execution import MODE
    from donghak_stock_vision.backtest.historical_service import (
        HistoricalBacktestRequest,
        run_historical_backtest,
    )
    from donghak_stock_vision.providers.base import RawPage
    from donghak_stock_vision.storage.analysis import AnalysisStore
    from tests.learning.helpers import bars, quality

    base = scored_runner(tmp_path)
    resolver = genuine_resolver(tmp_path / "trusted")
    rows = [replace(b, ticker="005930", provider="fixture_daily") for b in bars(tickers=1)[80:110]]
    market = SQLiteStore(tmp_path / "historical.db")
    for bar in rows:
        rid = market.archive(
            "fixture",
            bar.provider,
            bar.ticker,
            RawPage(b"fixture", bar.collected_at, bar.trading_date),
        )
        market.save([bar], [rid])
    q = quality(rows)
    provider = HGBHistoricalProvider(
        resolver,
        SignalPolicy(
            "fixture",
            ResearchPolicy(
                "fixture", frozenset(base.decision_policy.to_dict()["allowed_quality_flags"])
            ),
        ),
        CUTOFF,
    )
    m = json.loads(
        canonical(base.tape.manifest.to_dict())
        .replace('"synthetic"', '"real"')
        .replace('"synthetic_test_only"', '"research_only"')
    )
    c = {
        "manifest": m,
        "policy": base.tape.policy.to_dict(),
        "captured_at": CUTOFF.isoformat(),
        "quality": q,
        "availability_assumption": ASSUMPTION,
        "decision_policy": {
            **base.decision_policy.to_dict(),
            "allowed_models": [provider.model_id],
        },
        "execution_policy": {**base.execution_policy.to_dict(), "liquidity": MODE},
        "ledger_policy": base.initial.policy.document.to_dict(),
        "valuation_policy": valuation_policy(historical_marks="verified_daily_research").to_dict(),
        "admission_policy": base.config.to_dict()["decision_steps"][0]["admission_policy"],
        "reservation_by_side": base.config.to_dict()["decision_steps"][0]["reservation"],
        "decision_dates": [rows[i].trading_date.isoformat() for i in (20, 22, 23, 27, 29)],
        "decision_mark_field": "close",
    }
    request = HistoricalBacktestRequest(
        rows[0].trading_date,
        rows[-1].trading_date,
        "1000000",
        ("005930",),
        provider.model_id,
        FrozenJSON.freeze(c),
    )
    output = AnalysisStore(tmp_path / "hgb-output.db")
    before = (tmp_path / "historical.db").read_bytes()
    result = run_historical_backtest(
        request, market, None, run_output=output, hgb_provider=provider
    )
    assert (tmp_path / "historical.db").read_bytes() == before
    d = result.document.to_dict()
    assert len(d["historical_input"]["analyses"]) == 5
    assert len(output.all("hgb_analysis")) == 5
    assert [x["result"]["action"] for e in d["run"]["events"] for x in e["decisions"]] == [
        "BUY",
        "HOLD",
        "SELL",
        "BUY",
        "BUY",
    ]
    # Caller-fixed reservation ceilings correctly block the differently priced
    # second entry. Preserve those rejections; never infer an unconfirmed fill.
    assert d["performance"]["fill_count"] == 2
    rejected = [a for e in d["run"]["events"] for a in e["admissions"] if a["status"] != "accepted"]
    assert len(rejected) == 2
    assert all(a["reason"] == "admission_budget_exceeded" for a in rejected)
    assert (
        run_historical_backtest(request, market, None, run_output=output, hgb_provider=provider)
        == result
    )
    same_file = AnalysisStore(tmp_path / "historical.db")
    try:
        with pytest.raises(AnalysisError, match="run_output_must_be_separate"):
            run_historical_backtest(
                request, market, None, run_output=same_file, hgb_provider=provider
            )
    finally:
        same_file.close()
    output.close()
    market.close()


def test_public_contract_failures_without_score_substitution(tmp_path: Path) -> None:
    from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
    from donghak_stock_vision.signals.integration import ResearchSignalRequest, resolve_snapshot
    from tests.learning.helpers import bars, quality, seed

    resolver = genuine_resolver(tmp_path / "trusted")
    market = SQLiteStore(tmp_path / "negative.db")
    source = bars(30, tickers=1)
    seed(market, source)
    q = quality(source)
    request = ResearchSignalRequest(
        "000001",
        datetime.combine(source[24].trading_date, datetime.min.time(), CUTOFF.tzinfo),
        source[0].trading_date,
        CUTOFF,
        True,
        True,
        None,
        canonical(q),
    )
    flags = resolve_snapshot(market, request).context.quality_flags
    provider = HGBHistoricalProvider(
        resolver, SignalPolicy("fixture", ResearchPolicy("fixture", frozenset(flags))), CUTOFF
    )
    kwargs: dict[str, Any] = dict(
        ticker="000001",
        as_of=request.as_of,
        history_start=source[0].trading_date,
        source_cutoff=CUTOFF,
        quality=q,
        calendar=None,
        synthetic=True,
    )
    # UTC midnight is Seoul 09:00: anchor is the previous date, not today's bar.
    good = provider.capture(market, **kwargs)
    assert good["signal"]["last_trading_date"] < request.as_of.date().isoformat()
    strict = replace(
        provider, signal_policy=SignalPolicy("deny", ResearchPolicy("deny", frozenset()))
    )
    denied = strict.capture(market, **kwargs)
    assert denied["signal"]["input_status"] == "quality_blocked"
    assert denied["signal"]["up_score"] is None and denied["signal"]["down_score"] is None
    no_quality = provider.capture(market, **{**kwargs, "quality": None})
    assert no_quality["signal"]["input_status"] == "quality_unverified"
    with pytest.raises(AnalysisError, match="research_calendar_mismatch"):
        provider.capture(market, **{**kwargs, "calendar": ResearchCalendarPolicy(())})
    with pytest.raises(AnalysisError):
        provider.capture(market, **{**kwargs, "source_cutoff": request.as_of})
    with pytest.raises(AnalysisError, match="untrusted_artifact"):
        TrustedHGBResolver(
            resolver.root, *resolver.artifacts, up_pin="0" * 64, down_pin=resolver.pins[1]
        )
    with pytest.raises(AnalysisError, match="incompatible_artifact"):
        replace(resolver.artifacts[0], feature_version="incompatible")
    # The public research service permits a different input quality/session basis.
    # Trading adaptation must not silently change the locked label semantics.
    import shutil

    other_root = tmp_path / "other-session-basis"
    shutil.copytree(resolver.root, other_root)
    plan = json.loads((other_root / "plan.json").read_text())
    lock = json.loads((other_root / "lock.json").read_text())
    qual = json.loads((other_root / "qualification.json").read_text())
    for record in (plan, lock):
        record.update(event_contract("observed_bars_unverified"))
    lock.pop("lock_id")
    lock["plan_id"] = digest(plan)
    lid = digest(lock)
    qual.pop("report_id")
    qual["lock_id"] = lid
    qid = digest(qual)
    for name, record in (
        ("plan", plan),
        ("lock", {**lock, "lock_id": lid}),
        ("qualification", {**qual, "report_id": qid}),
    ):
        (other_root / f"{name}.json").write_text(canonical(record))
    manifests = [
        replace(a, experiment_lock_id=lid, qualification_id=qid) for a in resolver.artifacts
    ]
    other = TrustedHGBResolver(
        other_root, *manifests, up_pin=manifests[0].fingerprint, down_pin=manifests[1].fingerprint
    )
    with pytest.raises(ValueError, match="hgb_session_basis_mismatch"):
        replace(provider, resolver=other).capture(market, **kwargs)
    (resolver.root / "down-research-only.pkl").unlink()
    with pytest.raises(AnalysisError, match="missing_artifact"):
        provider.capture(market, **kwargs)
    market.close()


@pytest.mark.parametrize("key", ["snapshot", "candidate", "models", "experiment_provenance"])
def test_missing_source_evidence(runner: BacktestRunner, key: str) -> None:
    original = runner.analyses[0]
    b = original.bundle.to_dict()
    b["hgb_evidence"].pop(key)
    with pytest.raises((ValueError, KeyError)):
        replace(original, bundle=FrozenJSON.freeze(b))
