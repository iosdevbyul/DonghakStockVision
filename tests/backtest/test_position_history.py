"""Synthetic lineage evidence and round trips, never real performance evidence."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.contracts import ExecutionPolicy, MarketEvent, RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.ledger import checkpoint, initialize, restore
from donghak_stock_vision.backtest.position_history import (
    build_position_history,
    validate_position_history,
)
from donghak_stock_vision.data.learning import digest
from tests.backtest.test_runner import configure, runner


def round_trip(*, twice: bool = False) -> Any:
    r = runner()
    count = 8 if twice else 6
    days = [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
    ][:count]
    stamps = [day + "T00:00:00Z" for day in days]
    original = r.tape.sources[0].to_dict()
    # Canonical source order is independent of chronology.
    original = next(
        s.to_dict()
        for s in r.tape.sources
        if s.identifier == r.tape.events[0].to_dict()["source"]["content_hash"]
    )
    sources = []
    releases = []
    for i, stamp in enumerate(stamps):
        s = deepcopy(original)
        s.update(trading_date=days[i], source_received_at=stamp)
        s["fields"]["open"] = ["100", "90", "100", "110"][i % 4]
        sources.append(FrozenJSON.freeze(s))
        releases.append(
            {
                "sequence": i + 1,
                "previous_revision": None,
                "field_times": {
                    key: stamp if key == "open" else days[i] + "T01:00:00Z" for key in s["fields"]
                },
            }
        )
    q = r.tape.quality.to_dict()
    q["sessions"] = days
    q["coverage"]["005930"].update(start=days[0], end=days[-1])
    quality = FrozenJSON.freeze(q)
    plan = r.tape.release_plan.to_dict()
    plan["releases"] = releases
    plan["analysis_releases"] = []
    bundles = []
    steps = []
    for i in range(0, count, 2):
        b = r.analyses[0].bundle.to_dict()
        buying = i % 4 == 0
        b["signal"].update(
            anchor_at=stamps[i],
            last_trading_date=days[i],
            context="prior_decline" if buying else "prior_rise",
            up_score=0.8 if buying else None,
            down_score=None if buying else 0.8,
            up_status="scored" if buying else "context_mismatch",
            down_status="context_mismatch" if buying else "scored",
        )
        b["analysis_id"] = digest(b["signal"])
        bundles.append(b)
        plan["analysis_releases"].append(
            {
                "model_id": b["model_id"],
                "snapshot_id": b["snapshot_id"],
                "analysis_id": b["analysis_id"],
                "available_at": stamps[i],
                "sequence": i + 1,
            }
        )
        step = deepcopy(r.config.to_dict()["decision_steps"][0])
        step.update(sequence=i + 1, analysis_id=b["analysis_id"])
        step["market"].update(
            observed_at=stamps[i],
            received_at=stamps[i],
            price_observed_at=stamps[i],
            reference_price_krw="100",
        )
        step["admission_policy"]["marks"]["005930"]["sequence"] = i + 1
        if not buying:
            step["reservation"] = {"max_notional_krw": "0", "max_cost_krw": "0"}
        steps.append(step)
    release = FrozenJSON.freeze(plan)
    p = r.tape.policy.to_dict()
    p["availability_policy"]["content_hash"] = release.identifier
    policy = ExecutionPolicy.from_dict(p)
    m = r.tape.manifest.to_dict()
    m.update(
        execution_policy_id=policy.identifier,
        availability_assumption_id=release.identifier,
        end_at=days[-1] + "T02:00:00Z",
    )
    m["quality_manifest"]["content_hash"] = quality.identifier
    ref = m["market_snapshots"][0]
    m["market_snapshots"] = [
        {**ref, "artifact_id": s.identifier, "content_hash": s.identifier} for s in sources
    ] + [
        {**ref, "artifact_id": bundles[0]["snapshot_id"], "content_hash": bundles[0]["snapshot_id"]}
    ]
    m["analyses"] = [
        {**m["analyses"][0], "artifact_id": b["analysis_id"], "content_hash": b["analysis_id"]}
        for b in bundles
    ]
    manifest = RunManifest.from_dict(m)
    events = []
    for i, frozen_source in enumerate(sources):
        e = r.tape.events[0].to_dict()
        e.update(
            run_id=manifest.identifier,
            sequence=i + 1,
            trading_date=days[i],
            event_at=stamps[i],
            available_at=stamps[i],
            source_received_at=stamps[i],
            quality_evidence_ids=[quality.identifier],
            availability_assumption_id=release.identifier,
            public_fields=frozen_source.to_dict()["fields"],
            source={
                **ref,
                "artifact_id": frozen_source.identifier,
                "content_hash": frozen_source.identifier,
            },
        )
        events.append(MarketEvent.from_dict(e))
    tape = FrozenTape(manifest, policy, tuple(events), tuple(sources), release, quality)
    initial = initialize(
        manifest,
        tape.identifier,
        r.initial.policy,
        r.initial.events[0],
        cutoff=r.initial.to_dict()["known_at"],
    )
    return replace(
        configure(
            r,
            run_id=manifest.identifier,
            end_at=m["end_at"],
            decision_steps=steps,
            history_mode="verified_execution",
        ),
        tape=tape,
        initial=initial,
        analyses=tuple(FrozenAnalysis(manifest, release, FrozenJSON.freeze(b)) for b in bundles),
    )


@pytest.mark.parametrize(
    "twice,cash,held,exits", [(False, "9259.73", 10, 1), (True, "10341.1", 0, 2)]
)
def test_round_trip_runner(twice: bool, cash: str, held: int, exits: int) -> None:
    r = round_trip(twice=twice)
    result = r.run()
    v = result.to_dict()
    assert v["status"] == "completed", [(x["sequence"], x.get("reason")) for x in v["events"]]
    actions = [d["result"]["action"] for e in v["events"] for d in e["decisions"]]
    assert actions == (["BUY", "SELL", "BUY", "SELL"] if twice else ["BUY", "SELL", "BUY"])
    executions = [x for e in v["events"] for x in e["executions"]]
    assert all(x["status"] == "filled" for x in executions), executions
    assert len(executions) == len(actions)
    assert v["final_account"]["total_cash_krw"] == cash
    assert v["final_account"]["positions"]["005930"]["quantity"] == held
    assert v["final_account"]["positions"]["005930"]["reserved_quantity"] == 0
    h = v["position_history"]
    assert sum(x["status"] == "confirmed_full_exit" for x in h["episodes"]) == exits
    assert h["episodes"][-1]["status"] == ("confirmed_full_exit" if twice else "still_open")
    assert r.run().identifier == result.identifier
    state = restore(FrozenJSON.freeze(v["final_checkpoint"]))
    assert validate_position_history(FrozenJSON.freeze(h), state, r.tape, cutoff=v["end_at"]) == h


def completed() -> tuple[Any, dict[str, Any], Any, FrozenJSON]:
    r = round_trip()
    v = r.run().to_dict()
    state = restore(FrozenJSON.freeze(v["final_checkpoint"]))
    return r, v, state, FrozenJSON.freeze(v["position_history"])


def test_duplicate_evidence_is_idempotent_and_conflict_rejected() -> None:
    r, v, state, h = completed()
    packets = tuple(FrozenJSON.freeze(x) for x in h.to_dict()["evidence"])
    assert build_position_history(state, r.tape, (*packets, packets[0]), cutoff=v["end_at"]) == h
    bad = packets[0].to_dict()
    bad["decision_result"]["reason_codes"] = ["tampered"]
    with pytest.raises(ValueError, match="history_id_conflict"):
        build_position_history(
            state, r.tape, (*packets, FrozenJSON.freeze(bad)), cutoff=v["end_at"]
        )


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("run_id", "0" * 64, "history_run_mismatch"),
        ("account_id", "another-account", "history_account_mismatch"),
        ("revision", 999, "history_revision_mismatch"),
    ],
)
def test_foreign_history_and_revision(key: str, value: Any, reason: str) -> None:
    r, v, state, h = completed()
    raw = h.to_dict()
    raw[key] = value
    with pytest.raises(ValueError, match=reason):
        validate_position_history(FrozenJSON.freeze(raw), state, r.tape, cutoff=v["end_at"])


def test_wrong_exit_reference_and_other_ticker_exit_rejected() -> None:
    r, v, state, h = completed()
    raw = h.to_dict()
    raw["last_exits"]["005930"]["exit_fill_id"] = "f" * 64
    with pytest.raises(ValueError, match="history_projection_conflict"):
        validate_position_history(FrozenJSON.freeze(raw), state, r.tape, cutoff=v["end_at"])
    raw = h.to_dict()
    raw["last_exits"]["000001"] = raw["last_exits"].pop("005930")
    with pytest.raises(ValueError, match="history_projection_conflict"):
        validate_position_history(FrozenJSON.freeze(raw), state, r.tape, cutoff=v["end_at"])


def test_missing_actual_fill_evidence_is_not_inferred_from_flat_quantity() -> None:
    r = round_trip(twice=True)
    v = r.run().to_dict()
    state = restore(FrozenJSON.freeze(v["final_checkpoint"]))
    assert state.to_dict()["positions"]["005930"]["quantity"] == 0
    evidence = tuple(FrozenJSON.freeze(x) for x in v["position_history"]["evidence"])
    with pytest.raises(ValueError, match="history_fill_evidence_missing"):
        build_position_history(state, r.tape, evidence[:-1], cutoff=v["end_at"])
    # A genuinely flat initial account has no invented confirmed exit either.
    initial = build_position_history(r.initial, r.tape, (), cutoff=r.config.to_dict()["start_at"])
    assert initial.to_dict()["last_exits"] == {}


def test_pending_sell_has_no_confirmed_exit_and_cannot_reenter() -> None:
    r = round_trip()
    # End after SELL reservation but before its future fill.
    partial = configure(r, end_at="2026-01-07T01:00:00Z").run().to_dict()
    assert partial["pending"]
    assert partial["position_history"]["last_exits"] == {}
    assert partial["position_history"]["episodes"][0]["status"] == "still_open"
    assert partial["final_account"]["positions"]["005930"]["reserved_quantity"] == 10


def test_future_history_rejected_and_initial_state_unchanged() -> None:
    r, _, state, h = completed()
    before = checkpoint(state)
    with pytest.raises(ValueError, match="history_future_state"):
        validate_position_history(h, state, r.tape, cutoff=r.config.to_dict()["start_at"])
    assert checkpoint(state) == before


def test_unfilled_sell_blocks_later_entry() -> None:
    r = round_trip()
    p = r.execution_policy.to_dict()
    p.update(sell_fee_rate="0.9", sell_tax_rate="0.9")
    v = replace(r, execution_policy=FrozenJSON.freeze(p)).run().to_dict()
    assert v["events"][3]["executions"][0]["reason"] == "negative_sale_proceeds"
    assert v["events"][4]["decisions"][0]["result"]["blocking_reasons"] == ["pending_order"]
    assert not v["position_history"]["last_exits"]
    assert len(v["final_account"]["fills"]) == 1


def test_phase3_cooldown_is_not_relaxed() -> None:
    from donghak_stock_vision.strategy.contracts import DecisionPolicy

    r = round_trip()
    p = r.decision_policy.to_dict()
    p["reentry"]["duration"] = 1000000
    v = replace(r, decision_policy=DecisionPolicy.from_dict(p)).run().to_dict()
    assert v["events"][4]["decisions"][0]["result"]["blocking_reasons"] == ["cooldown_active"]
    assert len(v["final_account"]["fills"]) == 2
    assert v["position_history"]["episodes"][-1]["status"] == "confirmed_full_exit"


def test_partial_position_sale_is_not_a_confirmed_full_exit() -> None:
    from donghak_stock_vision.strategy.contracts import DecisionPolicy

    r = round_trip()
    p = r.decision_policy.to_dict()
    p["sizing"].update(sell_mode="partial", partial_kind="quantity", partial_value=5)
    v = replace(r, decision_policy=DecisionPolicy.from_dict(p)).run().to_dict()
    assert v["status"] == "completed", v["events"]
    assert len(v["final_account"]["fills"]) == 2
    assert v["final_account"]["positions"]["005930"]["quantity"] == 5
    assert v["position_history"]["episodes"][-1]["status"] == "partial_exit"
    assert not v["position_history"]["last_exits"]
    assert v["events"][4]["decisions"][0]["result"]["action"] == "HOLD"
    assert v["events"][4]["decisions"][0]["result"]["diagnostics"]["safety_assurance"] is False


def test_wait_does_not_create_acquisition_or_exit() -> None:
    from donghak_stock_vision.strategy.contracts import DecisionPolicy

    r = round_trip()
    p = r.decision_policy.to_dict()
    p["thresholds"]["buy"] = "0.99"
    v = replace(r, decision_policy=DecisionPolicy.from_dict(p)).run().to_dict()
    assert v["final_account"]["fills"] == {}
    assert v["position_history"]["episodes"] == []
    assert v["position_history"]["last_exits"] == {}


def test_bad_packet_fill_reference_rejected() -> None:
    r, v, state, h = completed()
    evidence = h.to_dict()["evidence"]
    for packet in evidence:
        if packet["decision_result"]["action"] == "SELL":
            packet["execution"]["fill"]["order_id"] = "0" * 64
    with pytest.raises(ValueError, match="history_order_conflict"):
        build_position_history(
            state, r.tape, tuple(FrozenJSON.freeze(p) for p in evidence), cutoff=v["end_at"]
        )


def test_bridge_requires_valid_bound_exit_projection() -> None:
    from donghak_stock_vision.backtest.clock import VirtualClock
    from donghak_stock_vision.backtest.decision_bridge import DecisionBundle, admit
    from donghak_stock_vision.backtest.ledger import apply
    from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy

    r, v, _, h = completed()
    packets = h.to_dict()["evidence"]
    packet = next(
        p
        for p in packets
        if p["decision_input"]["request"]["decision_as_of"].startswith("2026-01-09")
    )
    reserved = restore(FrozenJSON.freeze(packet["acceptance"]["reservation_checkpoint"]))
    state = r.initial
    for event in reserved.events[1:-1]:
        state = apply(state, event, cutoff=event.document.to_dict()["known_at"])
    previous = tuple(
        FrozenJSON.freeze(p)
        for p in packets
        if p["execution"]["fill_hash"] in state.to_dict()["fills"]
    )
    clock = VirtualClock(**packet["acceptance"]["decision_clock"])
    proof = build_position_history(state, r.tape, previous, cutoff=clock.cutoff)
    inputs = DecisionInput.from_dict(packet["decision_input"])
    bundle = DecisionBundle.calculate(
        inputs, DecisionPolicy.from_dict(packet["decision_policy"]), state
    )
    analysis = FrozenAnalysis(
        r.tape.manifest, r.tape.release_plan, FrozenJSON.freeze(packet["analysis_bundle"])
    )
    args = dict(
        bundle=bundle,
        decision_id=bundle.result.identifier,
        ledger=state,
        tape=r.tape,
        analysis=analysis,
        decision_clock=clock,
        admission_clock=clock,
        policy=r.config.to_dict()["decision_steps"][2]["admission_policy"],
        history=FrozenJSON.freeze({}),
    )
    assert admit(**args, position_history=proof)[0].to_dict()["virtual_order_eligible"]
    assert admit(**args)[0].to_dict()["reason"] == "exit_history_projection_unavailable"
    raw = proof.to_dict()
    raw["last_exits"]["005930"]["exit_fill_id"] = "f" * 64
    bad = admit(**args, position_history=FrozenJSON.freeze(raw))[0].to_dict()
    assert bad["reason"] == "position_history_invalid:history_projection_conflict"
    assert bad["candidate"] is None


def test_failed_history_validation_rolls_back_exit_and_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import donghak_stock_vision.backtest.runner as runner_module

    original = build_position_history

    def fail_on_exit(*args: Any, **kwargs: Any) -> FrozenJSON:
        proof = original(*args, **kwargs)
        if proof.to_dict()["last_exits"]:
            raise ValueError("injected_exit_evidence_failure")
        return proof

    monkeypatch.setattr(runner_module, "build_position_history", fail_on_exit)
    v = round_trip().run().to_dict()
    assert v["events"][3]["reason"] == "injected_exit_evidence_failure"
    assert len(v["final_account"]["fills"]) == 1
    assert v["final_account"]["total_cash_krw"] == "9089.18"
    assert v["final_account"]["positions"]["005930"]["quantity"] == 10
    assert v["final_account"]["positions"]["005930"]["reserved_quantity"] == 10
    assert v["position_history"]["last_exits"] == {}
    assert len(v["position_history"]["evidence"]) == 1
