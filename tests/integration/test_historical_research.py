"""Disjoint original-source rehearsal. Fixture prices are not market performance."""

import copy
import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_gemma_research import healthy
from tests.integration.test_real_market_training import source

from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.models.autonomous import execute
from donghak_stock_vision.models.historical_research import selection
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, prepare, terminals, worker
from donghak_stock_vision.storage.autonomous_runner import run
from donghak_stock_vision.storage.stock_research import compare, fork_policy, report
from donghak_stock_vision.storage.stock_universe import inspect


def test_disjoint_sources_periods_leak_guards_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    src = source(tmp_path / "source", 1280, universe_size=10)
    with closing(sqlite3.connect(src.database)) as db:
        days = [
            r[0] for r in db.execute("SELECT DISTINCT trading_date FROM bars ORDER BY trading_date")
        ][:1280]
        db.execute(
            "UPDATE bars SET payload='protected invalid payload' "
            "WHERE trading_date BETWEEN ? AND ?",
            (days[450], days[569]),
        )
        db.commit()
    before = src.database.read_bytes()

    def span(a: int, b: int) -> dict[str, str]:
        return dict(start=days[a], end=days[b])

    protocol = dict(
        split=dict(
            version=1, training=span(0, 449), validation=span(450, 509), final_test=span(510, 569)
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
    d: dict[str, Any] = dict(
        version="autonomous_historical_source_v1",
        dataset_version="offline-historical",
        research=dict(
            database=str(src.database),
            pin=hashlib.sha256(before).hexdigest(),
            source_contract="receipt_archive_v1",
        ),
        protocol=dict(path=str(pp), sha256=hashlib.sha256(pp.read_bytes()).hexdigest()),
        tickers=list(src.tickers),
        cutoff=src.cutoff.isoformat(),
        horizons=[1, 20],
        historical=dict(
            windows=[span(0, 449), span(570, 1279)],
            search_end=days[1079],
            evaluations=[span(780, 879), span(880, 979), span(980, 1079)],
            diagnostic=span(1080, 1279),
            policy_version="historical_robust_lexicographic_v1",
            references=[],
        ),
    )
    inv = inspect(d)
    ip = tmp_path / "inventory.json"
    ip.write_text(json.dumps(inv))
    d["multi_stock"] = dict(
        heldout_tickers=list(src.tickers[-2:]),
        budget=12,
        policy="adaptive",
        inventory=dict(path=str(ip), pin=digest(inv)),
        cohort="fixture",
    )
    root, pins = tmp_path / "adaptive", tmp_path / "pins"
    pin = prepare(d, root, pins).to_dict()["plan_id"]
    plan, data = context(root, pin)
    assert plan["version"] == "autonomous_plan_v6" and data["version"] == "direction_dataset_v4"
    assert data["trading_dates"] == 1160
    for g in data["goals"].values():
        assert all(not days[450] <= s["trading_date"] <= days[569] for s in g["samples"])
    cfg = next(
        c["config"]
        for c in plan["configurations"]
        if c["config"]["goal"]["horizon"] == 20 and c["config"]["stock_policy"] == "heldout"
    )
    result, _ = execute(data, cfg)
    assert (
        len(result["folds"]) == 3
        and result["deferred_diagnostic"]["heldout_symbols"]["counts"]["rows"] > 0
    )
    changed = copy.deepcopy(data)
    for s in changed["goals"]["20"]["samples"]:
        if s["ticker"] in d["multi_stock"]["heldout_tickers"] or s["trading_date"] >= days[1080]:
            s["label"] = 1 - int(s["label"])
    rebuilt, _ = execute(changed, cfg)
    assert (
        result["model_sha256"] == rebuilt["model_sha256"]
        and result["robust_summary"] == rebuilt["robust_summary"]
    )
    assert result["mean_ap"] == rebuilt["mean_ap"]
    for fold in data["goals"]["20"]["folds"]:
        assert date.fromisoformat(fold["training"]["end"]) < date.fromisoformat(
            fold["evaluation"]["start"]
        )
    bad = copy.deepcopy(d)
    bad["historical"]["windows"][1]["start"] = days[550]
    with pytest.raises(ValueError, match="original_roles"):
        prepare(bad, tmp_path / "bad", tmp_path / "badpins")
    reused = copy.deepcopy(d)
    reused["dataset_version"] = "offline-reused-periods"
    reused["historical"].update(
        source_parent=dict(root=str(root), pin=pin), include_original_internal_folds=True
    )
    rp = prepare(reused, tmp_path / "reused", tmp_path / "reused-pins").to_dict()["plan_id"]
    _, rd = context(tmp_path / "reused", rp)
    assert len(rd["goals"]["20"]["folds"]) == 4
    assert rd["observation_count"] == data["observation_count"]
    invalid = copy.deepcopy(reused)
    invalid["historical"]["source_parent"]["pin"] = "0" * 64
    with pytest.raises(ValueError):
        prepare(invalid, tmp_path / "bad-reuse", tmp_path / "bad-reuse-pins")
    child, cp = tmp_path / "robust", tmp_path / "robust-pins"
    peer = fork_policy(root, pin, child, cp, policy="robust")
    assert context(child, peer)[1] == data and not report(child, peer)["locked"]
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0, cpu_percent=0),
    )

    def launch(root: Path, pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.launch", launch)
    one = run(child, peer, 1, resource_probe=healthy).to_dict()
    two = run(child, peer, 1, resource_probe=healthy).to_dict()
    assert one["completed"] == 1 and two["completed"] == 2
    with closing(ReadOnlyAnalysisStore(child / DB)) as store:
        done = terminals(store)
    before_choice = selection(context(child, peer)[0], done)
    altered = copy.deepcopy(done)
    for v in altered.values():
        v["deferred_diagnostic"] = {"arbitrary": 999}
        v["heldout_score"] = 999
    assert selection(context(child, peer)[0], altered) == before_choice
    with pytest.raises(ValueError):
        compare(report(root, pin), report(child, peer))
    assert src.database.read_bytes() == before


def test_robust_choice_uses_comparable_memory_not_foreign_scores() -> None:
    from donghak_stock_vision.models.historical_research import PRIORITIES
    from donghak_stock_vision.models.stock_generalization import catalog

    configurations: list[dict[str, Any]] = [
        dict(experiment_id=digest(c), config=c) for c in catalog([1])
    ]
    plan = dict(
        configurations=configurations,
        experiment_budget=12,
        selection_policy="robust",
        transfer_hypotheses=[],
        robust_priorities=PRIORITIES,
    )

    def evidence(score: float) -> dict[str, Any]:
        return dict(
            version="historical_robust_lexicographic_v1",
            sufficient_folds=2,
            passes=1,
            worst_balanced_accuracy=score,
            worst_baseline_ap_gain=0.01,
            negative_dispersion=-0.01,
            worst_regime_balanced_accuracy=0.5,
            negative_overfit_gap=-0.1,
            heldout_or_diagnostic_used=False,
        )

    done: dict[str, dict[str, Any]] = {}
    for c in configurations[:4]:
        done[c["experiment_id"]] = dict(
            config=c["config"],
            state="completed",
            mean_ap=0.5,
            stability_ap=0.5,
            largest_train_gap=0.1,
            robust_summary=evidence(0.8 if c["config"]["mask"] == "v2_legacy" else 0.4),
        )
    original = selection(plan, done)
    assert original is not None and original["config"]["mask"] == "v2_legacy"
    changed = copy.deepcopy(done)
    for v in changed.values():
        if v["config"]["stock_policy"] == "pooled":
            v["robust_summary"] = evidence(0.4 if v["config"]["mask"] == "v2_legacy" else 0.8)
    altered = selection(plan, changed)
    assert altered is not None and altered["config"]["mask"] == "v2_multi"
    poisoned = copy.deepcopy(done)
    for v in poisoned.values():
        if v["config"]["stock_policy"] == "heldout":
            v["robust_summary"] = evidence(999)
        v["deferred_diagnostic"] = {"score": 999}
        v["foreign_population_score"] = 999
    assert selection(plan, poisoned) == original
