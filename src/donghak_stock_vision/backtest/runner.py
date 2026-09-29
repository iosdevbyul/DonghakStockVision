"""Finite, in-memory historical orchestration. No strategy, pricing, or performance model."""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import MarketEvent
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape
from donghak_stock_vision.backtest.decision_bridge import DecisionBundle, admit, policy_values
from donghak_stock_vision.backtest.execution import accept_candidate, execute, settings
from donghak_stock_vision.backtest.ledger import LedgerState, checkpoint, restore
from donghak_stock_vision.backtest.position_history import build_position_history
from donghak_stock_vision.backtest.validation import fields, integer, require, utc
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import validate_ticker
from donghak_stock_vision.strategy.contracts import DecisionInput, DecisionPolicy
from donghak_stock_vision.strategy.policy import validate_policy


@dataclass(frozen=True)
class BacktestRunner:
    tape: FrozenTape
    analyses: tuple[FrozenAnalysis, ...]
    initial: LedgerState
    decision_policy: DecisionPolicy
    execution_policy: FrozenJSON
    config: FrozenJSON
    historical_input: FrozenJSON | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "analyses", tuple(self.analyses))

    def validate(self) -> dict[str, Any]:
        c = fields(
            self.config.to_dict(),
            "run_id start_at end_at tickers event_clock decision_steps"
            + (" history_mode" if "history_mode" in self.config.to_dict() else ""),
        )
        require(c.get("history_mode") in {None, "verified_execution"}, "unsupported_history_mode")
        m = self.tape.manifest.to_dict()
        require(m["information_mode"] == "historical_research", "pit_runner_unsupported")
        if m["data_origin"] != "synthetic":
            from donghak_stock_vision.backtest.historical_execution import MODE, validate_input

            require(settings(self.execution_policy)["liquidity"] == MODE, "real_runner_unsupported")
            evidence = validate_input(self.tape, self.historical_input)
            require(self.analyses == evidence.analyses, "historical_analysis_evidence_mismatch")
            require(
                c["tickers"] == evidence.document.to_dict()["tickers"],
                "historical_universe_mismatch",
            )

        require(m["execution_mode"] == "backtest", "paper_runner_unsupported")
        require(c["run_id"] == self.tape.manifest.identifier, "run_id_mismatch")
        require(c["event_clock"] == "available_at", "unsupported_event_clock")
        start, end = utc(c["start_at"]), utc(c["end_at"])
        require(start <= end, "start_after_end")
        require(utc(m["start_at"]) <= start <= end <= utc(m["end_at"]), "period_outside_manifest")
        require(isinstance(c["tickers"], list) and bool(c["tickers"]), "universe_required")
        require(len(c["tickers"]) == len(set(c["tickers"])), "duplicate_ticker")
        for ticker in c["tickers"]:
            validate_ticker(ticker)
        require(
            self.initial.manifest == self.tape.manifest
            and self.initial.tape_hash == self.tape.identifier,
            "initial_run_mismatch",
        )
        require(
            len(self.initial.events) == 1
            and self.initial.events[0].document.to_dict()["kind"] == "initialize",
            "initialized_account_required",
        )
        require(utc(self.initial.to_dict()["known_at"]) <= start, "future_initial_account")
        require(restore(checkpoint(self.initial)) == self.initial, "invalid_initial_checkpoint")
        require(self.decision_policy is not None, "decision_policy_missing")
        require(self.execution_policy is not None, "execution_policy_missing")
        validate_policy(self.decision_policy.to_dict())
        settings(self.execution_policy)
        ids = [a.bundle.to_dict()["analysis_id"] for a in self.analyses]
        require(len(ids) == len(set(ids)), "duplicate_analysis")
        for a in self.analyses:
            require(
                a.manifest == self.tape.manifest and a.release_plan == self.tape.release_plan,
                "analysis_run_mismatch",
            )
        require(isinstance(c["decision_steps"], list), "decision_steps_required")
        seen: set[tuple[int, str]] = set()
        sequences = {e.to_dict()["sequence"] for e in self.tape.events}
        for step in c["decision_steps"]:
            s = fields(step, "sequence ticker analysis_id market admission_policy reservation")
            seq = integer(s["sequence"], positive=True)
            require(seq in sequences, "decision_event_missing")
            require(s["ticker"] in c["tickers"], "ticker_outside_universe")
            require(s["analysis_id"] in ids, "analysis_missing")
            require((seq, s["ticker"]) not in seen, "duplicate_decision_step")
            seen.add((seq, s["ticker"]))
            p = policy_values(s["admission_policy"])
            require(p["ordered_tickers"] == c["tickers"], "universe_order_policy_mismatch")
            reservation = s["reservation"]
            if set(reservation) == {"BUY", "SELL"}:
                for side in ("BUY", "SELL"):
                    fields(reservation[side], "max_notional_krw max_cost_krw")
            else:
                fields(reservation, "max_notional_krw max_cost_krw")
            require(isinstance(s["market"], dict), "market_context_required")
        previous: VirtualClock | None = None
        for e in self.tape.events:
            v = e.to_dict()
            require(utc(v["event_at"]) <= utc(v["available_at"]), "event_clock_before_event")
            previous = (
                previous.advance(v["available_at"], v["sequence"])
                if previous
                else VirtualClock.at(v["available_at"], v["sequence"])
            )
        c["start_at"], c["end_at"] = start.isoformat(), end.isoformat()
        c["decision_steps"].sort(key=lambda s: (s["sequence"], c["tickers"].index(s["ticker"])))
        return c

    def project(
        self,
        state: LedgerState,
        step: dict[str, Any],
        analysis: FrozenAnalysis,
        clock: VirtualClock,
        position_history: FrozenJSON | None = None,
    ) -> DecisionInput:
        """Read-only projection of the current virtual account, after the analysis gate."""
        view = self.tape.view(clock).to_dict()
        s, ticker = state.to_dict(), step["ticker"]
        prices: dict[str, Decimal] = {}
        marks = policy_values(step["admission_policy"])["marks"]
        needed = {ticker} | {t for t, p in s["positions"].items() if p["quantity"]}
        for symbol in sorted(needed):
            require(symbol in marks, "valuation_evidence_missing")
            mark = fields(marks[symbol], "sequence field")
            require(mark["field"] in {"open", "high", "low", "close"}, "invalid_mark")
            rows = [
                r
                for r in view["items"]
                if r["ticker"] == symbol and r["sequence"] == mark["sequence"]
            ]
            require(
                len(rows) == 1 and mark["field"] in rows[0]["public_fields"], "valuation_not_public"
            )
            prices[symbol] = Decimal(rows[0]["public_fields"][mark["field"]])
        with localcontext(Context(prec=80)):
            gross = sum(
                prices[t] * p["quantity"] for t, p in s["positions"].items() if p["quantity"]
            )
            equity = Decimal(s["total_cash_krw"]) + gross
            position = s["positions"].get(ticker, {"quantity": 0, "sellable_quantity": 0})
            exposure = prices[ticker] * position["quantity"]
        common = {
            "origin": "virtual",
            "snapshot_version": "runner_projection_v1",
            "observed_at": clock.cutoff,
            "received_at": clock.cutoff,
        }
        account = {
            **common,
            "account_ref": s["account_id"],
            "complete": True,
            "ticker": ticker,
            "currency": "KRW",
            "quantity": position["quantity"],
            "sellable_quantity": position["sellable_quantity"],
            "average_price_krw": None,
            "cash_basis": "net_of_reservations",
            "available_cash_krw": s["available_cash_krw"],
            "reserved_cash_krw": s["reserved_cash_krw"],
            "equity_krw": str(equity),
            "gross_exposure_krw": str(gross),
            "position_exposure_krw": str(exposure),
            "positions_count": sum(p["quantity"] > 0 for p in s["positions"].values()),
        }
        orders = {
            **common,
            "account_ref": s["account_id"],
            "complete": True,
            # Do not invent missing post-fill exit/analysis lineage.
            "history_complete": position_history is not None or not bool(s["fills"]),
            "last_exit": position_history.to_dict()["last_exits"].get(ticker)
            if position_history
            else None,
            "items": [
                {
                    "ticker": o["current"]["ticker"],
                    "state": (
                        "cancelled"
                        if o["current"]["status"] == "expired"
                        else o["current"]["status"]
                    ),
                    "remaining_quantity": o["current"]["remaining_quantity"],
                    "reserved_cash_krw": o["current"]["reserved_cash_krw"],
                    "received_at": s["known_at"],
                }
                for _, o in sorted(s["orders"].items())
            ],
        }
        market = step["market"]
        require(Decimal(market["reference_price_krw"]) == prices[ticker], "market_price_not_public")
        for key in ("observed_at", "received_at", "price_observed_at"):
            require(utc(market[key]) <= utc(clock.cutoff), "future_market_context")
        mark = marks[ticker]
        release = next(
            r
            for r in self.tape.release_plan.to_dict()["releases"]
            if r["sequence"] == mark["sequence"]
        )
        require(
            utc(market["price_observed_at"]) == utc(release["field_times"][mark["field"]]),
            "market_price_time_mismatch",
        )
        b = analysis.bundle.to_dict()
        return DecisionInput.from_dict(
            {
                "request": {
                    "scope": "research",
                    "ticker": ticker,
                    "decision_as_of": clock.cutoff,
                    "analysis_id": step["analysis_id"],
                },
                "analysis": b["signal"],
                "price_basis": step["market"]["adjustment"],
                "account": account,
                "orders": orders,
                "market": step["market"],
                "provenance": {
                    "source": "frozen_analysis",
                    "bundle_hash": analysis.bundle.identifier,
                    "analysis_id": b["analysis_id"],
                    "model_id": b["model_id"],
                    "snapshot_id": b["snapshot_id"],
                },
            }
        )

    def process_event(
        self,
        event: MarketEvent,
        clock: VirtualClock,
        state: LedgerState,
        pending: dict[str, FrozenJSON],
        history: FrozenJSON,
        c: dict[str, Any],
        records: dict[str, FrozenJSON] | None = None,
    ) -> tuple[LedgerState, dict[str, FrozenJSON], FrozenJSON, dict[str, Any]]:
        """Stage a whole event; the caller commits returned immutable state only on success."""
        pending = dict(pending)
        records = dict(records or {})
        verified_history = c.get("history_mode") == "verified_execution"
        e = event.to_dict()
        report: dict[str, Any] = {
            "sequence": clock.sequence,
            "cutoff": clock.cutoff,
            "status": "processed",
            "market_view_hash": self.tape.view(clock).identifier,
            "decisions": [],
            "candidates": [],
            "admissions": [],
            "executions": [],
            "blocked": [],
        }
        for ticker in c["tickers"]:
            if ticker not in pending or ticker != e["ticker"]:
                continue
            acceptance = pending[ticker]
            accepted = acceptance.to_dict()
            cursor = accepted["acceptance_clock"]
            if e["sequence"] <= cursor["sequence"] or utc(e["event_at"]) <= utc(cursor["cutoff"]):
                report["blocked"].append({"reason": "awaiting_future_event", "ticker": ticker})
                continue
            output, updated = execute(
                acceptance,
                state,
                self.tape,
                event,
                clock,
                expected_revision=accepted["reservation_checkpoint"]["state"]["revision"],
                ledger_sequence=state.to_dict()["last_sequence"] + 1,
                quantity=accepted["accepted_order"]["quantity"],
            )
            report["executions"].append(output.to_dict())
            state = updated
            if output.to_dict()["status"] == "filled":
                if verified_history:
                    packet = records[accepted["order_id"]].to_dict()
                    packet["execution"] = output.to_dict()
                    records[accepted["order_id"]] = FrozenJSON.freeze(packet)
                del pending[ticker]
        artifacts = {a.bundle.to_dict()["analysis_id"]: a for a in self.analyses}
        steps = [s for s in c["decision_steps"] if s["sequence"] == clock.sequence]
        steps.sort(key=lambda s: c["tickers"].index(s["ticker"]))
        for step in steps:
            analysis = artifacts[step["analysis_id"]]
            public = analysis.view(clock).to_dict()
            if public["status"] == "blocked":
                report["blocked"].append({"ticker": step["ticker"], "reason": public["reason"]})
                continue
            proof = (
                build_position_history(
                    state,
                    self.tape,
                    tuple(v for v in records.values() if v.to_dict()["execution"]),
                    cutoff=clock.cutoff,
                )
                if verified_history
                else None
            )
            inputs = self.project(state, step, analysis, clock, proof)
            bundle = DecisionBundle.calculate(inputs, self.decision_policy, state)
            report["decisions"].append(
                {
                    "decision_id": bundle.result.identifier,
                    "input": inputs.to_dict(),
                    "policy_id": self.decision_policy.identifier,
                    "result": bundle.result.to_dict(),
                }
            )
            candidate, updated_history = admit(
                bundle,
                bundle.result.identifier,
                state,
                self.tape,
                analysis,
                clock,
                clock,
                step["admission_policy"],
                history,
                position_history=proof,
                execution_policy=self.execution_policy,
                historical_input=self.historical_input,
            )
            report["candidates"].append(candidate.to_dict())
            if not candidate.to_dict()["virtual_order_eligible"]:
                continue
            # PR-E pins the whole reserved revision; simultaneous reservations would
            # invalidate each other's execution checkpoint. Do not weaken that contract.
            if pending:
                report["blocked"].append(
                    {"ticker": step["ticker"], "reason": "concurrent_reservation_unsupported"}
                )
                continue
            acceptance, updated = accept_candidate(
                candidate,
                state,
                self.tape,
                clock,
                clock,
                clock,
                self.execution_policy,
                FrozenJSON.freeze(
                    step["reservation"].get(bundle.result.to_dict()["action"], step["reservation"])
                ),
                ledger_sequence=state.to_dict()["last_sequence"] + 1,
                historical_input=self.historical_input,
            )
            report["admissions"].append(acceptance.to_dict())
            if acceptance.to_dict()["status"] == "accepted":
                if verified_history:
                    records[acceptance.to_dict()["order_id"]] = FrozenJSON.freeze(
                        {
                            "decision_input": inputs.to_dict(),
                            "decision_policy": self.decision_policy.to_dict(),
                            "decision_result": bundle.result.to_dict(),
                            "decision_ledger_hash": state.identifier,
                            "analysis_bundle": analysis.bundle.to_dict(),
                            "acceptance": acceptance.to_dict(),
                            "execution": None,
                        }
                    )
                state, history = updated, updated_history
                pending[step["ticker"]] = acceptance
        if verified_history:
            proof = build_position_history(
                state,
                self.tape,
                tuple(v for v in records.values() if v.to_dict()["execution"]),
                cutoff=clock.cutoff,
            )
            report["position_history_hash"] = proof.identifier
        report["checkpoint_hash"] = checkpoint(state).identifier
        if verified_history:
            report["_staged_trade_records"] = records
        return state, pending, history, report

    def run(self) -> FrozenJSON:
        c = self.validate()  # Invalid configuration aborts before any event.
        state, history = self.initial, FrozenJSON.freeze({})
        pending: dict[str, FrozenJSON] = {}
        reports: list[dict[str, Any]] = []
        records: dict[str, FrozenJSON] = {}
        previous: VirtualClock | None = None
        for event in self.tape.events:
            e = event.to_dict()
            if not utc(c["start_at"]) <= utc(e["available_at"]) <= utc(c["end_at"]):
                continue
            clock = (
                previous.advance(e["available_at"], e["sequence"])
                if previous
                else VirtualClock.at(e["available_at"], e["sequence"])
            )
            previous = clock
            try:
                updated, next_pending, next_history, report = self.process_event(
                    event, clock, state, pending, history, c, records
                )
            except (ValueError, KeyError, TypeError, ArithmeticError, RuntimeError) as error:
                reports.append(
                    {
                        "sequence": clock.sequence,
                        "cutoff": clock.cutoff,
                        "status": "failed",
                        "reason": str(error),
                        "checkpoint_hash": checkpoint(state).identifier,
                    }
                )
                continue
            next_records = report.pop("_staged_trade_records", records)
            state, pending, history, records = updated, next_pending, next_history, next_records
            reports.append(report)
        identity = {
            "config": c,
            "tape": self.tape.identifier,
            "analyses": sorted(a.bundle.identifier for a in self.analyses),
            "initial": self.initial.identifier,
            "decision_policy": self.decision_policy.identifier,
            "execution_policy": self.execution_policy.identifier,
            "runner_version": "historical_runner_v1",
        }
        payload = {
            "run_id": c["run_id"],
            "start_at": utc(c["start_at"]).isoformat(),
            "end_at": utc(c["end_at"]).isoformat(),
            "input_hash": digest(identity),
            "information_mode": "historical_research",
            "usage_restriction": self.tape.manifest.to_dict()["usage_restriction"],
            "operational_eligible": False,
            "executable": False,
            "status": "completed_with_failures"
            if any(r["status"] == "failed" for r in reports)
            else "completed",
            "processed_event_count": len(reports),
            "events": reports,
            "decision_policy": self.decision_policy.to_dict(),
            "pending": {k: v.to_dict() for k, v in sorted(pending.items())},
            "final_account": state.to_dict(),
            "final_checkpoint": checkpoint(state).to_dict(),
            "final_checkpoint_hash": checkpoint(state).identifier,
        }
        if c.get("history_mode") == "verified_execution":
            proof = build_position_history(
                state,
                self.tape,
                tuple(v for v in records.values() if v.to_dict()["execution"]),
                cutoff=c["end_at"],
            )
            payload["position_history"] = proof.to_dict()
            payload["position_history_hash"] = proof.identifier
        if self.historical_input is not None:
            from donghak_stock_vision.backtest.historical_execution import metadata

            payload.update(
                **metadata(self.tape), historical_input_hash=self.historical_input.identifier
            )
        return FrozenJSON.freeze(payload)
