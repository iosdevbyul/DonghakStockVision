"""Allowlisted strategy -> immutable scenario -> existing isolated Paper Broker pipeline."""

from dataclasses import dataclass
from time import monotonic
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler, Cancellation, RunBudget
from donghak_stock_vision.backtest.paper_scenario import PaperScenario
from donghak_stock_vision.backtest.paper_simulation import (
    PaperSimulationAdapter,
    verify_paper_result,
)
from donghak_stock_vision.backtest.simulation_comparison import PerformanceInput
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, bounded_json
from donghak_stock_vision.backtest.strategy_experiment import (
    ResearchPlan,
    StrategyCandidate,
    candidates,
)
from donghak_stock_vision.backtest.strategy_observations import (
    ObservationCase,
    PartitionAuthorization,
    SyntheticObservations,
    authorize_observations,
)
from donghak_stock_vision.backtest.validation import ContractError, require, sha
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.paper_reference import artifact, decide, parameters
from donghak_stock_vision.trading.domain import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Quantity,
)
from donghak_stock_vision.trading.paper import DeterministicPaperBroker
from donghak_stock_vision.trading.paper_types import (
    Cancellation as PaperCancellation,
)
from donghak_stock_vision.trading.paper_types import FillEvent, PaperCommand, Submission, wire


@dataclass(frozen=True)
class StrategyExecutionRequest:
    plan: ResearchPlan
    trusted_plan_hash: str
    observations: SyntheticObservations
    trusted_observations_hash: str
    authorization: PartitionAuthorization
    trusted_authorization_hash: str

    def verify(self) -> None:
        authorize_observations(
            self.plan,
            self.trusted_plan_hash,
            self.observations,
            self.trusted_observations_hash,
            self.authorization,
            self.trusted_authorization_hash,
        )


@dataclass(frozen=True)
class StrategyExecutionBatch:
    report: FrozenJSON
    inputs: tuple[PerformanceInput, ...]


def _instance(request: StrategyExecutionRequest, candidate: StrategyCandidate) -> FrozenJSON:
    # The actual parameters remain in the frozen strategy artifact, not hidden in a label.
    return FrozenJSON.freeze(
        {
            "version": "frozen_strategy_instance_v1",
            "candidate": candidate.document.to_dict(),
            "strategy_artifact": artifact().to_dict(),
            "code": request.plan.code.to_dict(),
            "observation_hash": request.trusted_observations_hash,
            "authorization_hash": request.trusted_authorization_hash,
        }
    )


def _compile(
    candidate: StrategyCandidate, case: ObservationCase
) -> tuple[PaperScenario, list[dict[str, Any]]]:
    _, _, quantity = parameters(candidate.parameters)
    initial = case.steps[0].decision_frame
    preview = DeterministicPaperBroker(case.initial_account, case.policy, initial)
    commands: list[PaperCommand] = []
    decisions = []
    opened = {OrderStatus.OPEN, OrderStatus.PARTIAL}
    for index, step in enumerate(case.steps):
        frame = step.decision_frame
        preview.advance(frame)
        commands.append(PaperCommand("advance", frame.frame_id, frame))
        quote = frame.quotes[0] if frame.quotes else None
        action = decide(candidate.parameters, quote.price.amount if quote is not None else None)
        identity = digest(
            {
                "candidate": candidate.candidate_id,
                "case": case.case_id,
                "step": index,
                "observation": digest(wire(frame)),
            }
        )
        order = None
        if action != "WAIT":
            order = OrderIntent(
                identity,
                case.initial_account.account_id,
                case.instrument,
                OrderSide.BUY if action == "BUY" else OrderSide.SELL,
                OrderType.MARKET,
                Quantity(quantity),
                frame.now,
                identity,
            )
            submission = Submission(order, identity, None)
            commands.append(PaperCommand("submit", identity, submission))
            # Original broker decides risk and reservations; strategy supplies no grant.
            preview.submit(
                order, idempotency_key=identity, risk=preview.preflight(order).to_decision()
            )
        decisions.append(
            {
                "decision_id": identity,
                "candidate_id": candidate.candidate_id,
                "case_id": case.case_id,
                "step": index,
                "decision_at": wire(frame.now),
                "observation_hash": digest(wire(frame)),
                "quote": wire(quote),
                "action": action,
                "rule": "missing_quote"
                if quote is None
                else "price_lte_entry"
                if action == "BUY"
                else "price_gte_exit"
                if action == "SELL"
                else "price_between_thresholds",
                "parameter_hash": candidate.parameters.identifier,
                "order": wire(order),
            }
        )
        execution = step.execution_frame
        preview.advance(execution)
        commands.append(PaperCommand("advance", execution.frame_id, execution))
        if order is not None and preview.order(order.order_id).status in opened:
            if step.capacity:
                key = digest({"decision": identity, "liquidity": step.liquidity_source_id})
                event = FillEvent(
                    key,
                    order.order_id,
                    Quantity(min(quantity, step.capacity)),
                    execution.now,
                    step.liquidity_source_id,
                )
                preview.execute(event)
                commands.append(PaperCommand("fill", key, event))
            if step.cancel_remainder and preview.order(order.order_id).status in opened:
                key = digest({"decision": identity, "cleanup": "fixture_cancel"})
                cancel = PaperCancellation(order.order_id, key, execution.now)
                preview.cancel(order.order_id, idempotency_key=key, at=execution.now)
                commands.append(PaperCommand("cancel", key, cancel))
    # Script construction consults authoritative broker state only to avoid invalid
    # fill/cancel commands after denial/closure. Final execution uses a NEW account.
    scenario = PaperScenario(
        digest({"candidate": candidate.candidate_id, "case": case.case_id}),
        case.instrument.market,
        case.instrument.currency,
        case.initial_account,
        case.policy,
        initial,
        tuple(commands),
    )
    return scenario, decisions


