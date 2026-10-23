"""Bounded multidimensional research search composed with original v1 execution/replay."""

from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import product
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.strategy_execution import StrategyExecutionRequest
from donghak_stock_vision.backtest.strategy_experiment import ResearchPlan, candidates
from donghak_stock_vision.backtest.strategy_improvement import (
    ImprovementPlan,
    ImprovementResult,
    TuningMarks,
    run_improvement,
    verify_improvement,
)
from donghak_stock_vision.backtest.strategy_observations import (
    PartitionAuthorization,
    SyntheticObservations,
)
from donghak_stock_vision.backtest.validation import ContractError, integer, require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.paper_reference import parameters
from donghak_stock_vision.trading.paper_types import validate_owner

Coordinates = tuple[int, ...]


@dataclass(frozen=True)
class MultiParameterPlan:
    cycle_id: str
    source: StrategyExecutionRequest
    valuation_policy: FrozenJSON
    trusted_policy_hash: str
    marks: tuple[TuningMarks, ...]
    initial: tuple[Coordinates, ...]
    metric: str
    minimum_coverage: str
    max_valid_candidates: int
    max_generations: int
    max_attempts: int
    max_simulation_runs: int
    proposals_per_generation: int
    max_candidate_bytes: int
    max_retained_bytes: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        require(len(text(self.cycle_id)) <= 128, "cycle_identity_bound")
        require(type(self.source) is StrategyExecutionRequest, "tuning_source_required")
        require(
            type(self.source.authorization) is PartitionAuthorization
            and type(self.source.observations) is SyntheticObservations,
            "tuning_source_required",
        )
        require(self.source.authorization.role in ("training", "tuning"), "feedback_role_forbidden")
        require(type(self.source.plan) is ResearchPlan, "source_plan_required")
        grid = self.source.plan.grid
        require(tuple(a.name for a in grid) == ("entry", "exit", "quantity"), "reference_axes")
        require(2 <= sum(len(a.values) > 1 for a in grid) <= 3, "varying_dimension_bound")
        cardinality = 1
        for axis in grid:
            require(len(axis.values) <= 8, "axis_value_bound")
            cardinality *= len(axis.values)
            require(cardinality <= 64, "cartesian_bound")
        require(0 < integer(self.max_valid_candidates) <= 64, "valid_candidate_budget")
        # Count validity using original strategy validation on pairs and quantities,
        # BEFORE constructing any complete Cartesian candidate collection.
        pairs = quantities = 0
        for entry in grid[0].values:
            for exit_price in grid[1].values:
                try:
                    parameters(
                        FrozenJSON.freeze({"entry": entry, "exit": exit_price, "quantity": 1})
                    )
                    pairs += 1
                except ContractError:
                    pass
        for quantity in grid[2].values:
            try:
                parameters(FrozenJSON.freeze({"entry": "1", "exit": "2", "quantity": quantity}))
                quantities += 1
            except ContractError:
                pass
        require(pairs * quantities <= self.max_valid_candidates, "valid_candidate_budget_exceeded")
        require(type(self.initial) is tuple and 0 < len(self.initial) <= 5, "initial_bound")
        require(len(set(self.initial)) == len(self.initial), "duplicate_initial")
        for coords in self.initial:
            self.check_coordinates(coords)
        require(0 < integer(self.max_generations) <= 16, "generation_budget")
        require(len(self.initial) <= integer(self.max_attempts) <= 64, "attempt_budget")
        require(0 < integer(self.max_simulation_runs) <= 256, "simulation_run_budget")
        require(0 < integer(self.proposals_per_generation) <= 5, "proposal_budget")
        require(
            0
            < integer(self.max_candidate_bytes)
            <= integer(self.max_retained_bytes)
            <= 16 * 1024 * 1024,
            "retained_byte_limits",
        )
        require(0 < integer(self.max_output_bytes) <= 16 * 1024 * 1024, "output_budget")
        self.source.verify()
        # Reuse original policy, mark and execution authorization after the pre-expansion bounds.
        self._bridge((0, 0, 0), None, 0)
        bounded_json(self.document, self.source.plan.limits.max_spec_bytes)

    def check_coordinates(self, coords: Coordinates) -> None:
        require(type(coords) is tuple and len(coords) == 3, "coordinate_dimensions")
        require(
            all(
                0 <= integer(i) < len(a.values)
                for i, a in zip(coords, self.source.plan.grid, strict=True)
            ),
            "coordinate_range",
        )

    def _bridge(self, coords: Coordinates, parent: str | None, generation: int) -> ImprovementPlan:
        # Narrow namespace bridge: all actual parameters still use original v1 candidate contracts.
        require(type(self.source) is StrategyExecutionRequest, "tuning_source_required")
        grid = self.source.plan.grid
        require(len(grid) == 3, "reference_axes")
        selected = tuple(
            replace(a, values=(a.values[i],)) for a, i in zip(grid, coords, strict=True)
        )
        # A cycle's independent pin binds all source identities, policies and limits below.
        provenance = FrozenJSON.freeze(
            {
                "artifact_id": "multi-parameter-generation-bridge",
                "version": "v1",
                "content_hash": digest(
                    {
                        "cycle_id": self.cycle_id,
                        "source": self.source.trusted_plan_hash,
                        "coordinates": list(coords),
                        "parent": parent,
                        "generation": generation,
                    }
                ),
            }
        )
        original = replace(self.source.plan, grid=selected, provenance=provenance)
        request = replace(
            self.source, plan=original, trusted_plan_hash=original.document.identifier
        )
        # Independent raw source-plan pin must also hold: deriving a plan must not bless tampering.
        require(
            self.source.plan.document.identifier == sha(self.source.trusted_plan_hash),
            "source_plan_pin",
        )
        return ImprovementPlan(
            "multi-bridge-" + provenance.to_dict()["content_hash"],
            request,
            self.valuation_policy,
            self.trusted_policy_hash,
            self.marks,
            "entry",
            (0,),
            self.metric,
            self.minimum_coverage,
            1,
            1,
            self.max_candidate_bytes,
        )

    @property
    def document(self) -> FrozenJSON:
        # Reuse the v1 policy/mark/source serialization; include the FULL original search grid pin.
        bridge = self._bridge((0, 0, 0), None, 0)
        return FrozenJSON.freeze(
            {
                "version": "multi_parameter_improvement_plan_v1",
                "cycle_id": self.cycle_id,
                "source_plan_hash": self.source.trusted_plan_hash,
                "execution_template": bridge.document.to_dict(),
                "initial": [list(c) for c in self.initial],
                "max_valid_candidates": self.max_valid_candidates,
                "max_generations": self.max_generations,
                "max_attempts": self.max_attempts,
                "max_simulation_runs": self.max_simulation_runs,
                "proposals_per_generation": self.proposals_per_generation,
                "max_candidate_bytes": self.max_candidate_bytes,
                "max_retained_bytes": self.max_retained_bytes,
                "max_output_bytes": self.max_output_bytes,
                "proposal_policy": "complete_case_best_l1_grid_neighborhood_v1",
                "dimensions": 3,
                "axis_order": [a.name for a in self.source.plan.grid],
                "operational_eligible": False,
            }
        )


