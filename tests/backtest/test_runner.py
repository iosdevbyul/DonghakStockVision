"""Frozen synthetic orchestration; no backtest performance claims."""

from dataclasses import replace
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import checkpoint, initialize, restore
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from tests.backtest.test_decision_bridge import scenario
from tests.backtest.test_execution import FUTURE, setup


def runner(side: str = "BUY") -> BacktestRunner:
    c = setup(side)
    old = scenario() if side == "BUY" else scenario(5, "prior_rise")
    tape = c["tape"]
    analysis = FrozenAnalysis(tape.manifest, tape.release_plan, old["analysis"].bundle)
    step = {
        "sequence": 1,
        "ticker": "005930",
        "analysis_id": analysis.bundle.to_dict()["analysis_id"],
        "market": old["bundle"].inputs.to_dict()["market"],
        "admission_policy": old["policy"],
        "reservation": c["reservation"].to_dict(),
    }
    config = {
        "run_id": tape.manifest.identifier,
        "start_at": tape.manifest.to_dict()["start_at"],
        "end_at": tape.manifest.to_dict()["end_at"],
        "tickers": ["005930"],
        "event_clock": "available_at",
        "decision_steps": [step],
    }
    return BacktestRunner(
        tape, (analysis,), c["ledger"], old["bundle"].policy, c["policy"], FrozenJSON.freeze(config)
    )


def configure(r: BacktestRunner, **changes: Any) -> BacktestRunner:
    return replace(r, config=FrozenJSON.freeze({**r.config.to_dict(), **changes}))


def replan(r: BacktestRunner, plan: dict[str, Any]) -> BacktestRunner:
    release = FrozenJSON.freeze(plan)
    p = r.tape.policy.to_dict()
    p["availability_policy"]["content_hash"] = release.identifier
    policy = ExecutionPolicy.from_dict(p)
    m = r.tape.manifest.to_dict()
    m.update(availability_assumption_id=release.identifier, execution_policy_id=policy.identifier)
    manifest = RunManifest.from_dict(m)
    events = []
    for event in r.tape.events:
        e = event.to_dict()
        e.update(run_id=manifest.identifier, availability_assumption_id=release.identifier)
        events.append(MarketEvent.from_dict(e))
    tape = FrozenTape(manifest, policy, tuple(events), r.tape.sources, release, r.tape.quality)
    initial = initialize(
        manifest,
        tape.identifier,
        r.initial.policy,
        r.initial.events[0],
        cutoff=r.initial.to_dict()["known_at"],
    )
    return replace(
        configure(r, run_id=manifest.identifier),
        tape=tape,
        initial=initial,
        analyses=tuple(FrozenAnalysis(manifest, release, a.bundle) for a in r.analyses),
    )


@pytest.mark.parametrize("side,cash,quantity", [("BUY", "9089.18", 10), ("SELL", "10040.69", 0)])
def test_end_to_end(side: str, cash: str, quantity: int) -> None:
    r = runner(side)
    before = checkpoint(r.initial).identifier
    result = r.run()
    v = result.to_dict()
    assert v["status"] == "completed", v
    assert v["processed_event_count"] == 2
    first, second = v["events"]
    assert first["decisions"][0]["result"]["action"] == side
    assert first["candidates"][0]["virtual_order_eligible"]
    assert first["admissions"][0]["status"] == "accepted"
    assert first["executions"] == []
    assert second["executions"][0]["status"] == "filled"
    assert v["final_account"]["total_cash_krw"] == cash
    assert v["final_account"]["positions"]["005930"]["quantity"] == quantity
    assert len(v["final_account"]["fills"]) == 1
    assert not v["pending"]
    assert restore(FrozenJSON.freeze(v["final_checkpoint"])).to_dict() == v["final_account"]
    assert checkpoint(r.initial).identifier == before
    assert r.run().identifier == result.identifier


@pytest.mark.parametrize("side,expected", [("BUY", "WAIT"), ("SELL", "HOLD")])
def test_hold_wait_never_order(side: str, expected: str) -> None:
    r = runner(side)
    policy = r.decision_policy.to_dict()
    policy["thresholds"] = {"buy": "0.99", "sell": "0.99"}
    v = replace(r, decision_policy=DecisionPolicy.from_dict(policy)).run().to_dict()
    assert v["events"][0]["decisions"][0]["result"]["action"] == expected
    assert v["events"][0]["admissions"] == []
    assert v["final_account"] == r.initial.to_dict()


