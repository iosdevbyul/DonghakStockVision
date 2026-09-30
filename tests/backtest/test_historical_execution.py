"""Fixture daily DB + an explicit public analysis test double, never market evidence."""

from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical import ASSUMPTION, build_historical_input
from donghak_stock_vision.backtest.historical_execution import MODE, market_context
from donghak_stock_vision.backtest.ledger import initialize
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.data.learning import FEATURES, digest, event_contract
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from tests.backtest.helpers import manifest, policy
from tests.backtest.test_decision_bridge import scenario
from tests.backtest.test_execution import execution_policy
from tests.backtest.test_ledger import command
from tests.backtest.test_ledger import policy as ledger_policy
from tests.strategy.helpers import inputs
from tests.strategy.helpers import policy as decision_policy


class ControlledResearch(SignalService):
    """Override only the public analysis boundary; all admission/execution checks stay real."""

    buy_days = (9,)

    def research(
        self, version: str, snapshot_id: str, tickers: list[str], anchor_date: date
    ) -> list[dict[str, Any]]:
        buying = anchor_date.day in self.buy_days
        a = inputs(0, "prior_decline" if buying else "prior_rise", 0.8)["analysis"]
        a.update(
            ticker=tickers[0],
            model_version=version,
            snapshot_id=snapshot_id,
            data_origin="real",
            usage_restriction="research_only",
            model_created_at=self.store.created_at("model", version).isoformat(),
            snapshot_as_of=self.store.get("snapshot", snapshot_id)["cutoff"],
            anchor_at=datetime.combine(anchor_date, time(16), SEOUL).isoformat(),
            last_trading_date=anchor_date.isoformat(),
            **event_contract("verified_sessions"),
        )
        return [{**a, "analysis_id": self.store.put("signal", a)}]