def prepare_strategy_candidate(
    request: StrategyExecutionRequest,
    candidate: StrategyCandidate,
) -> tuple[tuple[PaperScenario, ...], FrozenJSON]:
    """Explicit single-candidate decision-to-scenario boundary; always reauthorize sources.

    This returns immutable scripts/provenance, not a risk grant or execution receipt.
    The batch path uses the same compiler after its one request-wide authorization.
    """
    request.verify()
    require(
        type(candidate) is StrategyCandidate and candidate in candidates(request.plan),
        "candidate_not_in_authorized_plan",
    )
    parameters(candidate.parameters)
    compiled = [_compile(candidate, case) for case in request.observations.cases]
    return tuple(s for s, _ in compiled), FrozenJSON.freeze(
        {
            "version": "strategy_prepared_candidate_v1",
            "instance": _instance(request, candidate).to_dict(),
            "decisions": [d for _, rows in compiled for d in rows],
            "scenario_hashes": [s.fingerprint for s, _ in compiled],
        }
    )


def execute_strategy_candidates(request: StrategyExecutionRequest) -> StrategyExecutionBatch:
    request.verify()
    planned = candidates(request.plan)
    require(len(planned) <= 5, "strategy_execution_candidate_limit")
    require(
        len(planned) * len(request.observations.cases) <= request.plan.limits.max_runs,
        "strategy_execution_run_budget",
    )
    require(request.plan.limits.max_attempts_per_run == 1, "explicit_single_attempt_required")
    scenarios: list[PaperScenario] = []
    entries: list[dict[str, Any]] = []
    for candidate in planned:
        try:
            parameters(candidate.parameters)
        except ContractError:
            entries.append(
                {
                    "candidate": candidate.document.to_dict(),
                    "status": "failed",
                    "failure_classification": "invalid_configuration",
                    "reason": "unsupported_reference_parameters",
                    "decisions": [],
                    "strategy_instance": _instance(request, candidate).to_dict(),
                    "experiment": None,
                    "checkpoint_hash": None,
                }
            )
            continue
        compiled = [_compile(candidate, case) for case in request.observations.cases]
        scenarios.extend(s for s, _ in compiled)
        entries.append(
            {
                "candidate": candidate.document.to_dict(),
                "status": "prepared",
                "failure_classification": None,
                "reason": None,
                "decisions": [d for _, rows in compiled for d in rows],
                "strategy_instance": _instance(request, candidate).to_dict(),
                "scenario_ids": [s.scenario_id for s, _ in compiled],
            }
        )
    adapter = PaperSimulationAdapter(tuple(scenarios)) if scenarios else None
    inputs = []
    for entry in entries:
        if entry["status"] == "failed":
            continue
        assert adapter is not None
        candidate_id = entry["candidate"]["candidate_id"]
        instance = FrozenJSON.freeze(entry["strategy_instance"])
        strategy = FrozenJSON.freeze(
            {
                "artifact_id": candidate_id,
                "version": "frozen_strategy_instance_v1",
                "content_hash": instance.identifier,
            }
        )
        spec = ExperimentSpec(
            candidate_id,
            strategy,
            adapter.dataset_reference,
            request.plan.code,
            adapter.reference,
            # Shared RUNNER configuration. Strategy parameters live in the explicit
            # immutable specialized strategy artifact above and are independently replayed.
            FrozenJSON.freeze({"strategy_parameter_scope": "frozen_strategy_instance_v1"}),
            adapter.execution_policy,
            request.plan.seed,
            request.plan.market,
            request.plan.currency,
            "synthetic",
            request.plan.limits,
            tuple(
                adapter.job(c.case_id, sid)
                for c, sid in zip(
                    request.observations.cases, entry.pop("scenario_ids"), strict=True
                )
            ),
        )
        scheduler = BatchScheduler(spec)
        scheduler.run(adapter)
        checkpoint = scheduler.checkpoint()
        records = [r.to_dict() for r in scheduler.results]
        entry.update(
            status="completed"
            if all(r["status"] == "succeeded" for r in records)
            else "incomplete",
            experiment=spec.to_dict(),
            checkpoint_hash=checkpoint.identifier,
            runs=[
                {
                    "run_id": r["run_id"],
                    "job_key": r["job_key"],
                    "status": r["status"],
                    "failure": r["failure"],
                    "result_fingerprint": r["output"]["result_fingerprint"]
                    if r["output"] is not None
                    else None,
                }
                for r in records
            ],
        )
        inputs.append(PerformanceInput(spec, checkpoint, checkpoint.identifier, adapter, ()))
    value = {
        "version": "strategy_parameter_execution_v1",
        "simulated": True,
        "plan_hash": request.trusted_plan_hash,
        "source_dataset": request.observations.reference.to_dict(),
        "authorization_hash": request.trusted_authorization_hash,
        "role": request.authorization.role,
        "strategy_artifact": artifact().to_dict(),
        "candidates": entries,
        "ranking": None,
        "operational_eligible": False,
        "historical_pit_approved": False,
        "limitations": [
            "synthetic_price_band_not_model_inference",
            "no_oos_or_holdout_access",
            "fixture_liquidity_not_realistic_market_execution",
            "source_dataset_distinct_from_derived_scenario_registry",
            "strategy_evaluation_v1_namespace_translation_not_implemented",
        ],
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    bounded_json(report, min(request.plan.limits.max_total_result_bytes, 4 * 1024 * 1024))
    require(
        sum(len(i.checkpoint.payload_json.encode()) for i in inputs)
        + len(report.payload_json.encode())
        <= request.plan.limits.max_total_result_bytes,
        "strategy_batch_output_budget",
    )
    return StrategyExecutionBatch(report, tuple(inputs))


def verify_strategy_execution(
    batch: StrategyExecutionBatch,
    trusted_report_hash: str,
    request: StrategyExecutionRequest,
) -> None:
    """Rebuild strategy decisions and independently replay original paper receipts."""
    require(type(batch) is StrategyExecutionBatch, "strategy_batch_owner_required")
    bounded_json(batch.report, 4 * 1024 * 1024)
    value = batch.report.to_dict()
    require(
        value.get("content_fingerprint")
        == sha(trusted_report_hash)
        == digest({k: v for k, v in value.items() if k != "content_fingerprint"}),
        "strategy_report_pin_mismatch",
    )
    fresh = execute_strategy_candidates(request)
    require(batch.report == fresh.report, "strategy_decision_replay_mismatch")
    require(
        type(batch.inputs) is tuple and len(batch.inputs) == len(fresh.inputs),
        "strategy_owner_count",
    )
    for owner, original in zip(batch.inputs, fresh.inputs, strict=True):
        require(
            type(owner) is PerformanceInput
            and owner.experiment == original.experiment
            and owner.checkpoint == original.checkpoint
            and owner.trusted_checkpoint_hash == original.trusted_checkpoint_hash
            and not owner.valuations,
            "strategy_paper_owner_mismatch",
        )
        adapter = PaperSimulationAdapter(owner.adapter.scenarios)
        require(
            adapter.dataset_reference == original.adapter.dataset_reference
            and adapter.reference == original.adapter.reference,
            "strategy_scenario_replay_mismatch",
        )
        scheduler = BatchScheduler(
            owner.experiment,
            checkpoint=owner.checkpoint,
            expected_checkpoint_hash=owner.trusted_checkpoint_hash,
        )
        for record in scheduler.results:
            row = record.to_dict()
            if row["status"] == "succeeded":
                output = FrozenJSON.freeze(row["output"])
                verify_paper_result(output, output.to_dict()["result_fingerprint"])
                job = next(j for j in owner.experiment.jobs if j.key == row["job_key"])
                adapter.replay_validate(
                    owner.experiment,
                    job,
                    output,
                    RunBudget(owner.experiment, Cancellation(), monotonic),
                )
