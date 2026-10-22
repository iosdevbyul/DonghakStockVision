"""Production simulation port adapter, composing the existing in-memory Paper Broker."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import FailureCode, RunBudget, SimulationFault
from donghak_stock_vision.backtest.paper_scenario import PaperScenario
from donghak_stock_vision.backtest.simulation_spec import (
    ExperimentSpec,
    SimulationJob,
    bounded_json,
)
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.broker import RiskDecision
from donghak_stock_vision.trading.domain import Money, OrderStatus
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    Cancellation,
    FillEvent,
    PaperFrame,
    PaperReceipt,
    Submission,
    exact,
    fingerprint,
    wire,
)

RESULT_VERSION = "paper_simulation_result_v1"


@dataclass(frozen=True)
class PaperSimulationAdapter:
    """Immutable scenario registry. Brokers/proofs/receipts are always job-local.

    No input acquisition, shared mutable accounts, cached execution results or persistence.
    Native KR/US jobs may share a registry but belong to separate currency experiments.
    """

    scenarios: tuple[PaperScenario, ...]
    _dataset: FrozenJSON = field(init=False, repr=False)
    _reference: FrozenJSON = field(init=False, repr=False)
    _policy: FrozenJSON = field(init=False, repr=False)
    _scenario_pins: FrozenJSON = field(init=False, repr=False)
    version: ClassVar[str] = "paper_simulation_adapter_v1"

    def __post_init__(self) -> None:
        require(
            type(self.scenarios) is tuple and 0 < len(self.scenarios) <= 100,
            "scenario_registry_limit",
        )
        for scenario in self.scenarios:
            require(type(scenario) is PaperScenario, "scenario_required")
            scenario.__post_init__()
        require(
            len({s.scenario_id for s in self.scenarios}) == len(self.scenarios),
            "duplicate_scenario_id",
        )
        ordered = tuple(sorted(self.scenarios, key=lambda s: s.scenario_id))
        object.__setattr__(self, "scenarios", ordered)
        object.__setattr__(
            self,
            "_scenario_pins",
            FrozenJSON.freeze({s.scenario_id: s.fingerprint for s in ordered}),
        )
        manifest = FrozenJSON.freeze(
            {
                "version": "synthetic_paper_dataset_v1",
                "scenarios": [s.document.to_dict() for s in ordered],
            }
        )
        bounded_json(manifest, 4 * 1024 * 1024)
        object.__setattr__(
            self,
            "_dataset",
            FrozenJSON.freeze(
                {
                    "artifact_id": "synthetic-paper-scenarios",
                    "version": "synthetic_paper_dataset_v1",
                    "content_hash": manifest.identifier,
                }
            ),
        )
        object.__setattr__(
            self,
            "_reference",
            FrozenJSON.freeze(
                {
                    "artifact_id": "paper-broker-scenario-adapter",
                    "version": self.version,
                    "content_hash": digest(
                        {"adapter_version": self.version, "dataset": self._dataset.to_dict()}
                    ),
                }
            ),
        )
        object.__setattr__(
            self,
            "_policy",
            FrozenJSON.freeze(
                {
                    "version": "paper_scenario_policies_v1",
                    "policies": [
                        {"scenario_id": s.scenario_id, "policy_hash": fingerprint(s.policy)}
                        for s in ordered
                    ],
                }
            ),
        )

    @property
    def reference(self) -> FrozenJSON:
        return self._reference

    @property
    def dataset_reference(self) -> FrozenJSON:
        return self._dataset

    @property
    def execution_policy(self) -> FrozenJSON:
        return self._policy

    def job(self, key: str, scenario_id: str) -> SimulationJob:
        scenario = self._scenario(scenario_id)
        return SimulationJob(
            key,
            FrozenJSON.freeze(
                {
                    "scenario_id": scenario.scenario_id,
                    "scenario_hash": scenario.fingerprint,
                }
            ),
        )

    def _scenario(self, scenario_id: str) -> PaperScenario:
        for scenario in self.scenarios:
            if scenario.scenario_id == scenario_id:
                return scenario
        raise SimulationFault(FailureCode.MISSING_INPUT, "scenario_not_registered")

    def _inputs(self, experiment: ExperimentSpec, job: SimulationJob, seed: int) -> PaperScenario:
        require(job in experiment.jobs, "job_not_in_experiment")
        require(seed == experiment.run_seed(job), "run_seed_mismatch")
        require(experiment.runner == self.reference, "runner_reference_mismatch")
        require(experiment.dataset == self.dataset_reference, "scenario_dataset_mismatch")
        require(experiment.execution_policy == self.execution_policy, "scenario_policy_mismatch")
        require(experiment.data_origin == "synthetic", "synthetic_scenarios_only")
        params = fields(job.parameters.to_dict(), "scenario_id scenario_hash")
        scenario = self._scenario(params["scenario_id"])
        sha(params["scenario_hash"])
        require(
            params["scenario_hash"]
            == self._scenario_pins.to_dict()[scenario.scenario_id]
            == scenario.fingerprint,
            "scenario_hash_mismatch",
        )
        require(
            scenario.market is experiment.market and scenario.currency is experiment.currency,
            "scenario_experiment_currency_mismatch",
        )
        return scenario

    def run(
        self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
    ) -> FrozenJSON:
        budget.consume()
        try:
            scenario = self._inputs(experiment, job, seed)
        except SimulationFault:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise SimulationFault(
                FailureCode.INCOMPATIBLE_INPUT, "scenario_binding_invalid"
            ) from error
        # A new owner for every call: even identical account/order IDs cannot leak state.
        broker = DeterministicPaperBroker(
            scenario.initial_account, scenario.policy, scenario.initial_frame
        )
        proofs: dict[str, RiskDecision] = {}
        receipts: list[PaperReceipt] = []
        seen: set[tuple[str, str]] = set()
        order_ids: dict[str, None] = {}
        try:
            for command in scenario.commands:
                budget.consume()
                payload = command.payload
                receipt = None
                if isinstance(payload, PaperFrame):
                    broker.advance(payload)
                elif isinstance(payload, Submission):
                    # Preserve the original proof on exact repeated submissions, so broker
                    # idempotency still compares the complete original command after fills.
                    if command.key not in proofs:
                        decision = broker.preflight(payload.order).to_decision()
                        require(decision is not None, "risk_decision_unavailable")
                        assert decision is not None
                        proofs[command.key] = decision
                    receipt = broker.submit(
                        payload.order, idempotency_key=command.key, risk=proofs[command.key]
                    )
                    order_ids[payload.order.order_id] = None
                elif isinstance(payload, FillEvent):
                    receipt = broker.execute(payload)
                elif isinstance(payload, Cancellation):
                    receipt = broker.cancel(
                        payload.order_id, idempotency_key=command.key, at=payload.at
                    )
                else:
                    raise ValueError("unsupported_scenario_payload")
                budget.check()
                identity = (command.kind, command.key)
                if receipt is not None and identity not in seen:
                    receipts.append(receipt)
                seen.add(identity)
        except SimulationFault:
            raise
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            raise SimulationFault(
                FailureCode.INCOMPATIBLE_INPUT, "invalid_paper_instruction"
            ) from error
        checkpoint = broker.checkpoint()  # Existing complete conservation/state validation.
        account = broker.account(scenario.initial_account.account_id)
        orders = tuple(broker.order(order_id) for order_id in order_ids)
        fills = broker.fills()
        with exact():
            fees = sum((f.fees.amount for f in fills), Decimal(0))
            taxes = sum((f.taxes.amount for f in fills), Decimal(0))
            slippage = sum(
                (
                    r.slippage.amount
                    for r in receipts
                    if r.fill is not None and r.slippage is not None
                ),
                Decimal(0),
            )
        execution: dict[str, Any] = {
            "scenario": scenario.reference(),
            "market": scenario.market.value,
            "currency": scenario.currency.value,
            "instruments": wire(scenario.instruments),
            "starting_account": wire(scenario.initial_account),
            "ending_account": wire(account),
            "starting_cash": wire(scenario.initial_account.balances),
            "ending_cash": wire(account.balances),
            "ending_positions": wire(account.positions),
            "orders": [{**wire(o), "risk_allowed": o.risk.allowed} for o in orders],
            "fills": wire(fills),
            "receipts": [r.to_dict() for r in receipts],
            "counts": {
                "orders": len(orders),
                "fills": len(fills),
                "rejections": sum(r.outcome == "rejected" for r in receipts),
                "risk_denied_orders": sum(not o.risk.allowed for o in orders),
                "cancellations": sum(r.outcome == "cancelled" for r in receipts),
                "pending_orders": sum(
                    o.status in {OrderStatus.OPEN, OrderStatus.PARTIAL} for o in orders
                ),
                "instructions": len(scenario.commands),
                "committed_commands": len(checkpoint.commands),
            },
            "costs": {
                "fees": wire(Money(fees, scenario.currency)),
                "taxes": wire(Money(taxes, scenario.currency)),
                "slippage": wire(Money(slippage, scenario.currency)),
                "slippage_already_in_fill_prices": True,
            },
            "paper_checkpoint": {
                "version": checkpoint.version,
                "state_id": checkpoint.state_id,
                "commands_hash": fingerprint(checkpoint.commands),
            },
            "valuation": {
                "available": False,
                "portfolio_value": None,
                "realized_pnl": None,
                "unrealized_pnl": None,
                "reason": "paper_terminal_valuation_not_projected",
            },
            "outcome": "completed",
            "error_classification": None,
        }
        result = {
            "version": RESULT_VERSION,
            "simulated": True,
            "data_origin": "synthetic",
            "usage_restriction": "synthetic_test_only",
            "experiment_id": experiment.experiment_id,
            "experiment_fingerprint": experiment.fingerprint,
            "run_id": experiment.run_id(job),
            "job_key": job.key,
            "seed": seed,
            "seed_usage": "recorded_no_stochastic_execution",
            "strategy": experiment.strategy.to_dict(),
            "code": experiment.code.to_dict(),
            "dataset": self.dataset_reference.to_dict(),
            "runner": self.reference.to_dict(),
            "execution_policy_hash": experiment.execution_policy.identifier,
            "execution": execution,
            "execution_fingerprint": digest(execution),
        }
        result["result_fingerprint"] = digest(result)
        output = FrozenJSON.freeze(result)
        try:
            bounded_json(output, experiment.limits.max_result_bytes)
        except ValueError as error:
            raise SimulationFault(FailureCode.BUDGET_EXHAUSTED, "paper_result_bytes") from error
        budget.check()
        return output

    def replay_validate(
        self, experiment: ExperimentSpec, job: SimulationJob, result: FrozenJSON, budget: RunBudget
    ) -> None:
        """Explicit opt-in semantic replay in a NEW broker, never called during scheduler resume."""
        require(
            self.run(experiment, job, experiment.run_seed(job), budget) == result,
            "paper_result_replay_mismatch",
        )


def verify_paper_result(result: FrozenJSON, expected_fingerprint: str) -> None:
    """Transport integrity against a trusted pin; semantic verification requires explicit replay."""
    bounded_json(result, 16 * 1024 * 1024)
    sha(expected_fingerprint)
    document = fields(
        result.to_dict(),
        "version simulated data_origin usage_restriction experiment_id experiment_fingerprint"
        " run_id job_key seed seed_usage strategy code dataset runner execution_policy_hash"
        " execution execution_fingerprint result_fingerprint",
    )
    require(
        document["data_origin"] == "synthetic"
        and document["usage_restriction"] == "synthetic_test_only"
        and document["seed_usage"] == "recorded_no_stochastic_execution",
        "paper_result_provenance_mismatch",
    )
    require(
        document.get("version") == RESULT_VERSION and document.get("simulated") is True,
        "invalid_paper_result_version",
    )
    require(document.get("result_fingerprint") == expected_fingerprint, "paper_result_pin_mismatch")
    require(
        digest({k: v for k, v in document.items() if k != "result_fingerprint"})
        == expected_fingerprint,
        "paper_result_hash_mismatch",
    )
    require(
        digest(document["execution"]) == document["execution_fingerprint"],
        "paper_execution_hash_mismatch",
    )