def historical_runner(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    buy_open: int = 90,
    sell_open: int = 110,
    held: int = 0,
    decisions: tuple[int, ...] = (1, 3),
    end_day: int = 14,
    reserve_notional: str = "950",
    session_days: tuple[int, ...] = (9, 12, 13, 14),
    session_prices: tuple[int, ...] | None = None,
    research_calendar: ResearchCalendarPolicy | None = None,
    **policy_changes: Any,
) -> BacktestRunner:
    import donghak_stock_vision.backtest.historical as builder

    market = SQLiteStore(path / "market.db")
    analysis = AnalysisStore(path / "analysis.db")
    collected = datetime(2026, 2, 1, tzinfo=UTC)
    captured = datetime(2026, 2, 2, tzinfo=UTC)
    days = [date(2026, 1, d) for d in session_days if d <= end_day]
    prices = list(session_prices) if session_prices else [100, buy_open, 100, sell_open]
    rows = [
        DailyBar(
            "005930",
            d,
            p,
            max(p, 100),
            min(p, 100),
            100,
            100,
            10000,
            "fixture_daily",
            "KOSPI",
            "unadjusted",
            collected,
        )
        if p > 0
        else DailyBar(
            "005930", d, 0, 0, 0, 100, 0, 0, "fixture_daily", "KOSPI", "unadjusted", collected
        )
        for d, p in zip(days, prices, strict=False)
    ]
    for b in rows:
        rid = market.archive(
            "fixture", b.provider, b.ticker, RawPage(b"fixture", collected, b.trading_date)
        )
        market.save([b], [rid])
    q = {
        "schema_version": 1,
        "source": "offline_fixture_not_market_proof",
        "verified_at": captured.isoformat(),
        "sessions": [d.isoformat() for d in days],
        "coverage": {
            "005930": {
                "start": days[0].isoformat(),
                "end": days[-1].isoformat(),
                "adjustment": "unadjusted",
                "excluded_dates": [],
            }
        },
    }
    snap = capture(
        market,
        ["005930"],
        days[0],
        days[-1],
        captured,
        "historical_research",
        quality=q,
        research_calendar=research_calendar,
    )
    sid = analysis.put("snapshot", snap)
    split: dict[str, Any] = {}
    did = analysis.put("dataset", {"snapshot_id": sid, "split": split})
    model = {
        "schema_version": 1,
        "features": list(FEATURES),
        "snapshot_id": sid,
        "dataset_id": did,
        "split_hash": digest(split),
        **{
            k: snap[k]
            for k in ("mode", "data_origin", "usage_restriction", "anchor_policy", "session_basis")
        },
        **event_contract("verified_sessions"),
        "directions": {d: {"parameters": None} for d in ("up", "down")},
    }
    mid = analysis.put("model", model)
    m = manifest().to_dict()

    def real(v: Any) -> None:
        if isinstance(v, dict):
            if "data_origin" in v:
                v.update(data_origin="real", usage_restriction="research_only")
            for x in v.values():
                real(x)
        elif isinstance(v, list):
            for x in v:
                real(x)

    real(m)
    start = "2026-01-09T00:00:00+09:00"
    m.update(start_at=start, end_at=f"2026-01-{end_day + 1:02d}T00:00:00+09:00")
    m["initial_account"].update(
        observed_at=start,
        received_at=start,
        available_cash_krw=str(10000 - held * 100),
        positions=[
            {
                "ticker": "005930",
                "quantity": held,
                "reserved_quantity": 0,
                "sellable_quantity": held,
                "cost_basis_krw": str(held * 100),
            }
        ]
        if held
        else [],
    )
    with monkeypatch.context() as patch:
        patch.setattr(builder, "SignalService", ControlledResearch)
        frozen = build_historical_input(
            market,
            start=days[0],
            end=days[-1],
            tickers=["005930"],
            captured_at=captured.isoformat(),
            quality=FrozenJSON.freeze(q),
            manifest_template=RunManifest.from_dict(m),
            policy=policy(),
            availability_assumption=ASSUMPTION,
            analysis_store=analysis,
            model_id=mid,
            research_calendar=research_calendar,
        )
    market.close()
    analysis.close()
    tape = frozen.tape
    initial = initialize(
        tape.manifest,
        tape.identifier,
        ledger_policy(),
        command("initialize", 0, {}, effective=start, known=start),
        cutoff=start,
    )
    ep = execution_policy(**{"liquidity": MODE, **policy_changes})
    dp = decision_policy()
    dp.update(
        allowed_models=[mid],
        session_basis="verified_sessions",
        effective_from=start,
        effective_until="2027-01-01T00:00:00Z",
    )
    dp["costs"].update(effective_from=start, effective_until=dp["effective_until"])
    dp["ttl_seconds"]["analysis"] = 86400
    dp["ttl_seconds"]["max_skew"] = 86400
    if reserve_notional == "1150":
        dp["sizing"]["buy_budget_krw"] = "1200"
        dp["costs"]["buy"]["slippage_rate"] = "0.2"
    steps = []
    for seq in decisions:
        if seq > len(days):
            continue
        event = tape.events[seq - 1].to_dict()
        clock = VirtualClock.at(event["available_at"], seq)
        aid = frozen.analyses[seq - 1].bundle.to_dict()["analysis_id"]
        ap = scenario()["policy"]
        ap["order_budget_krw"] = "2000"
        ap["marks"]["005930"] = {"sequence": seq, "field": "close"}
        steps.append(
            {
                "sequence": seq,
                "ticker": "005930",
                "analysis_id": aid,
                "market": market_context(tape, frozen.document, "005930", clock, field="close"),
                "admission_policy": ap,
                "reservation": {
                    "max_notional_krw": reserve_notional if seq == 1 and not held else "0",
                    "max_cost_krw": "50" if seq == 1 and not held else "0",
                },
            }
        )
    config = {
        "run_id": tape.manifest.identifier,
        "start_at": tape.manifest.to_dict()["start_at"],
        "end_at": tape.manifest.to_dict()["end_at"],
        "tickers": ["005930"],
        "event_clock": "available_at",
        "history_mode": "verified_execution",
        "decision_steps": steps,
    }
    return BacktestRunner(
        tape,
        frozen.analyses,
        initial,
        DecisionPolicy.from_dict(dp),
        ep,
        FrozenJSON.freeze(config),
        frozen.document,
    )


def test_db_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = historical_runner(tmp_path, monkeypatch)
    v = r.run().to_dict()
    assert v["status"] == "completed", v
    fills = [x for e in v["events"] for x in e.get("executions", []) if x["status"] == "filled"]
    assert len(fills) == 2, v
    assert [f["execution_session"] for f in fills] == ["2026-01-12", "2026-01-14"]
    assert [f["source_open"] for f in fills] == ["90", "110"]
    assert v["final_account"]["total_cash_krw"] == "10170.55"
    assert v["final_account"]["positions"]["005930"]["quantity"] == 0
    assert v["position_history"]["episodes"][0]["status"] == "confirmed_full_exit"
    assert r.run().identifier == FrozenJSON.freeze(v).identifier


