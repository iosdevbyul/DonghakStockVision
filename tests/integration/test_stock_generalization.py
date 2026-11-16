"""Real source validators/fitters over offline KRX schema, never market performance."""

import copy
import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_gemma_research import healthy
from tests.integration.test_real_market_training import source

from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.models.autonomous import execute
from donghak_stock_vision.models.stock_generalization import catalog, selection
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, prepare, worker
from donghak_stock_vision.storage.autonomous_runner import run
from donghak_stock_vision.storage.stock_research import compare, fork_policy, report
from donghak_stock_vision.storage.stock_universe import inspect


def test_source_stock_split_cache_and_policy_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = source(tmp_path / "original", 800, universe_size=10)
    protocol = dict(
        split=src.access_split.to_dict(),
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
        version="autonomous_multi_stock_source_v1",
        dataset_version="offline-multi-stock",
        research=dict(
            database=str(src.database), pin=src.database_pin, source_contract="receipt_archive_v1"
        ),
        protocol=dict(path=str(pp), sha256=hashlib.sha256(pp.read_bytes()).hexdigest()),
        tickers=list(src.tickers),
        cutoff=src.cutoff.isoformat(),
        horizons=[1],
    )
    inventory = inspect(d)
    assert inventory["full_metadata"]["tickers"] == 10
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps(inventory))
    d["multi_stock"] = dict(
        heldout_tickers=list(src.tickers[-2:]),
        budget=12,
        policy="adaptive",
        inventory=dict(path=str(inv), pin=digest(inventory)),
        cohort="fixture",
    )
    before = src.database.read_bytes()
    root, pins = tmp_path / "adaptive", tmp_path / "pins"
    pin = prepare(d, root, pins).to_dict()["plan_id"]
    plan, data = context(root, pin)
    assert plan["version"] == "autonomous_plan_v5" and len(plan["configurations"]) == 154
    assert all(
        c["config"]["candidate"]["parameters"].get("solver") != "liblinear"
        for c in plan["configurations"]
    )
    assert data["goals"]["1"]["exclusions_by_ticker"]
    held = set(d["multi_stock"]["heldout_tickers"])
    chosen = next(
        c["config"]
        for c in plan["configurations"]
        if c["config"]["stock_policy"] == "heldout" and c["config"]["mask"] == "v2_multi"
    )
    result, _ = execute(data, chosen)
    assert not set(result["generalization_training"]["training_tickers"]) & held
    assert all(f["evaluation"]["stock_generalization"]["unseen_at_fit"] for f in result["folds"])
    assert all(not set(f["evaluation"]["by_ticker"]) & held for f in result["folds"])
    altered = copy.deepcopy(data)
    for sample in altered["goals"]["1"]["samples"]:
        if sample["ticker"] in held:
            sample["label"] = 1 - int(sample["label"])
    rebuilt, _ = execute(altered, chosen)
    assert (
        result["model_sha256"] == rebuilt["model_sha256"]
        and result["mean_ap"] == rebuilt["mean_ap"]
    )
    bad = copy.deepcopy(chosen)
    bad["candidate"]["parameters"] = dict(solver="liblinear", class_weight=None)
    with pytest.raises(ValueError, match="quarantined"):
        execute(data, bad)
    sibling = tmp_path / "predeclared"
    sibling_pins = tmp_path / "predeclared-pins"
    peer = fork_policy(root, pin, sibling, sibling_pins)
    assert context(sibling, peer)[1] == data
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0, cpu_percent=0),
    )

    def launch(root: Path, pin: str, attempt: str, **kwargs: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr("donghak_stock_vision.storage.autonomous_runner.launch", launch)
    summary = run(sibling, peer, 1, resource_probe=healthy).to_dict()
    assert summary["completed"] == 1 and not summary["failed"]
    assert not report(sibling, peer)["locked"]
    # Reference scores of held-out groups cannot alter ranking.
    with closing(ReadOnlyAnalysisStore(sibling / DB)) as store:
        from donghak_stock_vision.storage.autonomous_research import terminals

        done = terminals(store)
    initial = selection(context(sibling, peer)[0], done)
    poisoned = copy.deepcopy(done)
    for v in poisoned.values():
        v["unseen_metric"] = 999999
    assert selection(context(sibling, peer)[0], poisoned) == initial
    with pytest.raises(ValueError):
        compare(report(root, pin), report(sibling, peer))
    from donghak_stock_vision.storage.gemma_memory import adopt

    key = next(iter(done))
    candidate = next(c for c in plan["configurations"] if c["experiment_id"] == key)
    assert adopt(root, plan, sibling, peer, candidate)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        assert terminals(store)[key]["cache_source"]["plan"] == peer
    assert src.database.read_bytes() == before


def test_new_catalog_quarantine_preserves_old_profiles() -> None:
    from donghak_stock_vision.models.research_v2 import catalog as old_catalog

    assert len(catalog([1, 5, 20])) == 462
    assert len(old_catalog([1, 5, 20])) == 294
    assert any(c["candidate"]["parameters"].get("solver") == "liblinear" for c in old_catalog([1]))
    projected = []
    for c in catalog([1, 5, 20]):
        row = copy.deepcopy(c)
        row["candidate"].pop("name")
        projected.append(digest(row))
    assert len(set(projected)) == len(projected)


def test_pooled_model_cannot_tune_heldout_model() -> None:
    configs: list[dict[str, Any]] = [dict(experiment_id=digest(c), config=c) for c in catalog([1])]
    plan = dict(configurations=configs, experiment_budget=12, selection_policy="adaptive")
    pooled = next(c for c in configs if c["config"]["stock_policy"] == "pooled")
    done: dict[str, dict[str, Any]] = {
        pooled["experiment_id"]: dict(
            config=pooled["config"],
            state="completed",
            stability_ap=0.5,
            mean_ap=0.5,
            largest_train_gap=0.1,
        )
    }
    original = selection(plan, done)
    assert original is not None and original["config"]["stock_policy"] == "heldout"
    changed = copy.deepcopy(done)
    for row in changed.values():
        row.update(stability_ap=999, mean_ap=999, largest_train_gap=999)
    assert selection(plan, changed) == original