@dataclass(frozen=True)
class SearchPoint:
    coordinates: Coordinates
    configuration: FrozenJSON
    invalid_reason: str | None

    @property
    def identity(self) -> str:
        return self.configuration.identifier


def parameter_space(plan: MultiParameterPlan) -> tuple[SearchPoint, ...]:
    require(type(plan) is MultiParameterPlan, "multi_plan_required")
    # Reject forbidden source owners before recursively validating any nested object.
    plan.__post_init__()
    validate_owner(plan)
    # All cardinality/bounds above precede Cartesian materialization.
    result = []
    valid = 0
    coordinates = product(*(range(len(a.values)) for a in plan.source.plan.grid))
    for coords, candidate in zip(coordinates, candidates(plan.source.plan), strict=True):
        reason = None
        try:
            parameters(candidate.parameters)
            valid += 1
            require(valid <= plan.max_valid_candidates, "valid_candidate_budget_exceeded")
        except ContractError as exc:
            if str(exc) == "valid_candidate_budget_exceeded":
                raise
            reason = str(exc)
        result.append(SearchPoint(coords, candidate.parameters, reason))
    return tuple(result)


def neighbors(
    space: tuple[SearchPoint, ...],
    anchor: Coordinates,
    visited: set[Coordinates],
    limit: int,
) -> tuple[Coordinates, ...]:
    require(0 < integer(limit) <= 5, "proposal_budget")
    return tuple(
        p.coordinates
        for p in sorted(
            (p for p in space if p.invalid_reason is None and p.coordinates not in visited),
            key=lambda p: (
                sum(abs(a - b) for a, b in zip(p.coordinates, anchor, strict=True)),
                p.coordinates,
            ),
        )[:limit]
    )