def test_period_filters_and_no_end_liquidation() -> None:
    r = runner()
    early = configure(r, end_at="2026-01-05T12:00:00Z").run().to_dict()
    assert early["processed_event_count"] == 1
    assert early["pending"]
    assert not early["final_account"]["fills"]
    late = configure(r, start_at=FUTURE).run().to_dict()
    assert late["processed_event_count"] == 1
    assert not late["events"][0]["decisions"]
    assert late["final_account"] == r.initial.to_dict()
    assert r.run().to_dict()["final_account"]["positions"]["005930"]["quantity"] == 10


def test_future_analysis_blocked_without_decision() -> None:
    r = runner()
    plan = r.tape.release_plan.to_dict()
    plan["analysis_releases"][0]["available_at"] = FUTURE
    r = replan(r, plan)
    v = r.run().to_dict()
    assert v["events"][0]["blocked"][0]["reason"] == "analysis_not_released"
    assert not v["events"][0]["decisions"]
    assert v["final_account"] == r.initial.to_dict()


def test_future_price_not_used_for_decision() -> None:
    r = runner()
    steps = r.config.to_dict()["decision_steps"]
    steps[0]["admission_policy"]["marks"]["005930"] = {"sequence": 2, "field": "open"}
    v = configure(r, decision_steps=steps).run().to_dict()
    assert v["events"][0]["reason"] == "valuation_not_public"
    assert v["final_account"] == r.initial.to_dict()


def test_rejected_execution_preserves_reserved_account() -> None:
    r = runner()
    steps = r.config.to_dict()["decision_steps"]
    steps[0]["reservation"] = {"max_notional_krw": "10", "max_cost_krw": "0"}
    v = configure(r, decision_steps=steps).run().to_dict()
    assert v["events"][1]["executions"][0]["reason"] == "reservation_shortfall"
    reserved = v["events"][0]["admissions"][0]["reservation_checkpoint"]["state"]
    assert v["final_account"] == reserved
    assert v["pending"] and not v["final_account"]["fills"]


def test_event_failure_rolls_back_successful_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    r = runner()
    original = BacktestRunner.process_event

    def fail_after(*args: Any, **kwargs: Any) -> Any:
        staged = original(*args, **kwargs)
        if staged[3]["admissions"]:
            raise RuntimeError("injected_after_reservation")
        return staged

    monkeypatch.setattr(BacktestRunner, "process_event", fail_after)
    v = r.run().to_dict()
    assert v["events"][0]["reason"] == "injected_after_reservation"
    assert v["final_account"] == r.initial.to_dict()
    assert not v["pending"]
    assert v["status"] == "completed_with_failures"


def test_event_failure_rolls_back_successful_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    r = runner()
    original = BacktestRunner.process_event

    def fail_after(*args: Any, **kwargs: Any) -> Any:
        staged = original(*args, **kwargs)
        if staged[3]["executions"]:
            raise RuntimeError("injected_after_fill")
        return staged

    monkeypatch.setattr(BacktestRunner, "process_event", fail_after)
    v = r.run().to_dict()
    assert v["events"][1]["reason"] == "injected_after_fill"
    assert v["final_account"] == v["events"][0]["admissions"][0]["reservation_checkpoint"]["state"]
    assert len(v["pending"]) == 1


