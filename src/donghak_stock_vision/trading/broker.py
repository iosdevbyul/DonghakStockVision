"""Ports only. No implementation, authentication, live submission or approval."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

from donghak_stock_vision.backtest.validation import require, text
from donghak_stock_vision.trading.domain import AccountSnapshot, OrderIntent, OrderStatus


class ExecutionMode(Enum):
    PAPER = "paper"
    LIVE = "live"


class DenialReason(Enum):
    ELIGIBILITY_UNKNOWN = "eligibility_unknown"
    ELIGIBILITY_DENIED = "eligibility_denied"
    EVIDENCE_MISSING = "eligibility_evidence_missing"
    LIVE_DISABLED = "live_execution_disabled"
    RISK_LIMIT = "risk_limit_exceeded"


@dataclass(frozen=True)
class RiskDecision:
    """Paper-only assessment. Cannot confer operational/governance approval."""

    order_id: str
    mode: ExecutionMode = ExecutionMode.PAPER
    allowed: bool = False
    reasons: tuple[DenialReason, ...] = (DenialReason.ELIGIBILITY_UNKNOWN,)
    evidence_id: str | None = None
    order: OrderIntent | None = None

    def __post_init__(self) -> None:
        text(self.order_id)
        require(isinstance(self.mode, ExecutionMode), "invalid_execution_mode")
        require(type(self.allowed) is bool, "boolean_required")
        require(type(self.reasons) is tuple, "immutable_reasons_required")
        require(all(isinstance(reason, DenialReason) for reason in self.reasons), "invalid_denial")
        require(len(set(self.reasons)) == len(self.reasons), "duplicate_denial")
        if self.evidence_id is not None:
            text(self.evidence_id)
        require(self.allowed == (not self.reasons), "risk_reasons_mismatch")
        require(not self.allowed or self.evidence_id is not None, "risk_evidence_required")
        require(not self.allowed or self.mode is ExecutionMode.PAPER, "live_execution_disabled")
        require(
            self.mode is not ExecutionMode.LIVE or DenialReason.LIVE_DISABLED in self.reasons,
            "live_denial_required",
        )

        if self.order is not None:
            require(isinstance(self.order, OrderIntent), "order_intent_required")
            require(self.order_id == self.order.order_id, "risk_order_mismatch")
        require(not self.allowed or self.order is not None, "risk_order_binding_required")

    def validate_for(self, order: OrderIntent) -> None:
        require(isinstance(order, OrderIntent), "order_intent_required")
        require(self.order_id == order.order_id, "risk_order_mismatch")
        require(self.allowed and self.mode is ExecutionMode.PAPER, "risk_denied")
        require(self.order == order, "risk_order_mismatch")


@runtime_checkable
class BrokerReader(Protocol):
    """Read boundary shared by future paper/live adapters; reads cannot submit orders."""

    @property
    def mode(self) -> ExecutionMode: ...

    def account(self, account_id: str) -> AccountSnapshot: ...

    def order_status(self, account_id: str, order_id: str) -> OrderStatus: ...


@runtime_checkable
class PaperBroker(BrokerReader, Protocol):
    """Future paper adapter must validate risk binding and durable idempotency before acceptance.

    No concrete adapter is shipped. This protocol is never a live broker port.
    """

    @property
    def mode(self) -> Literal[ExecutionMode.PAPER]: ...

    def submit_paper(self, order: OrderIntent, risk: RiskDecision) -> OrderStatus: ...