@dataclass(frozen=True)
class CandidateEvidence:
    cycle_hash: str
    generation: int
    parent_hash: str | None
    coordinates: Coordinates
    result: ImprovementResult | None
    failure: str | None


@dataclass(frozen=True)
class MultiParameterResult:
    report: FrozenJSON
    evidence: tuple[CandidateEvidence, ...]


def _execute(
    plan: MultiParameterPlan,
    point: SearchPoint,
    generation: int,
    parent: str | None,
) -> tuple[CandidateEvidence, dict[str, Any], int]:
    bridge = plan._bridge(point.coordinates, parent, generation)
    try:
        result = run_improvement(bridge, bridge.document.identifier)
    except ContractError as exc:
        # Never present a failed execution/verification as an available return.
        reason = str(exc)
        evidence = CandidateEvidence(
            plan.document.identifier, generation, parent, point.coordinates, None, reason
        )
        return (
            evidence,
            {
                "coordinates": list(point.coordinates),
                "configuration_hash": point.identity,
                "parameters": point.configuration.to_dict(),
                "status": "failed",
                "failure": reason,
                "eligible": False,
                "exclusion": "execution_or_evidence_contract_failure",
                "objective": None,
                "evaluated": 0,
                "expected": len(plan.source.observations.cases),
            },
            0,
        )
    # v1 performs independent replay while constructing each original generation already.
    value = result.report.to_dict()
    row = dict(value["generations"][0]["assessments"][0])
    complete = row["evaluated"] == row["expected"]
    row["eligible"] = row["eligible"] and complete
    if not complete:
        row["exclusion"] = "incomplete_evaluation_case_membership"
    row.update(
        coordinates=list(point.coordinates),
        configuration_hash=point.identity,
        bridge_plan_hash=bridge.document.identifier,
        bridge_report_hash=value["content_fingerprint"],
    )
    row.pop("axis_index")
    size = value["retained_evidence_bytes"] + len(result.report.payload_json.encode())
    require(size <= plan.max_candidate_bytes, "candidate_output_budget")
    return (
        CandidateEvidence(
            plan.document.identifier, generation, parent, point.coordinates, result, None
        ),
        row,
        size,
    )


