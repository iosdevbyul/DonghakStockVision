"""Bounded synthetic forward-signal composition; original broker owns all settlement.

Source journals are sealed/pinned inputs. No retrospective or provider authorization
is granted here. Every read replays original source evidence and broker commands.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import (
    fields,
    flag,
    integer,
    money,
    require,
    sha,
    text,
    utc,
)
from donghak_stock_vision.storage.forward_signal import request_contract, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import history
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    CashBalance,
    Currency,
    Exchange,
    Instrument,
    Market,
    Money,
    OrderIntent,
    OrderSide,
    OrderType,
    Quantity,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    CurrencyMechanics,
    FillEvent,
    FixturePolicy,
    MarketSession,
    PaperFrame,
    exact,
    wire,
)
from donghak_stock_vision.trading.risk import MarketQuote, PortfolioValuation, RiskPolicy

INSTRUMENT = Instrument("005930", Exchange.KOSPI, Currency.KRW)
MAX_ACTIONS = 32


def amount(value: Any) -> Money:
    return Money(Decimal(money(value, signed=True)), Currency.KRW)


def initial(config: FrozenJSON, pin: str) -> DeterministicPaperBroker:
    require(config.identifier == sha(pin), "paper_configuration_pin_mismatch")
    require(len(config.payload_json.encode()) <= 8192, "paper_configuration_budget")
    provider = config.to_dict().get("version") == "forward_paper_account_v2"
    d = fields(
        config.to_dict(),
        "version mode account_id initial_cash initial_at risk mechanics"
        + (" authorization_trust" if provider else ""),
    )
    require(
        (d["version"] == "forward_paper_account_v1" and d["mode"] == "PAPER_SYNTHETIC")
        or (provider and d["mode"] == "PAPER_AUTHORIZED_CAPTURE"),
        "explicit_synthetic_paper_configuration_required",
    )
    r = fields(
        d["risk"],
        "policy_id max_order_notional max_position_fraction max_exposure_fraction "
        "max_daily_loss max_price_age_seconds max_fx_age_seconds max_account_age_seconds "
        "max_valuation_age_seconds risk_timezone",
    )
    policy = RiskPolicy(
        text(r["policy_id"]),
        Currency.KRW,
        frozenset({Market.KR}),
        amount(r["max_order_notional"]),
        Decimal(money(r["max_position_fraction"])),
        Decimal(money(r["max_exposure_fraction"])),
        amount(r["max_daily_loss"]),
        r["max_price_age_seconds"],
        r["max_fx_age_seconds"],
        r["max_account_age_seconds"],
        r["max_valuation_age_seconds"],
        r["risk_timezone"],
    )
    m = fields(
        d["mechanics"],
        "policy_id price_quantum cost_quantum buy_fee_rate sell_fee_rate "
        "sell_tax_rate slippage_rate max_session_age_seconds max_commands",
    )
    require(
        type(m["max_commands"]) is int and 1 <= m["max_commands"] <= 128, "paper_command_budget"
    )
    mechanics = FixturePolicy(
        text(m["policy_id"]),
        (policy,),
        (
            CurrencyMechanics(
                Currency.KRW, Decimal(money(m["price_quantum"])), Decimal(money(m["cost_quantum"]))
            ),
        ),
        Decimal(money(m["buy_fee_rate"])),
        Decimal(money(m["sell_fee_rate"])),
        Decimal(money(m["sell_tax_rate"])),
        Decimal(money(m["slippage_rate"])),
        m["max_session_age_seconds"],
        m["max_commands"],
    )
    at = utc(d["initial_at"])
    require(at.isoformat() == d["initial_at"], "canonical_utc_required")
    account = AccountSnapshot(
        text(d["account_id"]),
        (CashBalance(amount(d["initial_cash"]), amount("0")),),
        (),
        at,
        at,
        pin,
    )
    return DeterministicPaperBroker(account, mechanics, PaperFrame(pin, at, (), (), (), (), False))


def source(job: FrozenJSON, *, authorization_trust: FrozenJSON | None = None) -> dict[str, Any]:
    require(len(job.payload_json.encode()) <= 256 * 1024, "paper_action_byte_budget")
    d = fields(
        job.to_dict(),
        "version mode capture_root request request_pin signal_journal "
        "signal_journal_pin signal_id quantity at fill_at session_open trading_enabled daily_pnl",
    )
    require(
        (d["version"] == "forward_paper_action_v1" and d["mode"] == "PAPER_SYNTHETIC")
        or (d["version"] == "forward_paper_action_v2" and d["mode"] == "PAPER_AUTHORIZED_CAPTURE"),
        "prospective_synthetic_signal_required",
    )
    request = FrozenJSON.freeze(d["request"])
    request_contract(request, d["request_pin"])  # Protected partitions checked before file I/O.
    provider = d["version"] == "forward_paper_action_v2"
    if provider:
        require(
            authorization_trust is not None
            and d["request"]["version"] == "forward_signal_request_v2",
            "independent_provider_paper_configuration_required",
        )
    else:
        require(d["request"]["mode"] == "offline_fixture", "provider_execution_not_authorized")
    integer(d["quantity"], positive=True)
    require(d["quantity"] <= 10000, "position_sizing_budget")
    flag(d["session_open"])
    flag(d["trading_enabled"])
    amount(d["daily_pnl"])
    at, fill_at = utc(d["at"]), utc(d["fill_at"])
    require(
        at.isoformat() == d["at"] and fill_at.isoformat() == d["fill_at"], "canonical_utc_required"
    )
    require(
        utc(d["request"]["calculated_at"]) <= at < fill_at <= at + timedelta(seconds=60),
        "invalid_simulated_execution_clock",
    )
    require(
        (at - utc(d["request"]["calculated_at"])).total_seconds()
        <= d["request"]["max_latest_age_seconds"],
        "stale_signal_execution",
    )
    records = history(Path(d["signal_journal"]), d["signal_journal_pin"], limit=256).to_dict()
    selected = [
        e["entry"]
        for e in records["entries"]
        if e["entry"]["decision"]["signal_id"] == sha(d["signal_id"])
    ]
    require(len(selected) == 1, "source_signal_not_found")
    entry = selected[0]
    require(utc(entry["inserted_at"]) <= at, "execution_before_signal_journaling")
    decision = FrozenJSON.freeze(entry["decision"])
    verify_signal(
        Path(d["capture_root"]),
        request,
        d["request_pin"],
        decision,
        entry["decision_pin"],
        authorization_trust=authorization_trust,
    )
    verified = decision.to_dict()
    if provider:
        from donghak_stock_vision.storage.krx_authorization import (
            review,
            review_time,
            verify_authorization,
        )

        review_time(
            review(FrozenJSON.freeze(d["request"]["authorization"]), authorization_trust), at
        )

        for plan, item in zip(
            request_contract(request, d["request_pin"]), d["request"]["sources"], strict=True
        ):
            verify_authorization(
                plan,
                item["attempt"],
                FrozenJSON.freeze(d["request"]["authorization"]),
                authorization_trust,
                at,
            )
    observations = verified["inputs"]["observations"]
    if observations:
        require(
            (at - utc(observations[-1]["local_capture_at"])).total_seconds()
            <= d["request"]["max_latest_age_seconds"],
            "capture_stale_at_paper_execution",
        )
    return verified


def portfolio(broker: DeterministicPaperBroker, price: Money | None = None) -> dict[str, Any]:
    account = broker.account(broker.checkpoint().initial_account.account_id)
    with exact():
        cash = sum((b.available.amount + b.reserved.amount for b in account.balances), Decimal(0))
        exposure = (
            sum((p.quantity.shares * price.amount for p in account.positions), Decimal(0))
            if price
            else None
        )
        equity = cash + exposure if exposure is not None else None
        fees = sum((f.fees.amount for f in broker.fills()), Decimal(0))
        taxes = sum((f.taxes.amount for f in broker.fills()), Decimal(0))
    return {
        "account": wire(account),
        "broker_state_id": broker.state_id,
        "marked_equity": format(equity, "f") if equity is not None else None,
        "position_market_value": format(exposure, "f") if exposure is not None else None,
        "mark_assurance": "explicit_synthetic_fill_reference" if price else "mark_unavailable",
        "recorded_fees": format(fees, "f"),
        "recorded_taxes": format(taxes, "f"),
        "average_entry_cost": None,
        "realized_pnl": None,
        "unavailable_reason": "broker_snapshot_has_no_cost_basis_or_realized_pnl",
    }


def process(
    broker: DeterministicPaperBroker,
    job: FrozenJSON,
    *,
    authorization_trust: FrozenJSON | None = None,
) -> tuple[DeterministicPaperBroker, FrozenJSON]:
    decision = source(job, authorization_trust=authorization_trust)
    d = job.to_dict()
    at, fill_at = utc(d["at"]), utc(d["fill_at"])
    require(at >= broker.checkpoint().initial_frame.now, "clock_before_account")
    current = broker.account(broker.checkpoint().initial_account.account_id)
    require(at >= current.available_at, "paper_clock_regression")
    signal = decision["calculation"]["signal"]
    observations = decision["inputs"]["observations"]
    price = amount(str(observations[-1]["bar"]["close"])) if observations else None
    result: dict[str, Any] = {
        "version": "forward_paper_result_v2"
        if d["version"] == "forward_paper_action_v2"
        else "forward_paper_result_v1",
        "mode": "PAPER",
        "origin": decision["origin"],
        "signal_id": decision["signal_id"],
        "decision_pin": FrozenJSON.freeze(decision).identifier,
        "input_fingerprint": decision["input_fingerprint"],
        "signal": signal,
        "latest_trading_date": decision["latest_trading_date"],
        "calculated_at": decision["calculated_at"],
        "before": portfolio(broker, price),
        "mark": {
            "price": wire(price),
            "source_observation_id": observations[-1]["observation_id"],
            "synthetic_valuation_at": at.isoformat(),
        }
        if observations
        else None,
        "risk": None,
        "order": None,
        "submission": None,
        "execution": None,
        "outcome": "no_order",
        "reason": decision["calculation"]["reason"],
        "assumptions": "synthetic_close_reference_and_later_full_fill_not_exchange_execution",
        "operational_execution_authorized": False,
    }
    if signal in ("BUY", "SELL"):
        require(price is not None, "execution_reference_missing")
        assert price is not None
        candidate = DeterministicPaperBroker.restore(broker.checkpoint())
        policy = candidate.checkpoint().policy.risk_policies[0]
        with exact():
            equity = sum(
                (b.available.amount + b.reserved.amount for b in current.balances), Decimal(0)
            ) + sum((p.quantity.shares * price.amount for p in current.positions), Decimal(0))
        require(equity > 0, "positive_paper_equity_required")

        def frame(now: Any, suffix: str) -> PaperFrame:
            identity = job.identifier + suffix
            quote = MarketQuote(INSTRUMENT, price, now, now, decision["input_fingerprint"])
            valuation = PortfolioValuation(
                current.account_id,
                Money(equity, Currency.KRW),
                amount(d["daily_pnl"]),
                now.astimezone(ZoneInfo(policy.risk_timezone)).date(),
                now,
                now,
                identity,
            )
            return PaperFrame(
                identity,
                now,
                (quote,),
                (),
                (valuation,),
                (MarketSession(Exchange.KOSPI, d["session_open"], now, now, identity),),
                d["trading_enabled"],
            )

        candidate.advance(frame(at, "-accept"))
        order = OrderIntent(
            decision["signal_id"],
            current.account_id,
            INSTRUMENT,
            OrderSide(signal),
            OrderType.MARKET,
            Quantity(d["quantity"]),
            at,
            decision["signal_id"],
        )
        risk = candidate.preflight(order)
        result["risk"] = risk.to_dict()
        if not risk.allowed:
            result.update(outcome="risk_rejected", reason="preflight_denied")
        else:
            result["order"] = wire(order)
            accepted = candidate.submit(
                order, idempotency_key=order.order_id, risk=risk.to_decision()
            )
            result["submission"] = accepted.to_dict()
            if accepted.outcome == "accepted":
                candidate.advance(frame(fill_at, "-fill"))
                executed = candidate.execute(
                    FillEvent(
                        job.identifier,
                        order.order_id,
                        order.quantity,
                        fill_at,
                        decision["input_fingerprint"],
                    )
                )
                result["execution"] = executed.to_dict()
                result.update(outcome=executed.outcome, reason=list(executed.reasons))
            else:
                result.update(outcome="rejected", reason=list(accepted.reasons))
            broker = candidate
        result["after"] = portfolio(broker, price)
    else:
        require(signal in ("HOLD", "INSUFFICIENT_DATA"), "unsupported_signal")
        result["after"] = portfolio(broker, price)
    result["checkpoint_state_id"] = broker.state_id
    return broker, FrozenJSON.freeze(result)


def paper_trust(config: FrozenJSON) -> FrozenJSON | None:
    # The separately pinned account configuration owns trust, not the signal report.
    d = config.to_dict()
    return (
        FrozenJSON.freeze(d["authorization_trust"])
        if d.get("version") == "forward_paper_account_v2"
        else None
    )
