"""Only synthetic inputs and explicit signal doubles; no real/OOS model predictions."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.baseline_inputs import BaselineSession, BaselineSignalFrame
from donghak_stock_vision.backtest.baseline_runner import (
    PLANNING,
    BaselineBacktestRunner,
    input_fingerprint,
)
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.entry_contract import ObservedSession
from donghak_stock_vision.backtest.ledger import checkpoint, initialize, restore
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import FEATURES, digest
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.models.inference import HGBArtifact, ResearchScore
from donghak_stock_vision.signals.domain import SignalContext, SignalPolicy, build_signal
from tests.backtest.helpers import H, manifest
from tests.backtest.test_baseline_policy import BASELINE, artifact
from tests.backtest.test_ledger import command
from tests.backtest.test_ledger import policy as ledger_policy
from tests.learning.test_signal_domain import DOWN, POLICY

DAYS = (3, 6, 7, 8, 9, 10, 13)
VALUES = (0.0, 0.0, -0.01, 0.0, 0.005, 0.01, 0.0, 0.0)
QUALITY = FrozenJSON.freeze(
    dict(
        quality=None,
        session_basis="observed_bars_unverified",
        unavailable_dates={},
        quality_flags=[],
    )
)


@dataclass(frozen=True)
class Signals:
    up_artifact: HGBArtifact
    policy: SignalPolicy
    scores: tuple[tuple[str, str, float], ...]

    @property
    def fingerprint(self) -> str:
        return digest([self.up_artifact.fingerprint, self.policy.fingerprint, self.scores])

    def research(self, prefix: tuple[BaselineSession, ...], as_of: datetime) -> BaselineSignalFrame:
        assert all(r.session.bar_available_at <= as_of for r in prefix)
        last = prefix[-1]
        ticker, day = last.session.ticker, last.session.trading_date.isoformat()
        score = next((s for t, d, s in self.scores if t == ticker and d == day), 0.1)
        context = SignalContext(
            ticker,
            as_of,
            last.session.bar_available_at,
            "fixture_dataset",
            digest([r.fingerprint for r in prefix]),
            digest([r.fingerprint for r in prefix]),
            digest([list(FEATURES), list(VALUES)]),
            "prior_decline",
            (),
            "historical_research",
            "synthetic",
        )
        up = ResearchScore(
            "up",
            score,
            "scored",
            self.up_artifact.fingerprint,
            self.policy.research.fingerprint,
            (),
        )
        down = ResearchScore(
            "down", None, "context_mismatch", DOWN, self.policy.research.fingerprint, ()
        )
        return BaselineSignalFrame(
            build_signal(
                context,
                self.policy,
                up_model=self.up_artifact.fingerprint,
                down_model=DOWN,
                up=up,
                down=down,
            ),
            VALUES,
        )


def records(tickers: int = 1, days: tuple[int, ...] = DAYS) -> tuple[BaselineSession, ...]:
    out = []
    for i, day in enumerate(days):
        d = date(2025, 1, day)
        for n in range(tickers):
            ticker = f"{n + 1:06d}"
            session = ObservedSession(
                ticker,
                d,
                i,
                datetime.combine(d, time(9), SEOUL),
                datetime.combine(d, time(15, 30), SEOUL),
                datetime.combine(d + timedelta(days=1), time.min, SEOUL),
            )
            bar = DailyBar(
                ticker,
                d,
                10000,
                10050,
                9950,
                10000,
                1_000_000,
                10_000_000_000,
                "fake",
                "KOSPI",
                "unadjusted",
                session.bar_available_at,
            )
            out.append(BaselineSession(session, bar, QUALITY, d + timedelta(days=2), H))
    return tuple(out)


def runner(
    data: tuple[BaselineSession, ...] | None = None,
    scores: tuple[tuple[str, str, float], ...] | None = None,
) -> BaselineBacktestRunner:
    rows = data if data is not None else records()
    start, end = rows[0].session.trading_date, rows[-1].session.trading_date
    begin = datetime.combine(start, time.min, SEOUL).isoformat()
    finish = rows[-1].session.bar_available_at.isoformat()
    m = manifest().to_dict()
    m.update(start_at=begin, end_at=finish)
    m["models"][0]["content_hash"] = artifact().fingerprint
    m["initial_account"].update(
        observed_at=begin, received_at=begin, available_cash_krw="100000000"
    )
    initial = initialize(
        RunManifest.from_dict(m),
        input_fingerprint(rows),
        ledger_policy(),
        command("initialize", 0, {}, effective=begin, known=begin),
        cutoff=begin,
    )
    assert BASELINE.costs is not None
    if scores is None:
        scores = tuple(
            (r.session.ticker, start.isoformat(), 0.8)
            for r in rows
            if r.session.trading_date == start
        )
    return BaselineBacktestRunner(
        BASELINE,
        BASELINE.fingerprint,
        BASELINE.costs.fingerprint,
        BASELINE.contract(start, end),
        rows,
        initial,
        Signals(artifact(), POLICY, scores),
        H,
        PLANNING,
    )


def change(
    data: tuple[BaselineSession, ...], day: int, ticker: str = "000001", **changes: Any
) -> tuple[BaselineSession, ...]:
    return tuple(
        replace(r, bar=replace(r.bar, **changes))
        if r.session.trading_date.day == day and r.session.ticker == ticker and r.bar
        else r
        for r in data
    )


def events(result: FrozenJSON, kind: str) -> list[dict[str, object]]:
    return [e for e in result.to_dict()["events"] if e["kind"] == kind]


def test_time_exit_costs_replay_and_ledger() -> None:
    r = runner()
    result = r.run()
    assert result.fingerprint == r.run().fingerprint
    assert restore(checkpoint(result.ledger)) == result.ledger
    buys, sells = events(result.document, "buy_fill"), events(result.document, "sell_fill")
    assert len(buys) == len(sells) == 1
    assert sells[0]["reason"] == "fifth_session_close"
    assert sells[0]["holding_sessions"] == 5
    state = result.ledger.to_dict()
    assert state["positions"]["000001"]["quantity"] == 0
    assert state["reserved_cash_krw"] == "0"
    fills = sorted(state["fills"].values(), key=lambda f: f["sequence"])
    assert fills[0]["price_krw"] == "10010"
    assert fills[1]["price_krw"] == "9990"
    assert utc_day(fills[0]["fill_at"]) == date(2025, 1, 6)
    assert utc_day(fills[1]["fill_at"]) == date(2025, 1, 10)
    assert Decimal(state["total_cash_krw"]) >= 0
    assert r.initial.to_dict()["total_cash_krw"] == "100000000"
    assert result.document.to_dict()["fees"] == "1498"


def utc_day(value: str) -> date:
    return datetime.fromisoformat(value).astimezone(SEOUL).date()


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"high": 10300}, "target"),
        ({"low": 9700}, "stop_first"),
        ({"high": 10300, "low": 9700}, "stop_first"),
    ],
)
def test_target_stop_collision(updates: dict[str, Any], reason: str) -> None:
    result = runner(change(records(), 6, **updates)).run()
    sells = events(result.document, "sell_fill")
    assert sells[0]["reason"] == reason and sells[0]["holding_sessions"] == 1
    fill = sorted(result.ledger.to_dict()["fills"].values(), key=lambda f: f["sequence"])[1]
    expected = "10199" if reason == "target" else "9799"
    assert fill["price_krw"] == expected


def test_no_trade_next_session_no_retry() -> None:
    data = change(records(), 6, open=0, high=0, low=0, volume=0, trading_value=0)
    result = runner(data).run()
    assert not events(result.document, "buy_fill")
    assert events(result.document, "unfilled")[0]["reason"] == "no_trade_bar"
    assert result.ledger.to_dict()["reserved_cash_krw"] == "0"
    assert result.document.to_dict()["ending_cash"] == "100000000"


def test_simultaneous_ordering_twenty_positions_and_no_pyramiding() -> None:
    data = records(22, (3, 6, 7))
    scores = tuple((f"{i:06d}", "2025-01-03", 0.9 if i == 22 else 0.8) for i in range(1, 23))
    scores += (("000022", "2025-01-06", 0.99),)
    result = runner(data, scores).run()
    reservations = events(result.document, "reservation")
    assert [e["ticker"] for e in reservations][:3] == ["000022", "000001", "000002"]
    assert len(reservations) == 20
    assert len(events(result.document, "buy_fill")) == 20
    assert any(e["reason"] == "position_limit" for e in events(result.document, "unfilled"))
    assert len(result.document.to_dict()["open_positions"]) == 20
    assert Decimal(result.ledger.to_dict()["available_cash_krw"]) >= 0


def test_volume_cap_does_not_reallocate_same_session_cash() -> None:
    data = records(22, (3, 6))
    base = runner(data).run()
    limited = runner(change(data, 6, volume=100)).run()

    def allocations(doc: FrozenJSON) -> list[tuple[object, object, object]]:
        return [
            (e["ticker"], e["quantity"], e["reserved_cash"]) for e in events(doc, "reservation")
        ]

    assert allocations(base.document) == allocations(limited.document)
    assert len(events(limited.document, "reservation")) == 20
    fills = events(limited.document, "buy_fill")
    assert fills[0]["quantity"] == 1 and fills[0]["intended_quantity"] == 499
    assert not any(e["ticker"] in {"000021", "000022"} for e in fills)
    assert Decimal(limited.document.to_dict()["ending_cash"]) > Decimal(
        base.document.to_dict()["ending_cash"]
    )
    assert limited.ledger.to_dict()["reserved_cash_krw"] == "0"


def test_preopen_sizing_never_reads_future_open_or_exit_profit() -> None:
    data = records(2, (3, 6, 7))
    base = runner(data).run()
    higher = runner(change(data, 6, open=10100, high=10300)).run()
    a, b = events(base.document, "reservation"), events(higher.document, "reservation")
    assert [(e["quantity"], e["reserved_cash"]) for e in a] == [
        (e["quantity"], e["reserved_cash"]) for e in b
    ]
    assert b[0]["quantity"] == 499
    assert Decimal(str(b[0]["intended_notional"])) <= Decimal("5000000")
    assert events(higher.document, "unfilled")[0]["reason"] == "reservation_shortfall"


@pytest.mark.parametrize("pin", ["policy", "cost"])
def test_lock_mismatch(pin: str) -> None:
    r = runner()
    r = (
        replace(r, expected_policy_fingerprint="f" * 64)
        if pin == "policy"
        else replace(r, expected_cost_fingerprint="f" * 64)
    )
    with pytest.raises(ContractError, match=f"locked_{pin}_mismatch"):
        r.run()


def test_unsupported_settlement_is_atomic_failure() -> None:
    data = change(records(), 6, high=10300)
    data = tuple(
        replace(r, settlement_date=date(2027, 1, 1)) if r.session.trading_date.day == 6 else r
        for r in data
    )
    r = runner(data)
    before = checkpoint(r.initial)
    with pytest.raises(ContractError, match="BLOCKED_ON_COST_POLICY"):
        r.run()
    assert checkpoint(r.initial) == before


def test_missing_bar_and_unresolved_gap_remain_open() -> None:
    data = tuple(replace(r, bar=None) if r.session.trading_date.day == 7 else r for r in records())
    result = runner(data).run()
    assert events(result.document, "unsupported_exit")[0]["reason"] == "missing_bar"
    assert not events(result.document, "sell_fill")
    assert result.document.to_dict()["open_positions"] == ["000001"]
    assert result.document.to_dict()["equity_snapshots"][-1]["equity"] is None
    gapped = runner(change(records(), 7, open=10300, high=10400)).run()
    assert events(gapped.document, "unsupported_exit")[0]["reason"] == "gap_through_barrier"


def test_no_next_session_and_final_position_not_forced_out() -> None:
    r = runner(records(days=(3,)))
    result = r.run()
    assert not events(result.document, "reservation")
    assert events(result.document, "candidate_assessment")[0]["reason"] == "missing_next_session"
    short = runner(records(days=(3, 6))).run()
    assert not events(short.document, "sell_fill")
    assert short.document.to_dict()["open_positions"] == ["000001"]


def test_fingerprint_input_order_and_precision() -> None:
    r = runner()
    result = r.run()
    with localcontext() as context:
        context.prec = 4
        assert r.run().fingerprint == result.fingerprint
    changed = runner(change(records(), 7, volume=999999)).run()
    assert changed.fingerprint != result.fingerprint
    with pytest.raises(ContractError, match="noncanonical"):
        replace(r, records=tuple(reversed(r.records))).run()


def test_volume_cannot_rescue_reservation_overrun() -> None:
    # One share would be affordable, but all 499 intended shares exceed the locked reservation.
    r = runner(change(records(), 6, open=10100, high=10200, volume=100))
    result = r.run()
    assert not events(result.document, "buy_fill")
    assert events(result.document, "unfilled")[0]["reason"] == "reservation_shortfall"


def test_gap_down_does_not_increase_intended_quantity() -> None:
    result = runner(change(records(days=(3, 6)), 6, open=9900, low=9850)).run()
    assert events(result.document, "reservation")[0]["quantity"] == 499
    assert events(result.document, "buy_fill")[0]["quantity"] == 499
    assert result.ledger.to_dict()["reserved_cash_krw"] == "0"


def test_actual_price_and_commission_rounding() -> None:
    data = tuple(
        replace(r, bar=replace(r.bar, open=10001, close=10001))
        for r in records(days=(3, 6))
        if r.bar
    )
    result = runner(data).run()
    fill = next(iter(result.ledger.to_dict()["fills"].values()))
    assert fill["price_krw"] == "10012"
    assert fill["quantity"] == 499
    assert fill["fee_krw"] == "750"


def test_cash_reservations_are_bounded_before_fills() -> None:
    result = runner(records(22, (3, 6))).run()
    reservations = events(result.document, "reservation")
    assert sum(Decimal(str(e["reserved_cash"])) for e in reservations) <= Decimal("100000000")
    for state in [restore(checkpoint(result.ledger)), result.ledger]:
        assert Decimal(state.to_dict()["available_cash_krw"]) >= 0
    assert all(Decimal(str(e["equity"])) == Decimal("100000000") for e in reservations)


def test_tax_uses_supplied_settlement_date_across_year_boundary() -> None:
    original = records(days=(3, 6))
    data = []
    for row, day in zip(original, (date(2025, 12, 26), date(2025, 12, 29)), strict=True):
        assert row.bar is not None
        session = replace(
            row.session,
            trading_date=day,
            open_at=datetime.combine(day, time(9), SEOUL),
            close_at=datetime.combine(day, time(15, 30), SEOUL),
            bar_available_at=datetime.combine(day + timedelta(days=1), time.min, SEOUL),
        )
        data.append(
            replace(
                row,
                session=session,
                bar=replace(
                    row.bar,
                    trading_date=day,
                    collected_at=session.bar_available_at,
                    high=10300 if day.day == 29 else 10050,
                ),
                settlement_date=date(2026, 1, 2),
            )
        )
    result = runner(tuple(data)).run()
    sell = events(result.document, "sell_fill")[0]
    assert sell["tax"] == "2544" and sell["levy"] == "7633"


def test_no_trade_quality_exit_and_missing_settlement() -> None:
    result = runner(change(records(), 7, open=0, high=0, low=0, volume=0, trading_value=0)).run()
    assert result.document.to_dict()["status"] == "incomplete"
    assert not events(result.document, "sell_fill")
    data = tuple(
        replace(r, settlement_date=None, settlement_evidence_id=None)
        if r.session.trading_date.day == 6
        else r
        for r in change(records(), 6, high=10300)
    )
    with pytest.raises(ContractError, match="settlement_evidence_missing"):
        runner(data).run()


def test_missing_explicit_next_session_no_retry() -> None:
    data = tuple(replace(r, bar=None) if r.session.trading_date.day == 6 else r for r in records())
    result = runner(data).run()
    assert not events(result.document, "buy_fill")
    assert events(result.document, "unfilled")[0]["reason"] == "next_bar_unavailable"


def test_no_extra_signal_when_held_and_reentry_after_exit() -> None:
    scores = (
        ("000001", "2025-01-03", 0.8),
        ("000001", "2025-01-06", 0.9),
        ("000001", "2025-01-10", 0.8),
    )
    result = runner(scores=scores).run()
    assert len(events(result.document, "reservation")) == 2
    assert len(events(result.document, "buy_fill")) == 2
    assert len(events(result.document, "sell_fill")) == 1
    assert any(e["ticker"] == "000001" for e in events(result.document, "signal_ignored"))


def test_mismatched_dataset_and_old_policy_pin_fail_closed() -> None:
    r = runner()
    with pytest.raises(ContractError, match="dataset_manifest_mismatch"):
        replace(r, dataset_fingerprint="d" * 64).run()
    with pytest.raises(ContractError, match="locked_policy_mismatch"):
        replace(
            r,
            expected_policy_fingerprint="5686cc5c347babec80b5c92caff542362cbf7de523a8c57fc047316d0f163e3f",
        ).run()
    assert r.policy.to_dict()["rules"]["planning_rule"] == PLANNING


def test_after_end_and_same_day_ordering_fail_closed() -> None:
    r = runner()
    with pytest.raises(ContractError, match="after_end_input"):
        replace(r, contract=BASELINE.contract(date(2025, 1, 3), date(2025, 1, 10))).run()
    invalid = list(r.records)
    invalid[1] = replace(invalid[1], session=replace(invalid[1].session, sequence=0))
    with pytest.raises(ContractError, match="nonincreasing_sessions"):
        replace(r, records=tuple(invalid)).run()


def test_quality_blocked_signal_is_no_intent() -> None:
    from donghak_stock_vision.models.inference import ResearchPolicy

    @dataclass(frozen=True)
    class Blocked(Signals):
        def research(
            self, prefix: tuple[BaselineSession, ...], as_of: datetime
        ) -> BaselineSignalFrame:
            frame = super().research(prefix, as_of)
            context = replace(frame.signal.context, quality_flags=("unapproved",))
            signal = build_signal(
                context,
                self.policy,
                up_model=self.up_artifact.fingerprint,
                down_model=DOWN,
                up=None,
                down=None,
            )
            return replace(frame, signal=signal)

    source = Blocked(artifact(), SignalPolicy("blocked", ResearchPolicy("strict", frozenset())), ())
    result = replace(runner(), signals=source).run()
    assert not events(result.document, "reservation")
    assert all(
        e["reason"] == "quality_blocked" for e in events(result.document, "signal_unavailable")
    )


def test_public_hgb_adapter_uses_prefix_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.baseline_inputs import HGBBaselineSignals
    from donghak_stock_vision.models.inference import ResearchPolicy
    from tests.learning.helpers import CUTOFF, bars
    from tests.learning.test_inference import StubEstimator
    from tests.learning.test_signal_integration import bundle

    resolver = bundle(tmp_path / "trusted-fixture")
    monkeypatch.setattr("sklearn.ensemble.HistGradientBoostingClassifier", StubEstimator)
    source_bars = bars(15, tickers=1)
    rows = []
    for i, b in enumerate(source_bars):
        s = ObservedSession(
            b.ticker,
            b.trading_date,
            i,
            datetime.combine(b.trading_date, time(9), SEOUL),
            datetime.combine(b.trading_date, time(15, 30), SEOUL),
            datetime.combine(b.trading_date + timedelta(days=1), time.min, SEOUL),
        )
        rows.append(BaselineSession(s, b, QUALITY, None, None))
    flags = frozenset(
        {
            "universe_incomplete",
            "revision_history_unavailable",
            "retrospective_revisions_possible",
            "corporate_actions_unverified",
            "calendar_unverified",
        }
    )
    policy = SignalPolicy("adapter-fixture", ResearchPolicy("fixture-allowlist", flags))
    adapter = HGBBaselineSignals(resolver, policy, CUTOFF, None)
    prefix = tuple(rows[:12])
    frame = adapter.research(prefix, prefix[-1].session.bar_available_at)
    assert frame == adapter.research(prefix, prefix[-1].session.bar_available_at)
    assert frame.signal.context.data_cutoff == prefix[-1].session.bar_available_at
    with pytest.raises(ContractError, match="future_signal_prefix"):
        adapter.research(tuple(rows), prefix[-1].session.bar_available_at)
    # The strict locked allowlist does not silently waive production research flags.
    strict = HGBBaselineSignals(resolver, POLICY, CUTOFF, None)
    blocked = strict.research(prefix, prefix[-1].session.bar_available_at)
    assert blocked.signal.up.status == "quality_blocked"


def test_prefix_store_read_only() -> None:
    from donghak_stock_vision.backtest.baseline_inputs import PrefixMarketStore

    source = tuple(r.bar for r in records() if r.bar)
    store = PrefixMarketStore(source)
    assert store.latest("000001", date(2025, 1, 3), date(2025, 1, 6)) == date(2025, 1, 6)
    with pytest.raises(ValueError, match="read_only_prefix"):
        store.save([], [])