def executions(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for e in value["events"] for x in e.get("executions", [])]


@pytest.mark.parametrize("side,opening", [("BUY", 90), ("BUY", 110), ("SELL", 90), ("SELL", 110)])
def test_gaps_use_open_without_clamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str, opening: int
) -> None:
    r = historical_runner(
        tmp_path,
        monkeypatch,
        buy_open=opening,
        sell_open=opening,
        held=5 if side == "SELL" else 0,
        decisions=(3,) if side == "SELL" else (1,),
        reserve_notional="1150",
        slippage_rate="0",
        buy_fee_rate="0",
        sell_fee_rate="0",
        sell_tax_rate="0",
    )
    v = r.run().to_dict()
    x = executions(v)
    assert len(x) == 1 and x[0]["status"] == "filled", v
    assert x[0]["fill"]["price_krw"] == str(opening)
    assert x[0]["fill"]["benchmark_price_krw"] == str(opening)
    assert x[0]["fill"]["slippage_krw"] == "0"
    assert v["final_account"]["total_cash_krw"] == str(
        10000 - 10 * opening if side == "BUY" else 9500 + 5 * opening
    )


def test_costs_slippage_and_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = historical_runner(tmp_path, monkeypatch)
    x = executions(r.run().to_dict())
    assert [(e["fill"]["price_krw"], e["fill"]["fee_krw"], e["fill"]["tax_krw"]) for e in x] == [
        ("90.9", "1.82", "0"),
        ("108.9", "3.27", "4.36"),
    ]
    assert [e["fill"]["slippage_krw"] for e in x] == ["9", "11"]
    assert all(e["usage_restriction"] == "research_only" and not e["executable"] for e in x)
    assert all("historical_daily_full_fill_assumption" in e["limitations"] for e in x)
    assert x[0]["fill"]["fill_known_at"] == r.tape.events[1].to_dict()["available_at"]
    assert x[0]["fill"]["source_event_id"] == r.tape.events[1].identifier


def test_gap_exceeding_reservation_never_resizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = historical_runner(tmp_path, monkeypatch, buy_open=120, decisions=(1,))
    v = r.run().to_dict()
    x = executions(v)
    assert x and all(e["status"] == "rejected" for e in x)
    assert "reservation" in x[0]["reason"]
    assert v["final_account"]["total_cash_krw"] == "10000"
    assert not v["final_account"]["fills"] and v["pending"]


def test_missing_open_no_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = historical_runner(tmp_path, monkeypatch, buy_open=0, decisions=(1,))
    v = r.run().to_dict()
    x = executions(v)
    assert x[0]["reason"] == "historical_open_unavailable"
    assert not v["final_account"]["fills"] and v["pending"]
    assert all(e["status"] == "rejected" for e in x)


def test_no_next_session_leaves_open_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = historical_runner(tmp_path, monkeypatch, end_day=9, decisions=(1,))
    v = r.run().to_dict()
    assert not executions(v) and v["pending"]
    assert v["final_account"]["total_cash_krw"] == "10000"
    assert not v["final_account"]["fills"]


