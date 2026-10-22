"""Bounded, explicit synthetic paper inputs composed from existing domain owners."""

from dataclasses import dataclass
from typing import Any, ClassVar

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.validation import require, text
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    Currency,
    Instrument,
    Market,
    OrderIntent,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    Cancellation,
    FillEvent,
    FixturePolicy,
    PaperCommand,
    PaperFrame,
    Submission,
    validate_owner,
    wire,
)


@dataclass(frozen=True)
class PaperScenario:
    """Native single-currency scenario. No paths, providers, random liquidity or grants."""

    scenario_id: str
    market: Market
    currency: Currency
    initial_account: AccountSnapshot
    policy: FixturePolicy
    initial_frame: PaperFrame
    commands: tuple[PaperCommand, ...]
    version: ClassVar[str] = "synthetic_paper_scenario_v1"

    def __post_init__(self) -> None:
        require(len(text(self.scenario_id)) <= 128, "scenario_id_too_long")
        require(isinstance(self.market, Market), "market_required")
        require(
            self.currency is (Currency.KRW if self.market is Market.KR else Currency.USD),
            "scenario_market_currency_mismatch",
        )
        require(type(self.initial_account) is AccountSnapshot, "account_required")
        require(type(self.policy) is FixturePolicy, "policy_required")
        require(type(self.initial_frame) is PaperFrame, "initial_frame_required")
        require(type(self.commands) is tuple, "immutable_scenario_commands_required")
        require(0 < len(self.commands) <= 64, "scenario_command_limit")
        require(
            len(self.commands) <= self.policy.max_commands <= 64, "scenario_policy_command_limit"
        )
        require(
            len(self.initial_account.balances) == 1
            and self.initial_account.balances[0].available.currency is self.currency,
            "native_settlement_only",
        )
        require(
            len(self.policy.currencies) == len(self.policy.risk_policies) == 1
            and self.policy.currencies[0].currency is self.currency,
            "native_policy_only",
        )
        require(len(self.initial_account.positions) <= 16, "scenario_position_limit")
        frames = [self.initial_frame]
        for command in self.commands:
            require(type(command) is PaperCommand, "paper_command_required")
            text(command.key)
            payload = command.payload
            if command.kind == "submit":
                require(type(payload) is Submission, "submission_required")
                assert isinstance(payload, Submission)
                require(type(payload.order) is OrderIntent, "order_intent_required")
                require(payload.risk is None, "scenario_cannot_supply_risk_grant")
                require(command.key == payload.idempotency_key, "command_key_mismatch")
                require(
                    payload.order.account_id == self.initial_account.account_id,
                    "order_account_mismatch",
                )
            elif command.kind == "advance":
                require(type(payload) is PaperFrame, "frame_required")
                assert isinstance(payload, PaperFrame)
                require(command.key == payload.frame_id, "command_key_mismatch")
                frames.append(payload)
            elif command.kind == "fill":
                require(type(payload) is FillEvent, "fill_event_required")
                assert isinstance(payload, FillEvent)
                require(command.key == payload.event_id, "command_key_mismatch")
            elif command.kind == "cancel":
                require(type(payload) is Cancellation, "cancellation_required")
                assert isinstance(payload, Cancellation)
                require(command.key == payload.idempotency_key, "command_key_mismatch")
            else:
                raise ValueError("unsupported_scenario_command")
        for frame in frames:
            require(not frame.fx_rates, "scenario_fx_settlement_unsupported")
            require(len(frame.quotes) <= 16 and len(frame.sessions) <= 4, "scenario_frame_limit")
            require(len(frame.valuations) <= 1, "scenario_valuation_limit")
            require(
                all(s.exchange.market is self.market for s in frame.sessions),
                "scenario_session_market_mismatch",
            )
            require(
                all(v.equity.currency is self.currency for v in frame.valuations),
                "scenario_valuation_currency_mismatch",
            )
        require(len(self.instruments) <= 16, "scenario_instrument_limit")
        require(
            all(i.market is self.market and i.currency is self.currency for i in self.instruments),
            "scenario_instrument_mismatch",
        )
        # Existing constructors validate canonical nested owners, initial holds and accounting.
        for value in (self.initial_account, self.policy, self.initial_frame, self.commands):
            validate_owner(value)
        DeterministicPaperBroker(self.initial_account, self.policy, self.initial_frame)
        bounded_json(self.document, 256 * 1024)

    @property
    def instruments(self) -> tuple[Instrument, ...]:
        instruments = {p.instrument for p in self.initial_account.positions}
        frames = [self.initial_frame]
        for command in self.commands:
            if isinstance(command.payload, Submission):
                instruments.add(command.payload.order.instrument)
            elif isinstance(command.payload, PaperFrame):
                frames.append(command.payload)
        instruments.update(q.instrument for frame in frames for q in frame.quotes)
        return tuple(
            sorted(instruments, key=lambda i: (i.exchange.value, i.symbol, i.currency.value))
        )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": self.version,
                "simulated": True,
                "paper_policy_version": self.policy.version,
                **wire(self),
            }
        )

    @property
    def fingerprint(self) -> str:
        return self.document.identifier

    def reference(self) -> dict[str, Any]:
        return {
            "artifact_id": self.scenario_id,
            "version": self.version,
            "content_hash": self.fingerprint,
        }