def run_multi_parameter(
    plan: MultiParameterPlan,
    trusted_plan_hash: str,
    *,
    cancel_before_generation: int | None = None,
) -> MultiParameterResult:
    require(type(plan) is MultiParameterPlan, "multi_plan_required")
    space = parameter_space(plan)
    require(plan.document.identifier == sha(trusted_plan_hash), "multi_plan_pin")
    if cancel_before_generation is not None:
        require(0 <= integer(cancel_before_generation) <= plan.max_generations, "cancel_boundary")
    keyed = {p.coordinates: p for p in space}
    proposed = plan.initial
    visited: set[Coordinates] = set()
    history: list[dict[str, Any]] = []
    evidence = []
    generations = []
    retained = runs = attempts = 0
    parent: str | None = None
    state, reason = "completed", "generation_budget_exhausted"
    for generation in range(plan.max_generations):
        if generation == cancel_before_generation:
            state, reason = "stopped", "cancelled_at_generation_boundary"
            break
        valid = tuple(c for c in proposed if keyed[c].invalid_reason is None)
        required_runs = len(valid) * len(plan.source.observations.cases)
        if attempts + len(valid) > plan.max_attempts:
            state, reason = "stopped", "attempt_budget_exhausted"
            break
        if runs + required_runs > plan.max_simulation_runs:
            state, reason = "stopped", "simulation_run_budget_exhausted"
            break
        if retained + len(valid) * plan.max_candidate_bytes > plan.max_retained_bytes:
            state, reason = "stopped", "retained_byte_reservation_exhausted"
            break
        rows = []
        for coords in proposed:
            require(coords not in visited, "duplicate_configuration")
            point = keyed[coords]
            visited.add(coords)
            if point.invalid_reason is not None:
                rows.append(
                    {
                        "coordinates": list(coords),
                        "configuration_hash": point.identity,
                        "parameters": point.configuration.to_dict(),
                        "status": "invalid",
                        "failure": point.invalid_reason,
                        "eligible": False,
                        "objective": None,
                    }
                )
                continue
            owner, row, size = _execute(plan, point, generation, parent)
            attempts += 1
            runs += len(plan.source.observations.cases)
            retained += size
            require(retained <= plan.max_retained_bytes, "retained_output_budget")
            evidence.append(owner)
            rows.append(row)
        history.extend(rows)
        eligible = [r for r in history if r["eligible"]]
        anchor = None
        next_points: tuple[Coordinates, ...] = ()
        if eligible:
            best = min(
                eligible,
                key=lambda r: (
                    -Fraction(int(r["objective"]["numerator"]), int(r["objective"]["denominator"])),
                    tuple(r["coordinates"]),
                ),
            )
            anchor = tuple(best["coordinates"])
            next_points = neighbors(space, anchor, visited, plan.proposals_per_generation)
        record: dict[str, Any] = {
            "generation": generation,
            "parent_hash": parent,
            "assessments": rows,
            "research_priority_anchor": list(anchor) if anchor is not None else None,
            "next_coordinates": [list(c) for c in next_points],
        }
        record["generation_hash"] = digest(record)
        generations.append(record)
        parent = record["generation_hash"]
        # Explicit cancellation wins over prior failures or exhaustion at this boundary.
        if cancel_before_generation == generation + 1:
            state, reason = "stopped", "cancelled_at_generation_boundary"
            break
        if not eligible:
            state, reason = "stopped", "no_complete_eligible_tuning_feedback"
            break
        if not next_points:
            reason = "finite_search_exhausted"
            break
        proposed = next_points
    value = {
        "version": "multi_parameter_improvement_report_v1",
        "cycle_hash": trusted_plan_hash,
        "cycle_id": plan.cycle_id,
        "state": state,
        "stop_reason": reason,
        "events": ["planned", "running", state],
        "generations": generations,
        "attempts": attempts,
        "simulation_runs_reserved": runs,
        "retained_evidence_bytes": retained,
        "cancel_before_generation": cancel_before_generation,
        "configurations": [
            {
                "coordinates": list(p.coordinates),
                "configuration_hash": p.identity,
                "parameters": p.configuration.to_dict(),
                "status": "invalid"
                if p.invalid_reason
                else ("attempted" if p.coordinates in visited else "skipped"),
                "reason": p.invalid_reason or (None if p.coordinates in visited else reason),
            }
            for p in space
        ],
        "cohort": plan.source.plan.accessible_partition(
            plan.source.authorization.role
        ).cohort.to_dict(),
        "feedback_role": plan.source.authorization.role,
        "simulated": True,
        "operational_eligible": False,
        "production_selection": None,
        "oos_access": False,
        "holdout_access": False,
        "comparison_scope": "same_authorized_source_cases_original_native_currency_valuations",
    }
    value["content_fingerprint"] = digest(value)
    report = FrozenJSON.freeze(value)
    # Separate final output check; pre-generation reservations do not replace it.
    bounded_json(report, plan.max_output_bytes - retained)
    return MultiParameterResult(report, tuple(evidence))


def verify_multi_parameter(
    result: MultiParameterResult,
    trusted_report_hash: str,
    plan: MultiParameterPlan,
    trusted_plan_hash: str,
    *,
    cancel_before_generation: int | None = None,
) -> None:
    require(type(plan) is MultiParameterPlan, "multi_plan_required")
    require(type(result) is MultiParameterResult, "multi_result_owner_required")
    bounded_json(result.report, plan.max_output_bytes)
    require(
        type(result.evidence) is tuple
        and len(result.evidence) <= plan.max_attempts
        and all(type(e) is CandidateEvidence for e in result.evidence),
        "multi_evidence_bound",
    )
    require(
        result.report.to_dict().get("content_fingerprint") == sha(trusted_report_hash),
        "multi_report_pin",
    )
    # Independently reconstruct caught failures, ordering, budgets and proposals.
    fresh = run_multi_parameter(
        plan, trusted_plan_hash, cancel_before_generation=cancel_before_generation
    )
    require(result == fresh, "multi_semantic_replay_mismatch")
    # Original owner verification remains independently callable/authoritative.
    for owner in result.evidence:
        if owner.result is not None:
            bridge = plan._bridge(owner.coordinates, owner.parent_hash, owner.generation)
            verify_improvement(
                owner.result,
                owner.result.report.to_dict()["content_fingerprint"],
                bridge,
                bridge.document.identifier,
            )