def test_same_event_future_view_and_idempotency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.execution import execute
    from donghak_stock_vision.backtest.ledger import restore

    r = historical_runner(tmp_path, monkeypatch, decisions=(1,))
    v = r.run().to_dict()
    a = FrozenJSON.freeze(v["events"][0]["admissions"][0])
    reserved = restore(FrozenJSON.freeze(a.to_dict()["reservation_checkpoint"]))
    common = dict(
        acceptance=a,
        ledger=reserved,
        tape=r.tape,
        expected_revision=reserved.to_dict()["revision"],
        ledger_sequence=2,
        quantity=10,
    )
    first, second = r.tape.events[:2]
    clock = VirtualClock.at(first.to_dict()["available_at"], 1)
    view = r.tape.view(clock).to_dict()
    assert len(view["items"]) == 1 and all(x["trading_date"] == "2026-01-09" for x in view["items"])
    result, state = execute(**common, event=first, clock=clock)
    assert result.to_dict()["reason"] == "same_or_past_event" and state == reserved
    result, state = execute(**common, event=second, clock=clock)
    assert result.to_dict()["status"] == "rejected" and state == reserved
    future = VirtualClock.at(second.to_dict()["available_at"], 2)
    result, state = execute(**common, event=second, clock=future)
    again, same = execute(**{**common, "ledger": state}, event=second, clock=future)
    assert result == again and state == same
    wrong, state2 = execute(**{**common, "quantity": 11}, event=second, clock=future)
    assert wrong.to_dict()["status"] == "rejected" and state2 == reserved


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_price_field": "close"},
        {"fill_mode": "partial"},
        {"liquidity": "real_liquidity"},
        {"scope": "operational"},
        {"slippage_rate": None},
    ],
)
def test_unsupported_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    r = historical_runner(tmp_path, monkeypatch, **changes)
    with pytest.raises((ValueError, TypeError)):
        r.run()


def test_policy_and_input_evidence_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = historical_runner(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="historical_input_evidence_missing"):
        replace(r, historical_input=None).run()
    with pytest.raises(ValueError, match="real_runner_unsupported"):
        replace(r, execution_policy=execution_policy()).run()
    v = r.run()
    new = replace(r, execution_policy=execution_policy(liquidity=MODE, slippage_rate="0")).run()
    assert new.identifier != v.identifier
    assert executions(new.to_dict())[0]["fill_hash"] != executions(v.to_dict())[0]["fill_hash"]


def test_frozen_evidence_is_not_an_approval_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = historical_runner(tmp_path, monkeypatch)
    assert r.historical_input is not None
    broken = r.historical_input.to_dict()
    broken["source_metadata"]["quality"]["source"] = "changed-evidence"
    with pytest.raises(ValueError, match="historical_quality_mismatch"):
        replace(r, historical_input=FrozenJSON.freeze(broken)).run()


def test_fill_identifier_conflict_preserves_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.execution import execute
    from donghak_stock_vision.backtest.ledger import restore

    r = historical_runner(tmp_path, monkeypatch, decisions=(1,))
    v = r.run().to_dict()
    acceptance = FrozenJSON.freeze(v["events"][0]["admissions"][0])
    ledger = restore(FrozenJSON.freeze(v["final_checkpoint"]))
    event = r.tape.events[1]
    output, unchanged = execute(
        acceptance,
        ledger,
        r.tape,
        event,
        VirtualClock.at(event.to_dict()["available_at"], 2),
        expected_revision=acceptance.to_dict()["reservation_checkpoint"]["state"]["revision"],
        ledger_sequence=3,
        quantity=10,
    )
    assert output.to_dict()["reason"] == "fill_identifier_conflict"
    assert unchanged == ledger


def test_sell_cannot_reserve_more_than_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.contracts import VirtualOrder
    from donghak_stock_vision.backtest.execution import accept_candidate

    r = historical_runner(tmp_path, monkeypatch, held=5, decisions=(3,))
    v = r.run().to_dict()
    candidate = v["events"][2]["candidates"][0]
    order = candidate["candidate"]
    order.update(quantity=6, remaining_quantity=6)
    changed = VirtualOrder.from_dict(order)
    candidate.update(candidate=changed.to_dict(), order_id=changed.identifier)
    clock = VirtualClock.at(r.tape.events[2].to_dict()["available_at"], 3)
    output, same = accept_candidate(
        FrozenJSON.freeze(candidate),
        r.initial,
        r.tape,
        clock,
        clock,
        clock,
        r.execution_policy,
        FrozenJSON.freeze({"max_notional_krw": "0", "max_cost_krw": "0"}),
        ledger_sequence=1,
        historical_input=r.historical_input,
    )
    assert output.to_dict()["reason"] == "insufficient_shares"
    assert same == r.initial


def test_sell_without_next_session_stays_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = historical_runner(tmp_path, monkeypatch, held=5, decisions=(3,), end_day=13)
    v = r.run().to_dict()
    assert not executions(v) and v["pending"]
    assert v["final_account"]["positions"]["005930"]["quantity"] == 5
    assert v["final_account"]["positions"]["005930"]["reserved_quantity"] == 5
