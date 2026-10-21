"""In-memory deterministic paper adapter. Explicit fixtures only; no live route or I/O."""

from dataclasses import replace
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from threading import RLock
from typing import Literal

from donghak_stock_vision.backtest.validation import integer, require, sha, text, utc
from donghak_stock_vision.trading.broker import ExecutionMode, RiskDecision
from donghak_stock_vision.trading.domain import (
    AccountSnapshot,
    CashBalance,
    Currency,
    FillReceipt,
    Instrument,
    Money,
    OrderIntent,
    OrderSide,
    OrderStatus,
    Position,
    Quantity,
    validate_fills,
    validate_transition,
)
from donghak_stock_vision.trading.paper_types import (
    Cancellation,
    CurrencyMechanics,
    FillEvent,
    FixturePolicy,
    PaperCheckpoint,
    PaperCommand,
    PaperFrame,
    PaperOrder,
    PaperReceipt,
    PaperState,
    RecordedCommand,
    Submission,
    exact,
    fingerprint,
    rounded,
    validate_owner,
)
from donghak_stock_vision.trading.risk import RiskAssessment, RiskContext, evaluate_risk

OPEN = {OrderStatus.OPEN, OrderStatus.PARTIAL}
TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


class DeterministicPaperBroker:
    """One simulated account, serialized atomic commands and bounded replay checkpoint.

    Caller-supplied risk decisions are evidence to compare, never trusted grants.
    Every new intent is evaluated internally before acceptance; fills re-evaluate
    remaining risk against the current virtual account with its own hold restored.
    """

    def __init__(self, account: AccountSnapshot, policy: FixturePolicy, frame: PaperFrame) -> None:
        for value in (account, policy, frame):
            validate_owner(value)
        require(isinstance(account, AccountSnapshot), "account_required")
        require(
            isinstance(policy, FixturePolicy) and isinstance(frame, PaperFrame),
            "paper_inputs_required",
        )
        require(account.available_at <= frame.now, "future_initial_account")
        require(bool(account.balances), "initial_balances_required")
        require(
            all(b.reserved.amount == 0 for b in account.balances), "initial_cash_holds_unsupported"
        )
        require(
            all(p.reserved.shares == 0 and p.sellable == p.quantity for p in account.positions),
            "initial_share_holds_unsupported",
        )
        require(
            {b.available.currency for b in account.balances}
            <= {c.currency for c in policy.currencies},
            "unsupported_initial_currency",
        )
        self._initial, self._policy, self._initial_frame = account, policy, frame
        self._lock = RLock()
        state = PaperState(account, frame, (), (), (), 0)
        self._head = (state, self._identity(state))
        self._check()

    @property
    def simulated(self) -> Literal[True]:
        return True

    @property
    def mode(self) -> Literal[ExecutionMode.PAPER]:
        return ExecutionMode.PAPER

    def _identity(self, state: PaperState) -> str:
        return fingerprint(
            (
                PaperCheckpoint.version,
                FixturePolicy.version,
                self._initial,
                self._policy,
                self._initial_frame,
                state,
            )
        )

    @property
    def state_id(self) -> str:
        with self._lock:
            self._check()
            return self._head[1]

    @property
    def revision(self) -> int:
        with self._lock:
            self._check()
            return self._head[0].revision

    def account(self, account_id: str) -> AccountSnapshot:
        with self._lock:
            self._check()
            state = self._head[0]
            require(account_id == state.account.account_id, "unknown_account")
            # This is an explicit virtual observation at the supplied clock, not
            # a claim about original market/broker observation timestamps.
            return replace(
                state.account,
                observed_at=state.frame.now,
                available_at=state.frame.now,
                source_id=self._head[1],
            )

    def order_status(self, account_id: str, order_id: str) -> OrderStatus:
        with self._lock:
            self._check()
            require(account_id == self._head[0].account.account_id, "unknown_account")
            record = next((o for o in self._head[0].orders if o.intent.order_id == order_id), None)
            return record.status if record else OrderStatus.UNKNOWN

    def order(self, order_id: str) -> PaperOrder:
        with self._lock:
            self._check()
            return self._find(self._head[0], order_id)

    def fills(self) -> tuple[FillReceipt, ...]:
        with self._lock:
            self._check()
            return self._head[0].fills

    @staticmethod
    def _find(state: PaperState, order_id: str) -> PaperOrder:
        record = next((o for o in state.orders if o.intent.order_id == order_id), None)
        require(record is not None, "unknown_order")
        assert record is not None
        return record

    def _mechanics(self, currency: Currency) -> CurrencyMechanics:
        value = next((m for m in self._policy.currencies if m.currency is currency), None)
        require(value is not None, "unsupported_paper_currency")
        assert value is not None
        return value

    def _bound(
        self, order: OrderIntent, state: PaperState
    ) -> tuple[Money, Money | None, Money | None]:
        """Per-share holds cover worst-case whole-share splitting of rounded fees."""
        currency = order.instrument.currency
        zero = Money(Decimal(0), currency)
        if order.side is OrderSide.SELL:
            return zero, None, zero
        quote = next((q for q in state.frame.quotes if q.instrument == order.instrument), None)
        if quote is None:
            return zero, None, None
        try:
            mechanics = self._mechanics(currency)
            cap = (
                order.limit_price.amount
                if order.limit_price
                else rounded(
                    quote.price.amount * (1 + self._policy.slippage_rate), mechanics.price_quantum
                )
            )
            unit = cap + rounded(cap * self._policy.buy_fee_rate, mechanics.cost_quantum)
            risk_notional = (
                max(
                    quote.price.amount,
                    order.limit_price.amount if order.limit_price else quote.price.amount,
                )
                * order.quantity.shares
            )
            buffer = max(Decimal(0), unit * order.quantity.shares - risk_notional)
            return Money(unit, currency), Money(cap, currency), Money(buffer, currency)
        except (ValueError, ArithmeticError):
            return zero, None, None

    def _assess(
        self, order: OrderIntent, state: PaperState, own: PaperOrder | None = None
    ) -> RiskAssessment:
        snapshot = self.account(state.account.account_id)
        if own is not None:
            # Its existing reservation is funding for this intent, not an extra
            # candidate. Other orders' reservations and pending buys stay intact.
            snapshot = self._cash(
                snapshot,
                order.instrument.currency,
                own.reserved_cash.amount,
                -own.reserved_cash.amount,
            )
            if own.reserved_quantity:
                snapshot = self._position(snapshot, order.instrument, 0, -own.reserved_quantity)
        pending = tuple(
            replace(o.intent, quantity=Quantity(o.remaining))
            for o in state.orders
            if o.status in OPEN and (own is None or o.intent.order_id != own.intent.order_id)
        )
        policy = next(
            (
                p
                for p in self._policy.risk_policies
                if p.account_currency is order.instrument.currency
            ),
            None,
        )
        _, _, buffer = self._bound(order, state)
        valuation = next(
            (v for v in state.frame.valuations if v.equity.currency is order.instrument.currency),
            None,
        )
        context = RiskContext(
            snapshot,
            order.instrument.currency,
            True,
            valuation,
            state.frame.quotes,
            state.frame.fx_rates,
            pending,
            buffer,
            state.frame.trading_enabled,
            state.frame.now,
        )
        return evaluate_risk(order, context, policy)

    def preflight(self, order: OrderIntent) -> RiskAssessment:
        with self._lock, exact():
            self._check()
            validate_owner(order)
            return self._assess(order, self._head[0])

    def submit(
        self, order: OrderIntent, *, idempotency_key: str, risk: RiskDecision | None
    ) -> PaperReceipt:
        result = self._apply(
            PaperCommand("submit", idempotency_key, Submission(order, idempotency_key, risk))
        )
        assert result is not None
        return result

    def submit_paper(self, order: OrderIntent, risk: RiskDecision) -> OrderStatus:
        """Existing protocol uses the explicit order ID as its submission key."""
        return self.submit(order, idempotency_key=order.order_id, risk=risk).order_status

    def advance(self, frame: PaperFrame) -> None:
        self._apply(PaperCommand("advance", frame.frame_id, frame))

    def execute(self, event: FillEvent) -> PaperReceipt:
        result = self._apply(PaperCommand("fill", event.event_id, event))
        assert result is not None
        return result

    def cancel(self, order_id: str, *, idempotency_key: str, at: datetime) -> PaperReceipt:
        result = self._apply(
            PaperCommand(
                "cancel", idempotency_key, Cancellation(order_id, idempotency_key, utc(at))
            )
        )
        assert result is not None
        return result

    def _apply(self, command: PaperCommand) -> PaperReceipt | None:
        with self._lock, exact():
            self._check()
            text(command.key)
            validate_owner(command.payload)
            state = self._head[0]
            if command.kind == "advance" and command.key == self._initial_frame.frame_id:
                require(command.payload == self._initial_frame, "idempotency_conflict")
                return None
            for recorded in state.records:
                if (recorded.command.kind, recorded.command.key) == (command.kind, command.key):
                    require(recorded.command == command, "idempotency_conflict")
                    return recorded.receipt
            cleanup = (
                command.kind == "cancel"
                and isinstance(command.payload, Cancellation)
                and any(
                    o.intent.order_id == command.payload.order_id and o.status in OPEN
                    for o in state.orders
                )
            )
            require(
                len(state.records) < self._policy.max_commands or cleanup, "paper_command_limit"
            )
            if command.kind == "advance":
                require(isinstance(command.payload, PaperFrame), "invalid_advance_payload")
                assert isinstance(command.payload, PaperFrame)
                require(command.key == command.payload.frame_id, "command_key_mismatch")
                require(command.payload.now >= state.frame.now, "clock_regression")
                updated, result = replace(state, frame=command.payload), None
            elif command.kind == "submit":
                require(isinstance(command.payload, Submission), "invalid_submission_payload")
                assert isinstance(command.payload, Submission)
                require(command.key == command.payload.idempotency_key, "command_key_mismatch")
                updated, result = self._submit(state, command.payload)
            elif command.kind == "fill":
                require(isinstance(command.payload, FillEvent), "invalid_fill_payload")
                assert isinstance(command.payload, FillEvent)
                require(command.key == command.payload.event_id, "command_key_mismatch")
                updated, result = self._execute(state, command.payload)
            elif command.kind == "cancel":
                require(isinstance(command.payload, Cancellation), "invalid_cancel_payload")
                assert isinstance(command.payload, Cancellation)
                require(command.key == command.payload.idempotency_key, "command_key_mismatch")
                updated, result = self._cancel(state, command.payload)
            else:
                raise ValueError("unsupported_paper_command")
            if result is not None:
                quote = next(
                    (
                        q
                        for q in updated.frame.quotes
                        if q.instrument == self._find(updated, result.order_id).intent.instrument
                    ),
                    None,
                )
                session = next(
                    (
                        s
                        for s in updated.frame.sessions
                        if s.exchange
                        == self._find(updated, result.order_id).intent.instrument.exchange
                    ),
                    None,
                )
                result = replace(
                    result,
                    frame_id=updated.frame.frame_id,
                    policy_id=self._policy.policy_id,
                    command_key=command.key,
                    quote_source_id=quote.source_id if quote is not None else None,
                    session_source_id=session.source_id if session is not None else None,
                )
            revision = state.revision + 1
            snapshot = replace(
                updated.account,
                observed_at=updated.frame.now,
                available_at=updated.frame.now,
                source_id=fingerprint((self._head[1], command, revision)),
            )
            updated = replace(
                updated,
                account=snapshot,
                revision=revision,
                records=(*state.records, RecordedCommand(command, result)),
            )
            self._validate_state(updated)
            identity = self._identity(updated)
            self._head = (
                updated,
                identity,
            )  # Only commit point; failures leave the old head intact.
            return result

    def _submit(self, state: PaperState, request: Submission) -> tuple[PaperState, PaperReceipt]:
        order = request.order
        require(isinstance(order, OrderIntent), "order_intent_required")
        require(
            all(o.intent.order_id != order.order_id for o in state.orders), "duplicate_order_id"
        )
        assessment = self._assess(
            order, state
        )  # Mandatory, even with missing/forged caller approval.
        reasons = tuple(r.code.value for r in assessment.rejections)
        decision = assessment.to_decision()
        if request.risk is None:
            reasons += ("risk_evidence_missing",)
        elif request.risk != decision:
            reasons += ("risk_evidence_mismatch",)
        unit, cap, _ = self._bound(order, state)
        mechanics = next(
            (m for m in self._policy.currencies if m.currency is order.instrument.currency), None
        )
        if mechanics is None:
            reasons += ("native_currency_unsupported",)
        elif order.limit_price is not None and order.limit_price.amount != rounded(
            order.limit_price.amount, mechanics.price_quantum
        ):
            reasons += ("limit_price_quantum_mismatch",)
        accepted = not reasons
        held = Money(
            unit.amount * order.quantity.shares if accepted else Decimal(0),
            order.instrument.currency,
        )
        account = state.account
        if accepted:
            if order.side is OrderSide.BUY:
                require(cap is not None, "missing_reservation_cap")
                account = self._cash(account, order.instrument.currency, -held.amount, held.amount)
            else:
                account = self._position(account, order.instrument, 0, order.quantity.shares)
        zero = Money(Decimal(0), order.instrument.currency)
        record = PaperOrder(
            order,
            OrderStatus.OPEN if accepted else OrderStatus.REJECTED,
            order.quantity.shares if accepted else 0,
            0,
            0 if accepted else order.quantity.shares,
            held if accepted else zero,
            order.quantity.shares if accepted and order.side is OrderSide.SELL else 0,
            unit,
            cap,
            state.frame.now if accepted else None,
            assessment,
            reasons,
        )
        validate_transition(OrderStatus.SUBMITTED, record.status)
        receipt = PaperReceipt(
            "submit",
            order.order_id,
            record.status,
            "accepted" if accepted else "rejected",
            reasons,
            state.frame.now,
            assessment,
        )
        return replace(state, account=account, orders=(*state.orders, record)), receipt

    @staticmethod
    def _cash(
        snapshot: AccountSnapshot, currency: Currency, available: Decimal, reserved: Decimal
    ) -> AccountSnapshot:
        balances = list(snapshot.balances)
        index = next((i for i, b in enumerate(balances) if b.available.currency is currency), None)
        require(index is not None, "native_cash_balance_missing")
        assert index is not None
        old = balances[index]
        balances[index] = CashBalance(
            Money(old.available.amount + available, currency),
            Money(old.reserved.amount + reserved, currency),
        )
        return replace(snapshot, balances=tuple(balances))

    @staticmethod
    def _position(
        snapshot: AccountSnapshot,
        instrument: Instrument,
        quantity_delta: int,
        reservation_delta: int,
    ) -> AccountSnapshot:
        positions = list(snapshot.positions)
        index = next((i for i, p in enumerate(positions) if p.instrument == instrument), None)
        if index is None:
            positions.append(Position(instrument, Quantity(0), Quantity(0), Quantity(0)))
            index = len(positions) - 1
        previous = positions[index]
        quantity = previous.quantity.shares + quantity_delta
        reserved = previous.reserved.shares + reservation_delta
        positions[index] = Position(
            instrument, Quantity(quantity), Quantity(quantity - reserved), Quantity(reserved)
        )
        return replace(snapshot, positions=tuple(positions))

    @staticmethod
    def _replace_order(state: PaperState, order: PaperOrder) -> PaperState:
        return replace(
            state,
            orders=tuple(
                order if old.intent.order_id == order.intent.order_id else old
                for old in state.orders
            ),
        )

    def _release(
        self, state: PaperState, order: PaperOrder, rejected: bool, reasons: tuple[str, ...]
    ) -> PaperState:
        account = state.account
        if order.reserved_cash.amount:
            account = self._cash(
                account,
                order.intent.instrument.currency,
                order.reserved_cash.amount,
                -order.reserved_cash.amount,
            )
        if order.reserved_quantity:
            account = self._position(account, order.intent.instrument, 0, -order.reserved_quantity)
        status = OrderStatus.REJECTED if rejected and order.filled == 0 else OrderStatus.CANCELLED
        validate_transition(order.status, status)
        updated = replace(
            order,
            status=status,
            remaining=0,
            closed=order.closed + order.remaining,
            reserved_cash=Money(Decimal(0), order.intent.instrument.currency),
            reserved_quantity=0,
            reasons=reasons,
        )
        return self._replace_order(replace(state, account=account), updated)

    def _cancel(self, state: PaperState, request: Cancellation) -> tuple[PaperState, PaperReceipt]:
        text(request.order_id)
        require(request.at == state.frame.now, "command_clock_mismatch")
        order = self._find(state, request.order_id)
        if order.status in TERMINAL:
            return state, PaperReceipt(
                "cancel", request.order_id, order.status, "unchanged", (), state.frame.now
            )
        updated = self._release(state, order, False, ("explicit_cancellation",))
        return updated, PaperReceipt(
            "cancel",
            request.order_id,
            OrderStatus.CANCELLED,
            "cancelled",
            ("explicit_cancellation",),
            state.frame.now,
        )

    def _execute(self, state: PaperState, event: FillEvent) -> tuple[PaperState, PaperReceipt]:
        require(event.at == state.frame.now, "command_clock_mismatch")
        order = self._find(state, event.order_id)
        require(order.status in OPEN, "order_not_open")
        require(event.quantity.shares <= order.remaining, "overfill")
        assert order.accepted_at is not None
        reasons: tuple[str, ...] = ()
        if event.at <= order.accepted_at:
            reasons += ("later_execution_event_required",)
        session = next(
            (s for s in state.frame.sessions if s.exchange is order.intent.instrument.exchange),
            None,
        )
        if session is None or session.is_open is not True:
            reasons += ("market_closed_or_unknown",)
        else:
            elapsed = event.at - session.observed_at
            seconds = elapsed.days * 86400 + elapsed.seconds
            age = self._policy.max_session_age_seconds
            if (
                session.observed_at > event.at
                or session.available_at > event.at
                or seconds > age
                or (seconds == age and elapsed.microseconds > 0)
            ):
                reasons += ("session_evidence_unavailable",)
        quote = next(
            (q for q in state.frame.quotes if q.instrument == order.intent.instrument), None
        )
        if quote is None or quote.observed_at <= order.accepted_at:
            reasons += ("later_price_evidence_required",)
        remaining_intent = replace(order.intent, quantity=Quantity(order.remaining))
        assessment = self._assess(remaining_intent, state, order)
        reasons += tuple(r.code.value for r in assessment.rejections)
        if reasons:
            return state, PaperReceipt(
                "fill",
                event.order_id,
                order.status,
                "pending",
                tuple(dict.fromkeys(reasons)),
                event.at,
                assessment,
            )
        assert quote is not None
        mechanics = self._mechanics(order.intent.instrument.currency)
        buy = order.intent.side is OrderSide.BUY
        price = rounded(
            quote.price.amount
            * (1 + self._policy.slippage_rate if buy else 1 - self._policy.slippage_rate),
            mechanics.price_quantum,
            "ROUND_CEILING" if buy else ROUND_FLOOR,
        )
        limit = order.intent.limit_price
        if limit is not None and (
            (buy and price > limit.amount) or (not buy and price < limit.amount)
        ):
            return state, PaperReceipt(
                "fill",
                event.order_id,
                order.status,
                "pending",
                ("limit_not_marketable",),
                event.at,
                assessment,
            )
        gross = price * event.quantity.shares
        fee = rounded(
            gross * (self._policy.buy_fee_rate if buy else self._policy.sell_fee_rate),
            mechanics.cost_quantum,
        )
        tax = (
            Decimal(0)
            if buy
            else rounded(gross * self._policy.sell_tax_rate, mechanics.cost_quantum)
        )
        rejection: tuple[str, ...] = ()
        if price <= 0 or (not buy and fee + tax > gross):
            rejection = ("invalid_execution_economics",)
        if buy and (order.price_cap is None or price > order.price_cap.amount):
            rejection = ("accepted_price_cap_exceeded",)
        if rejection:
            updated = self._release(state, order, True, rejection)
            status = self._find(updated, event.order_id).status
            return updated, PaperReceipt(
                "fill", event.order_id, status, "rejected", rejection, event.at, assessment
            )
        currency = order.intent.instrument.currency
        fill = FillReceipt(
            fingerprint((event, quote, self._policy.policy_id, self._head[1])),
            order.intent,
            event.quantity,
            Money(price, currency),
            Money(fee, currency),
            Money(tax, currency),
            event.at,
            event.at,
            event.source_id,
        )
        account = state.account
        quantity = event.quantity.shares
        release = order.unit_reserve.amount * quantity
        if buy:
            require(gross + fee + tax <= release, "reservation_shortfall")
            account = self._cash(account, currency, release - gross - fee - tax, -release)
            account = self._position(account, order.intent.instrument, quantity, 0)
        else:
            account = self._cash(account, currency, gross - fee - tax, Decimal(0))
            account = self._position(account, order.intent.instrument, -quantity, -quantity)
        remaining = order.remaining - quantity
        status = OrderStatus.PARTIAL if remaining else OrderStatus.FILLED
        validate_transition(order.status, status)
        updated_order = replace(
            order,
            status=status,
            remaining=remaining,
            filled=order.filled + quantity,
            reserved_cash=Money(order.unit_reserve.amount * remaining, currency),
            reserved_quantity=remaining if not buy else 0,
        )
        updated = self._replace_order(
            replace(state, account=account, fills=(*state.fills, fill)), updated_order
        )
        slippage = (price - quote.price.amount if buy else quote.price.amount - price) * quantity
        return updated, PaperReceipt(
            "fill",
            event.order_id,
            status,
            "filled",
            (),
            event.at,
            assessment,
            fill,
            quote.price,
            Money(slippage, currency),
        )

    def _check(self) -> None:
        with exact():
            state, expected = self._head
            require(self._identity(state) == expected, "paper_state_integrity_failure")
            self._validate_state(state)

    def _validate_state(self, state: PaperState) -> None:
        validate_owner(
            (
                self._initial,
                self._policy,
                self._initial_frame,
                state.account,
                state.frame,
                state.orders,
                state.fills,
                state.records,
            )
        )
        integer(state.revision)
        require(
            len({o.intent.order_id for o in state.orders}) == len(state.orders),
            "duplicate_order_id",
        )
        require(len({f.fill_id for f in state.fills}) == len(state.fills), "duplicate_fill_id")
        require(
            len({(r.command.kind, r.command.key) for r in state.records}) == len(state.records),
            "duplicate_command_key",
        )
        require(state.revision == len(state.records), "revision_journal_mismatch")
        cash = {b.available.currency: b.available.amount for b in self._initial.balances}
        quantities = {p.instrument: p.quantity.shares for p in self._initial.positions}
        for fill in state.fills:
            require(fill.order.account_id == state.account.account_id, "fill_account_mismatch")
            self._find(state, fill.order.order_id)
            currency = fill.order.instrument.currency
            gross = fill.price.amount * fill.quantity.shares
            buy = fill.order.side is OrderSide.BUY
            cash[currency] += (-gross if buy else gross) - fill.fees.amount - fill.taxes.amount
            quantities[fill.order.instrument] = quantities.get(fill.order.instrument, 0) + (
                fill.quantity.shares if buy else -fill.quantity.shares
            )
        held = {c: Decimal(0) for c in cash}
        shares: dict[Instrument, int] = {}
        for order in state.orders:
            for count in (order.remaining, order.filled, order.closed, order.reserved_quantity):
                integer(count)
            require(
                order.intent.quantity.shares == order.remaining + order.filled + order.closed,
                "order_quantity_conservation",
            )
            relevant = tuple(f for f in state.fills if f.order.order_id == order.intent.order_id)
            validate_fills(order.intent, relevant)
            require(order.filled == sum(f.quantity.shares for f in relevant), "order_fill_mismatch")
            require(order.status in OPEN | TERMINAL, "unsupported_paper_status")
            currency = order.intent.instrument.currency
            require(
                order.reserved_cash.currency is currency
                and order.unit_reserve.currency is currency,
                "reservation_currency_mismatch",
            )
            if order.status in OPEN:
                require(
                    order.remaining > 0 and order.accepted_at is not None and order.risk.allowed,
                    "unproven_open_order",
                )
                require(order.closed == 0, "open_closed_quantity")
                require(
                    (order.status is OrderStatus.PARTIAL) == (order.filled > 0),
                    "partial_status_mismatch",
                )
            else:
                require(
                    order.remaining == order.reserved_quantity == 0
                    and order.reserved_cash.amount == 0,
                    "terminal_reservation",
                )
                if order.status is OrderStatus.FILLED:
                    require(
                        order.filled == order.intent.quantity.shares, "filled_quantity_mismatch"
                    )
            if order.accepted_at is None:
                require(
                    order.status is OrderStatus.REJECTED and order.filled == 0,
                    "acceptance_required",
                )
            else:
                require(
                    order.intent.created_at <= order.accepted_at <= state.frame.now
                    and order.risk.allowed,
                    "acceptance_evidence_mismatch",
                )
            buy = order.intent.side is OrderSide.BUY
            require(
                order.reserved_cash.amount
                == (order.unit_reserve.amount * order.remaining if buy else 0),
                "cash_hold_mismatch",
            )
            require(
                order.reserved_quantity == (0 if buy else order.remaining), "share_hold_mismatch"
            )
            if currency in held:
                held[currency] += order.reserved_cash.amount
            else:
                require(
                    order.reserved_cash.amount == 0 and order.status is OrderStatus.REJECTED,
                    "missing_balance",
                )
            shares[order.intent.instrument] = (
                shares.get(order.intent.instrument, 0) + order.reserved_quantity
            )
        require(
            {b.available.currency for b in state.account.balances} == set(cash),
            "cash_currency_drift",
        )
        for balance in state.account.balances:
            currency = balance.available.currency
            require(
                balance.available.amount + balance.reserved.amount == cash[currency],
                "cash_conservation",
            )
            require(balance.reserved.amount == held[currency], "reservation_conservation")
        positions = {p.instrument: p for p in state.account.positions}
        require(set(quantities) <= set(positions), "position_missing")
        for instrument, position in positions.items():
            require(
                position.quantity.shares == quantities.get(instrument, 0), "position_conservation"
            )
            require(
                position.reserved.shares == shares.get(instrument, 0),
                "share_reservation_conservation",
            )
            require(
                position.sellable.shares + position.reserved.shares == position.quantity.shares,
                "position_invariant",
            )

    def checkpoint(self) -> PaperCheckpoint:
        with self._lock:
            self._check()
            return PaperCheckpoint(
                self._initial,
                self._policy,
                self._initial_frame,
                tuple(r.command for r in self._head[0].records),
                self._head[1],
            )

    @classmethod
    def restore(cls, checkpoint: PaperCheckpoint) -> "DeterministicPaperBroker":
        require(isinstance(checkpoint, PaperCheckpoint), "paper_checkpoint_required")
        sha(checkpoint.state_id)
        require(type(checkpoint.commands) is tuple, "immutable_commands_required")
        restored = cls(checkpoint.initial_account, checkpoint.policy, checkpoint.initial_frame)
        for command in checkpoint.commands:
            require(isinstance(command, PaperCommand), "paper_command_required")
            restored._apply(command)
        require(restored.state_id == checkpoint.state_id, "checkpoint_replay_mismatch")
        require(
            restored.checkpoint().commands == checkpoint.commands, "checkpoint_command_mismatch"
        )
        return restored
