"""Finite baseline simulation with precommitted reservations and delayed fill knowledge."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, localcontext
from itertools import groupby
from typing import Any

from donghak_stock_vision.backtest import baseline_book as book
from donghak_stock_vision.backtest.baseline_inputs import (
    BaselineSession,
    BaselineSignalSource,
    HGBBaselineSignals,
)
from donghak_stock_vision.backtest.baseline_policy import (
    BaselineBacktestPolicy,
    BaselineCandidate,
    CandidateAccountContext,
    assess_candidate,
    desired_quantity,
    exit_levels,
    order_candidates,
    participation_quantity,
)
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.entry_contract import (
    EntryStatus,
    EntryWindow,
    SignalChronology,
    assess_entry,
    validate_sessions,
)
from donghak_stock_vision.backtest.ledger import LedgerState, checkpoint, restore
from donghak_stock_vision.backtest.research_contract import BacktestContract, OrderSide
from donghak_stock_vision.backtest.validation import require, sha, utc
from donghak_stock_vision.data.learning import AnalysisError, digest
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.data.snapshot import covered
from donghak_stock_vision.features.engine import check_segment

VERSION = "baseline_runner_v1"
PLANNING = "anchor_close_adverse_price_reservation_no_gap_topup_v1"


def input_fingerprint(records: tuple[BaselineSession, ...]) -> str:
    return digest([r.fingerprint for r in records])


@dataclass(frozen=True)
class PendingEntry:
    candidate: BaselineCandidate
    window: EntryWindow
    order_id: str
    quantity: int
    previous: DailyBar


@dataclass(frozen=True)
class OpenEpisode:
    ticker: str
    candidate_id: str
    entry_fill_id: str
    entry_date: date
    target: Decimal
    stop: Decimal
    sessions: int
    previous: DailyBar
    unsupported: str | None = None


@dataclass(frozen=True)
class BaselineRunResult:
    document: FrozenJSON
    ledger: LedgerState

    @property
    def fingerprint(self) -> str:
        return digest([self.document.identifier, self.ledger.identifier])


@dataclass(frozen=True)
class BaselineBacktestRunner:
    policy: BaselineBacktestPolicy
    expected_policy_fingerprint: str
    expected_cost_fingerprint: str
    contract: BacktestContract
    records: tuple[BaselineSession, ...]
    initial: LedgerState
    signals: BaselineSignalSource
    dataset_fingerprint: str
    planning_rule: str

    def validate(self) -> None:
        sha(self.expected_policy_fingerprint)
        sha(self.expected_cost_fingerprint)
        sha(self.dataset_fingerprint)
        require(
            self.policy.fingerprint == self.expected_policy_fingerprint, "locked_policy_mismatch"
        )
        require(
            self.policy.require_costs().fingerprint == self.expected_cost_fingerprint,
            "locked_cost_mismatch",
        )
        require(
            self.planning_rule == PLANNING == self.policy.to_dict()["rules"]["planning_rule"],
            "planning_rule_required",
        )
        p = self.contract.to_dict()
        require(
            self.contract
            == self.policy.contract(date.fromisoformat(p["start"]), date.fromisoformat(p["end"])),
            "locked_contract_mismatch",
        )
        require(type(self.records) is tuple and bool(self.records), "frozen_records_required")
        require(
            tuple(sorted(self.records, key=lambda r: (r.session.trading_date, r.session.ticker)))
            == self.records,
            "noncanonical_session_order",
        )
        keys = [(r.session.trading_date, r.session.ticker) for r in self.records]
        require(len(keys) == len(set(keys)), "duplicate_session")
        series: dict[str, list[BaselineSession]] = {}
        for row in self.records:
            series.setdefault(row.session.ticker, []).append(row)
        for items in series.values():
            validate_sessions(tuple(r.session for r in items))
        m = self.initial.manifest.to_dict()
        require(
            m["information_mode"] == "historical_research" and m["execution_mode"] == "backtest",
            "historical_backtest_only",
        )
        require(m["time_basis"]["boundary"] == "inclusive", "inclusive_boundary_required")
        require(restore(checkpoint(self.initial)) == self.initial, "invalid_initial_checkpoint")
        a = self.initial.to_dict()
        require(
            len(self.initial.events) == 1
            and not a["positions"]
            and not a["orders"]
            and a["available_cash_krw"] == "100000000"
            and a["reserved_cash_krw"] == "0",
            "locked_initial_account_required",
        )
        require(
            self.initial.tape_hash == input_fingerprint(self.records), "input_snapshot_mismatch"
        )
        require(self.signals.up_artifact.direction == "up", "up_artifact_required")
        require(
            self.dataset_fingerprint in {r["content_hash"] for r in m["market_snapshots"]},
            "dataset_manifest_mismatch",
        )
        require(
            self.signals.up_artifact.fingerprint in {r["content_hash"] for r in m["models"]},
            "model_manifest_mismatch",
        )
        if m["data_origin"] == "real":
            require(type(self.signals) is HGBBaselineSignals, "trusted_HGB_source_required")
        for r in self.records:
            r.__post_init__()
            self.policy.quality_policy.rejected_flags(tuple(r.quality.to_dict()["quality_flags"]))
            require(r.session.trading_date.isoformat() <= p["end"], "after_end_input_forbidden")
            if r.bar:
                require(
                    r.bar.market == "KOSPI" and r.bar.adjustment == "unadjusted",
                    "unsupported_price_basis",
                )
                require(
                    (r.bar.provider == "fake") == (m["data_origin"] == "synthetic"),
                    "mixed_source_origin",
                )
            if r.session.trading_date.isoformat() >= p["start"]:
                require(
                    utc(m["start_at"]) <= r.session.bar_available_at <= utc(m["end_at"]),
                    "manifest_publication_boundary",
                )
        previous_known: datetime | None = None
        for _, rows in groupby(self.records, key=lambda r: r.session.trading_date):
            group = tuple(rows)
            require(
                previous_known is None or previous_known < group[0].session.open_at,
                "overlapping_publication_sessions",
            )
            previous_known = group[0].session.bar_available_at
            require(
                len(
                    {
                        (r.session.open_at, r.session.close_at, r.session.bar_available_at)
                        for r in group
                    }
                )
                == 1,
                "asynchronous_daily_sessions_unsupported",
            )
        require(
            self.initial.policy.document.to_dict()["costs"]
            == "supplied_fill_buy_capitalize_sell_expense",
            "ledger_cost_contract",
        )

    def run(self) -> BaselineRunResult:
        """Call explicitly; constructors never run a simulation or predict."""
        self.validate()
        with localcontext(Context(prec=80)):
            return _Simulation(self).run()


class _Simulation:
    def __init__(self, request: BaselineBacktestRunner) -> None:
        self.r = request
        self.source_id = request.signals.fingerprint
        self.series: dict[str, tuple[BaselineSession, ...]] = {}
        grouped: dict[str, list[BaselineSession]] = {}
        for row in request.records:
            grouped.setdefault(row.session.ticker, []).append(row)
        self.series = {t: tuple(rows) for t, rows in grouped.items()}
        self.prefix_indices = {
            (r.session.ticker, r.session.trading_date): i + 1
            for rows in self.series.values()
            for i, r in enumerate(rows)
        }
        self.state = request.initial
        self.pending: dict[str, PendingEntry] = {}
        self.positions: dict[str, OpenEpisode] = {}
        self.events: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.equities: list[dict[str, Any]] = []
        self.costs = request.policy.require_costs()
        self.contract = request.contract.to_dict()
        self.clock = VirtualClock.at(self.state.to_dict()["known_at"], 0)

    def log(self, kind: str, ticker: str, **values: Any) -> None:
        self.events.append(
            dict(
                sequence=len(self.events),
                kind=kind,
                ticker=ticker,
                known_at=self.clock.cutoff,
                **values,
            )
        )

    def quality(self, row: BaselineSession, previous: DailyBar | None) -> str | None:
        if row.bar is None:
            return "missing_bar"
        q = row.quality.to_dict()
        if self.r.policy.quality_policy.rejected_flags(tuple(q.get("quality_flags", []))):
            return "quality_blocked"
        if self.state.manifest.to_dict()["data_origin"] == "real" and (
            self.r.policy.quality_policy.classify(q["session_basis"]) != "ALLOWED"
        ):
            return "quality_blocked"
        if not set(q.get("quality_flags", [])) <= set(self.r.policy.allowed_research_flags):
            return "quality_blocked"
        if q["session_basis"] == "verified_sessions" and not covered(
            q["quality"],
            row.session.ticker,
            previous.trading_date if previous else row.session.trading_date,
            row.session.trading_date,
        ):
            return "quality_coverage_missing"
        try:
            check_segment(([previous] if previous else []) + [row.bar], q)
        except (AnalysisError, ValueError) as error:
            return str(error)
        return None

    def fills(self, rows: tuple[BaselineSession, ...], known: datetime) -> None:
        # Every reservation already exists before this session. No allocation occurs here.
        releases: list[str] = []
        for row in rows:
            ticker = row.session.ticker
            pending = self.pending.get(ticker)
            if pending is None:
                continue
            target = pending.window.eligible_execution_session
            require(target == row.session, "entry_retry_forbidden")
            assessment = assess_entry(
                self.r.contract,
                pending.window,
                attempted_session=row.session,
                previous=pending.previous,
                candidate=row.bar,
                quality_snapshot=row.quality,
                observed_at=known,
            )
            approval_failure = self.quality(row, pending.previous)
            reason = (
                (approval_failure or assessment.reason)
                if assessment.status == EntryStatus.ELIGIBLE
                else assessment.reason
            )
            quantity = 0
            price = Decimal(0)
            if assessment.status == EntryStatus.ELIGIBLE and approval_failure is None:
                assert row.bar is not None
                quantity = participation_quantity(
                    pending.quantity,
                    session_volume=row.bar.volume,
                    bar_available_at=row.session.bar_available_at,
                    observed_at=known,
                )
                price = self.costs.adverse_price(Decimal(row.bar.open), OrderSide.BUY)
                reserved = Decimal(
                    self.state.to_dict()["orders"][pending.order_id]["current"]["reserved_cash_krw"]
                )
                if (
                    price * pending.quantity + self.costs.commission(price * pending.quantity)
                    > reserved
                ):
                    reason, quantity = "reservation_shortfall", 0
                elif quantity == 0:
                    reason = "participation_below_one_share"
            if quantity:
                assert row.bar is not None
                fee = self.costs.commission(price * quantity)
                self.state, fid = book.confirm_fill(
                    self.state,
                    order_id=pending.order_id,
                    source=row.fingerprint,
                    ticker=ticker,
                    quantity=quantity,
                    reference=Decimal(row.bar.open),
                    price=price,
                    fee=fee,
                    tax=Decimal(0),
                    effective=row.session.open_at,
                    known=known,
                )
                target_price, stop = exit_levels(price, pending.candidate.barrier_width)
                self.positions[ticker] = OpenEpisode(
                    ticker,
                    pending.candidate.fingerprint,
                    fid,
                    row.session.trading_date,
                    target_price,
                    stop,
                    0,
                    pending.previous,
                )
                self.log(
                    "buy_fill",
                    ticker,
                    fill_id=fid,
                    quantity=quantity,
                    intended_quantity=pending.quantity,
                    fee=str(fee),
                    tax="0",
                    levy="0",
                )
            else:
                self.log("unfilled", ticker, reason=reason, order_id=pending.order_id)
            order = self.state.to_dict()["orders"][pending.order_id]["current"]
            if order["remaining_quantity"]:
                releases.append(pending.order_id)
            del self.pending[ticker]
        # Release only after ALL entry fills; unused volume-cap cash cannot fund this batch.
        for oid in releases:
            self.state = book.release(self.state, oid, rows[0].session.close_at, known)

    def exits(self, rows: tuple[BaselineSession, ...], known: datetime) -> None:
        from dataclasses import replace

        present = {r.session.ticker for r in rows}
        for ticker in sorted(set(self.positions) - present):
            self.positions[ticker] = replace(
                self.positions[ticker], unsupported="missing_portfolio_session"
            )
            self.log("unsupported_exit", ticker, reason="missing_portfolio_session")
        for row in rows:
            ticker = row.session.ticker
            episode = self.positions.get(ticker)
            if episode is None:
                continue
            if episode.unsupported:
                continue  # Never recover silently after an unresolved exit interval.
            reason = self.quality(row, episode.previous)
            if reason:
                self.positions[ticker] = replace(episode, unsupported=reason)
                self.log("unsupported_exit", ticker, reason=reason)
                continue
            assert row.bar is not None
            bar = row.bar
            n = episode.sessions + 1
            episode = replace(episode, sessions=n, previous=bar)
            self.positions[ticker] = episode
            # Gap-through rules were deliberately not approved in the locked policy.
            if Decimal(bar.open) < episode.stop or Decimal(bar.open) > episode.target:
                self.positions[ticker] = replace(episode, unsupported="gap_through_barrier")
                self.log("unsupported_exit", ticker, reason="gap_through_barrier")
                continue
            reference: Decimal | None = None
            if Decimal(bar.low) <= episode.stop:
                reference, reason = episode.stop, "stop_first"
            elif Decimal(bar.high) >= episode.target:
                reference, reason = episode.target, "target"
            elif n == 5:
                reference, reason = Decimal(bar.close), "fifth_session_close"
            if reference is None:
                continue
            require(
                row.settlement_date is not None and row.settlement_evidence_id is not None,
                "settlement_evidence_missing",
            )
            assert row.settlement_date is not None and row.settlement_evidence_id is not None
            position = self.state.to_dict()["positions"][ticker]
            quantity = position["quantity"]
            price = self.costs.adverse_price(reference, OrderSide.SELL)
            fee, tax, levy = self.costs.charges(
                execution_price=price,
                quantity=quantity,
                side=OrderSide.SELL,
                execution_date=row.session.trading_date,
                settlement_date=row.settlement_date,
                settlement_evidence_id=row.settlement_evidence_id,
            )
            source = digest([episode.candidate_id, row.fingerprint, reason])
            self.state, oid = book.reserve(
                self.state,
                side="SELL",
                ticker=ticker,
                quantity=quantity,
                source=source,
                effective=row.session.close_at,
                known=known,
                notional=Decimal(0),
                fee=Decimal(0),
            )
            self.state, fid = book.confirm_fill(
                self.state,
                order_id=oid,
                source=row.fingerprint,
                ticker=ticker,
                quantity=quantity,
                reference=reference,
                price=price,
                fee=fee,
                tax=tax + levy,
                effective=row.session.close_at,
                known=known,
            )
            closed = dict(
                ticker=ticker,
                entry_fill_id=episode.entry_fill_id,
                exit_fill_id=fid,
                reason=reason,
                holding_sessions=n,
                realized_pnl=str(
                    price * quantity - fee - tax - levy - Decimal(position["cost_basis_krw"])
                ),
            )
            self.closed.append(closed)
            self.log(
                "sell_fill",
                ticker,
                **{k: v for k, v in closed.items() if k != "ticker"},
                fee=str(fee),
                tax=str(tax),
                levy=str(levy),
            )
            del self.positions[ticker]

    def equity(self, rows: tuple[BaselineSession, ...]) -> Decimal | None:
        marks = {r.session.ticker: r for r in rows}
        equity = Decimal(self.state.to_dict()["total_cash_krw"])
        for ticker, episode in self.positions.items():
            if episode.unsupported or ticker not in marks or marks[ticker].bar is None:
                return None
            row = marks[ticker]
            if self.quality(row, None):
                return None
            assert row.bar is not None
            equity += Decimal(row.bar.close) * self.state.to_dict()["positions"][ticker]["quantity"]
        return equity

    def allocate(self, rows: tuple[BaselineSession, ...], known: datetime) -> None:
        equity = self.equity(rows)
        self.equities.append(
            dict(
                known_at=known.isoformat(),
                session=rows[0].session.trading_date.isoformat(),
                cash=self.state.to_dict()["total_cash_krw"],
                equity=str(equity) if equity is not None else None,
                complete=equity is not None,
            )
        )
        if equity is None:
            self.log("allocation_blocked", "", reason="incomplete_equity")
            return
        context = CandidateAccountContext(
            self.state.identifier, known, frozenset(self.positions), frozenset(self.pending)
        )
        candidates: list[tuple[BaselineCandidate, EntryWindow, DailyBar]] = []
        for row in rows:
            ticker = row.session.ticker
            if ticker in self.positions or ticker in self.pending:
                self.log("signal_ignored", ticker, reason="held_or_pending")
                continue
            if self.quality(row, None):
                self.log("signal_unavailable", ticker, reason=self.quality(row, None))
                continue
            series = self.series[ticker]
            prefix = series[: self.prefix_indices[(ticker, row.session.trading_date)]]
            try:
                frame = self.r.signals.research(prefix, known)
                require(
                    frame.signal.context.data_origin
                    == self.state.manifest.to_dict()["data_origin"],
                    "signal_origin_mismatch",
                )
                require(
                    frame.signal.policy_fingerprint == self.r.signals.policy.fingerprint,
                    "signal_policy_mismatch",
                )
                require(frame.signal.context.as_of == known, "signal_time_mismatch")
                quality_reason = self.r.policy.quality_policy.signal_reason(frame.signal)
                if quality_reason:
                    self.log(
                        "signal_unavailable",
                        ticker,
                        reason=quality_reason,
                        quality_policy=self.r.policy.quality_policy.fingerprint,
                        quality_flags=list(frame.signal.context.quality_flags),
                        signal_id=frame.signal.fingerprint,
                    )
                    continue
                chronology = SignalChronology.from_signal(frame.signal, row.session, known)
                sessions = tuple(r.session for r in series)
                window = EntryWindow(chronology, sessions)
                result = assess_candidate(
                    self.r.policy,
                    self.r.contract,
                    frame.signal,
                    up_artifact=self.r.signals.up_artifact,
                    values=frame.values,
                    window=window,
                    account=context,
                )
            except AnalysisError as error:
                self.log("signal_unavailable", ticker, reason=str(error))
                continue
            self.log(
                "candidate_assessment",
                ticker,
                reason=result.reason,
                signal_id=frame.signal.fingerprint,
            )
            if result.candidate:
                assert row.bar is not None
                candidates.append((result.candidate, window, row.bar))
        ordered = order_candidates(tuple(c for c, _, _ in candidates))
        by_id = {c.fingerprint: (w, b) for c, w, b in candidates}
        for candidate in ordered:
            ticker = candidate.intent.chronology.signal_anchor.ticker
            if len(self.positions) + len(self.pending) >= 20:
                self.log("unfilled", ticker, reason="position_limit")
                continue
            window, anchor = by_id[candidate.fingerprint]
            planning_price = self.costs.adverse_price(Decimal(anchor.close), OrderSide.BUY)
            quantity = desired_quantity(
                self.r.policy,
                current_equity=equity,
                available_cash=Decimal(self.state.to_dict()["available_cash_krw"]),
                entry_execution_price=planning_price,
            )
            if quantity < 1:
                self.log("unfilled", ticker, reason="cash_below_one_share")
                continue
            notional = planning_price * quantity
            fee = self.costs.commission(notional)
            self.state, oid = book.reserve(
                self.state,
                side="BUY",
                ticker=ticker,
                quantity=quantity,
                source=candidate.intent.fingerprint,
                effective=known,
                known=known,
                notional=notional,
                fee=fee,
            )
            self.pending[ticker] = PendingEntry(candidate, window, oid, quantity, anchor)
            self.log(
                "reservation",
                ticker,
                order_id=oid,
                quantity=quantity,
                intended_notional=str(notional),
                reserved_cash=str(notional + fee),
                equity=str(equity),
                planning_price=str(planning_price),
                candidate_id=candidate.fingerprint,
                signal_id=candidate.intent.signal_id,
                intent_id=candidate.intent.fingerprint,
                eligible_session=window.eligible_execution_session.trading_date.isoformat()
                if window.eligible_execution_session
                else None,
            )

    def run(self) -> BaselineRunResult:
        for index, (day, records) in enumerate(
            groupby(self.r.records, key=lambda r: r.session.trading_date), 1
        ):
            if day.isoformat() < self.contract["start"]:
                continue  # Warmup only: no orders, fills, valuation or account effects.
            rows = tuple(records)
            known = rows[0].session.bar_available_at
            self.clock = self.clock.advance(known, index)
            # All mutations are to local immutable values. Any exception discards the entire run.
            self.fills(rows, known)
            self.exits(rows, known)
            self.allocate(rows, known)
        for ticker, episode in sorted(self.positions.items()):
            self.log(
                "open_at_end",
                ticker,
                reason=episode.unsupported or "insufficient_remaining_sessions",
            )
        require(self.r.signals.fingerprint == self.source_id, "signal_source_changed_during_run")
        doc = FrozenJSON.freeze(
            dict(
                runner_version=VERSION,
                dataset_fingerprint=self.r.dataset_fingerprint,
                input_snapshot_fingerprint=input_fingerprint(self.r.records),
                up_model_sha=self.r.signals.up_artifact.checkpoint_sha256,
                up_model_fingerprint=self.r.signals.up_artifact.fingerprint,
                signal_policy_fingerprint=self.r.signals.policy.fingerprint,
                signal_source_fingerprint=self.r.signals.fingerprint,
                baseline_policy_fingerprint=self.r.policy.fingerprint,
                backtest_contract_fingerprint=self.r.contract.fingerprint,
                cost_schedule_fingerprint=self.costs.fingerprint,
                planning_rule=self.r.planning_rule,
                events=self.events,
                closed_trades=self.closed,
                equity_snapshots=self.equities,
                final_ledger_hash=self.state.identifier,
                pending_orders=sorted(self.pending),
                open_positions=sorted(self.positions),
                position_episodes=[
                    dict(
                        ticker=t,
                        candidate_id=e.candidate_id,
                        entry_fill_id=e.entry_fill_id,
                        entry_date=e.entry_date.isoformat(),
                        target=str(e.target),
                        stop=str(e.stop),
                        observed_sessions=e.sessions,
                        unsupported=e.unsupported,
                    )
                    for t, e in sorted(self.positions.items())
                ],
                fill_count=len(self.state.to_dict()["fills"]),
                closed_trade_count=len(self.closed),
                rejection_count=sum(e["kind"] == "unfilled" for e in self.events),
                status="incomplete"
                if any(e.unsupported for e in self.positions.values())
                else "completed_with_open_positions"
                if self.positions
                else "completed",
                beginning_cash="100000000",
                ending_cash=self.state.to_dict()["total_cash_krw"],
                realized_pnl=self.state.to_dict()["realized_pnl_krw"],
                fees=str(sum((Decimal(e.get("fee", "0")) for e in self.events), Decimal(0))),
                taxes=str(sum((Decimal(e.get("tax", "0")) for e in self.events), Decimal(0))),
                levies=str(sum((Decimal(e.get("levy", "0")) for e in self.events), Decimal(0))),
                scope="historical_research",
                operational_eligible=False,
                executable=False,
                usage_restriction=self.state.manifest.to_dict()["usage_restriction"],
                limitations=[
                    "not_PIT_or_OOS_evidence",
                    "daily_volume_known_after_session",
                    "no_liquidity_guarantee",
                    "barrier_time_recorded_at_session_close",
                    "immediate_virtual_cash_not_broker_settlement",
                ],
            )
        )
        return BaselineRunResult(doc, self.state)