@pytest.mark.parametrize("name", ["execution_policy", "decision_policy"])
def test_missing_policy(name: str) -> None:
    with pytest.raises(ValueError, match="policy_missing"):
        changes: dict[str, Any] = {name: None}
        replace(runner(), **changes).run()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"start_at": "2026-01-06T01:00:00Z", "end_at": FUTURE}, "start_after_end"),
        ({"tickers": []}, "universe_required"),
        ({"tickers": ["005930", "005930"]}, "duplicate_ticker"),
        ({"event_clock": "guess_next_day"}, "unsupported_event_clock"),
        ({"run_id": "bad"}, "run_id_mismatch"),
    ],
)
def test_invalid_settings(change: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        configure(runner(), **change).run()


def test_configuration_hash_changes_without_performance_metrics() -> None:
    r = runner()
    a = r.run()
    p = r.execution_policy.to_dict()
    p["buy_fee_rate"] = "0.001"
    b = replace(r, execution_policy=FrozenJSON.freeze(p)).run()
    assert a.identifier != b.identifier
    assert a.to_dict()["input_hash"] != b.to_dict()["input_hash"]
    assert not {"total_return", "mdd", "cagr", "sharpe", "win_rate"} & a.to_dict().keys()


def test_post_fill_entry_not_promoted_by_runner() -> None:
    r = runner()
    steps = r.config.to_dict()["decision_steps"]
    later = {**steps[0], "sequence": 2}
    # No new or fabricated analysis; old analysis must be rejected as stale.
    v = configure(r, decision_steps=[*steps, later]).run().to_dict()
    assert len(v["final_account"]["fills"]) == 1
    assert v["events"][1]["decisions"][0]["result"]["status"] == "blocked"
    assert v["events"][1]["admissions"] == []


def test_pending_order_prevents_duplicate_decision_order() -> None:
    from copy import deepcopy

    r = runner()
    p = r.decision_policy.to_dict()
    p["ttl_seconds"] = {key: 100000 for key in p["ttl_seconds"]}
    ep = r.execution_policy.to_dict()
    ep["slippage_rate"] = "0.99"  # Explicit fixture: future fill exceeds its reservation.
    steps = r.config.to_dict()["decision_steps"]
    later = deepcopy(steps[0])
    later["sequence"] = 2
    later["market"].update(
        observed_at=FUTURE, received_at=FUTURE, price_observed_at=FUTURE, reference_price_krw="90"
    )
    later["admission_policy"]["marks"]["005930"]["sequence"] = 2
    v = (
        replace(
            configure(r, decision_steps=[*steps, later]),
            decision_policy=DecisionPolicy.from_dict(p),
            execution_policy=FrozenJSON.freeze(ep),
        )
        .run()
        .to_dict()
    )
    assert v["events"][1]["executions"][0]["reason"] == "reservation_shortfall"
    assert v["events"][1]["decisions"][0]["result"]["blocking_reasons"] == ["pending_order"]
    assert not v["events"][1]["admissions"]
    assert len(v["final_account"]["orders"]) == 1


def rebuild(
    r: BacktestRunner, wire: dict[str, Any], bundles: list[dict[str, Any]]
) -> BacktestRunner:
    from donghak_stock_vision.data.learning import digest

    plan = FrozenJSON.freeze(wire["release_plan"])
    wire["policy"]["availability_policy"]["content_hash"] = plan.identifier
    selected = ExecutionPolicy.from_dict(wire["policy"])
    m = wire["manifest"]
    m.update(execution_policy_id=selected.identifier, availability_assumption_id=plan.identifier)
    m["quality_manifest"]["content_hash"] = digest(wire["quality"])
    ref = m["market_snapshots"][0]
    m["market_snapshots"] = [
        {**ref, "artifact_id": digest(s), "content_hash": digest(s)} for s in wire["sources"]
    ] + [
        {**ref, "artifact_id": b["snapshot_id"], "content_hash": b["snapshot_id"]} for b in bundles
    ]
    m["market_snapshots"] = list({v["artifact_id"]: v for v in m["market_snapshots"]}.values())
    m["analyses"] = [
        {**m["analyses"][0], "artifact_id": b["analysis_id"], "content_hash": b["analysis_id"]}
        for b in bundles
    ]
    manifest = RunManifest.from_dict(m)
    for e in wire["events"]:
        e.update(
            run_id=manifest.identifier,
            availability_assumption_id=plan.identifier,
            quality_evidence_ids=[digest(wire["quality"])],
        )
    tape = FrozenTape(
        manifest,
        selected,
        tuple(MarketEvent.from_dict(e) for e in wire["events"]),
        tuple(FrozenJSON.freeze(s) for s in wire["sources"]),
        plan,
        FrozenJSON.freeze(wire["quality"]),
    )
    initial = initialize(
        manifest,
        tape.identifier,
        r.initial.policy,
        r.initial.events[0],
        cutoff=r.initial.to_dict()["known_at"],
    )
    return replace(
        configure(r, run_id=manifest.identifier),
        tape=tape,
        initial=initial,
        analyses=tuple(FrozenAnalysis(manifest, plan, FrozenJSON.freeze(b)) for b in bundles),
    )


def test_multiticker_ordering_and_equal_timestamp_sequences() -> None:
    import json
    from copy import deepcopy

    from donghak_stock_vision.data.learning import digest

    r = runner()
    wire = json.loads(r.tape.to_json())
    source = deepcopy(wire["sources"][0])
    # Select the source for the first event independent of canonical source ordering.
    source = next(
        deepcopy(s)
        for s in wire["sources"]
        if digest(s) == wire["events"][0]["source"]["content_hash"]
    )
    source["ticker"] = "000001"
    wire["sources"].append(source)
    other = deepcopy(wire["events"][0])
    other.update(
        sequence=2,
        ticker="000001",
        source={**other["source"], "artifact_id": digest(source), "content_hash": digest(source)},
    )
    wire["events"][1]["sequence"] = 3
    wire["events"].insert(1, other)
    releases = wire["release_plan"]["releases"]
    releases[1]["sequence"] = 3
    releases.insert(1, {**deepcopy(releases[0]), "sequence": 2})
    wire["quality"]["coverage"]["000001"] = deepcopy(wire["quality"]["coverage"]["005930"])
    b = deepcopy(r.analyses[0].bundle.to_dict())
    b["signal"]["ticker"] = "000001"
    b["analysis_id"] = digest(b["signal"])
    ar = deepcopy(wire["release_plan"]["analysis_releases"][0])
    ar.update(sequence=2, analysis_id=b["analysis_id"])
    wire["release_plan"]["analysis_releases"].append(ar)
    r = rebuild(r, wire, [r.analyses[0].bundle.to_dict(), b])
    steps = r.config.to_dict()["decision_steps"]
    steps[0]["sequence"] = 2
    steps[0]["admission_policy"]["ordered_tickers"] = ["005930", "000001"]
    second = deepcopy(steps[0])
    second.update(ticker="000001", analysis_id=b["analysis_id"])
    second["market"]["ticker"] = "000001"
    second["admission_policy"].update(
        max_position_quantity={"000001": 10},
        max_position_notional_krw={"000001": "1000"},
        marks={"000001": {"sequence": 2, "field": "open"}},
    )
    r = configure(r, tickers=["005930", "000001"], decision_steps=[second, steps[0]])
    result = r.run()
    v = result.to_dict()
    assert v["status"] == "completed", v
    assert [e["sequence"] for e in v["events"]] == [1, 2, 3]
    assert [d["result"]["ticker"] for d in v["events"][1]["decisions"]] == ["005930", "000001"]
    assert v["events"][1]["blocked"][0]["reason"] == "concurrent_reservation_unsupported"
    assert len(v["final_account"]["fills"]) == 1
    assert configure(r, decision_steps=[steps[0], second]).run().identifier == result.identifier


def test_buy_then_sell_is_explicitly_blocked_by_existing_bridge() -> None:
    import json
    from copy import deepcopy

    from donghak_stock_vision.data.learning import digest

    r = runner()
    wire = json.loads(r.tape.to_json())
    b = deepcopy(r.analyses[0].bundle.to_dict())
    b["signal"].update(
        anchor_at=FUTURE,
        last_trading_date="2026-01-06",
        context="prior_rise",
        up_score=None,
        up_status="context_mismatch",
        down_score=0.8,
        down_status="scored",
    )
    b["analysis_id"] = digest(b["signal"])
    release = deepcopy(wire["release_plan"]["analysis_releases"][0])
    release.update(analysis_id=b["analysis_id"], sequence=2, available_at=FUTURE)
    wire["release_plan"]["analysis_releases"].append(release)
    r = rebuild(r, wire, [r.analyses[0].bundle.to_dict(), b])
    steps = r.config.to_dict()["decision_steps"]
    later = deepcopy(steps[0])
    later.update(sequence=2, analysis_id=b["analysis_id"])
    later["market"].update(
        observed_at=FUTURE, received_at=FUTURE, price_observed_at=FUTURE, reference_price_krw="90"
    )
    later["admission_policy"]["marks"]["005930"]["sequence"] = 2
    later["reservation"] = {"max_notional_krw": "0", "max_cost_krw": "0"}
    v = configure(r, decision_steps=[*steps, later]).run().to_dict()
    assert v["events"][1]["decisions"][0]["result"]["action"] == "SELL"
    assert v["events"][1]["candidates"][0]["reason"] == "exit_history_projection_unavailable"
    assert not v["events"][1]["admissions"]
    assert len(v["final_account"]["fills"]) == 1
    assert v["final_account"]["positions"]["005930"]["quantity"] == 10


@pytest.mark.parametrize(
    "mode,reason",
    [("point_in_time", "pit_runner_unsupported"), ("real", "real_runner_unsupported")],
)
def test_unimplemented_information_modes_rejected(mode: str, reason: str) -> None:
    # Validate the mode before any strategy or account access. Do not fake PIT fills.
    r = runner()
    m = r.tape.manifest.to_dict()
    if mode == "point_in_time":
        m["information_mode"] = mode
        m["availability_assumption_id"] = None
        for key in ("market_snapshots", "models", "analyses"):
            for ref in m[key]:
                ref["information_mode"] = mode
        for key in ("quality_manifest", "calendar", "universe"):
            m[key]["information_mode"] = mode
    else:
        m["data_origin"], m["usage_restriction"] = "real", "research_only"
        for key in ("market_snapshots", "models", "analyses"):
            for ref in m[key]:
                ref.update(data_origin="real", usage_restriction="research_only")
        for key in ("quality_manifest", "calendar", "universe"):
            m[key].update(data_origin="real", usage_restriction="research_only")

    # An immutable stub supplies only the manifest; no fabricated public evidence.
    class ManifestOnly:
        manifest = RunManifest.from_dict(m)

    from typing import cast

    with pytest.raises(ValueError, match=reason):
        replace(r, tape=cast(FrozenTape, ManifestOnly())).run()


def test_hidden_future_price_and_labels_do_not_change_past_decision() -> None:
    import json

    from donghak_stock_vision.data.learning import digest

    r = runner()
    original = r.run().to_dict()
    wire = json.loads(r.tape.to_json())
    e = wire["events"][1]
    source = next(s for s in wire["sources"] if digest(s) == e["source"]["content_hash"])
    source["fields"]["close"] = "999"
    source["original"]["future_labels"] = [0, 0, 0]
    e["public_fields"]["close"] = "999"
    e["source"].update(artifact_id=digest(source), content_hash=digest(source))
    changed = rebuild(r, wire, [r.analyses[0].bundle.to_dict()]).run().to_dict()
    assert original["events"][0]["decisions"] == changed["events"][0]["decisions"]
    assert original["final_account"]["total_cash_krw"] == changed["final_account"]["total_cash_krw"]
    assert original["input_hash"] != changed["input_hash"]


def test_duplicate_decision_sequence_ticker_is_rejected() -> None:
    r = runner()
    steps = r.config.to_dict()["decision_steps"]
    with pytest.raises(ValueError, match="duplicate_decision_step"):
        configure(r, decision_steps=[*steps, *steps]).run()


def test_empty_period_has_no_synthetic_days_or_forced_positions() -> None:
    r = runner("SELL")
    v = configure(r, start_at="2026-01-05T02:00:00Z", end_at="2026-01-05T03:00:00Z").run().to_dict()
    assert v["processed_event_count"] == 0
    assert v["final_account"] == r.initial.to_dict()


def test_future_market_context_fails_without_economic_change() -> None:
    r = runner()
    steps = r.config.to_dict()["decision_steps"]
    steps[0]["market"]["received_at"] = FUTURE
    v = configure(r, decision_steps=steps).run().to_dict()
    assert v["events"][0]["reason"] == "future_market_context"
    assert v["final_account"] == r.initial.to_dict()


def test_cost_policy_missing_before_any_processing() -> None:
    r = runner()
    p = r.execution_policy.to_dict()
    del p["cost_rounding"]
    with pytest.raises(ValueError, match="missing_or_unknown_fields"):
        replace(r, execution_policy=FrozenJSON.freeze(p)).run()
