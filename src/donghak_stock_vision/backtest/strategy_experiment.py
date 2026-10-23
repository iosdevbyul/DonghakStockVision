"""Bounded research plans; metadata only, no dataset reader or strategy execution."""

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import SimulationLimits, bounded_json
from donghak_stock_vision.backtest.simulation_validation import TimeWindow
from donghak_stock_vision.backtest.validation import integer, money, reference, require, sha, text
from donghak_stock_vision.data.learning import canonical, digest
from donghak_stock_vision.trading.domain import Currency, Market
from donghak_stock_vision.trading.paper_types import validate_owner, wire


@dataclass(frozen=True)
class ParameterGrid:
    """Finite typed choices, not a range generator or executable expression."""

    name: str
    kind: str
    values: tuple[int | str | bool, ...]
    minimum: str | None = None
    maximum: str | None = None

    def __post_init__(self) -> None:
        require(text(self.name).isidentifier() and len(self.name) <= 64, "invalid_parameter_name")
        require(self.kind in ("integer", "decimal", "boolean", "enum"), "parameter_type")
        require(type(self.values) is tuple and 0 < len(self.values) <= 64, "grid_axis_bound")
        numeric = self.kind in ("integer", "decimal")
        if numeric:
            require(self.minimum is not None and self.maximum is not None, "range_required")
            low = Decimal(money(self.minimum, signed=True))
            high = Decimal(money(self.maximum, signed=True))
            require(low <= high, "invalid_parameter_range")
        else:
            require(self.minimum is None and self.maximum is None, "unexpected_range")
        normalized: list[int | str | bool] = []
        for value in self.values:
            if self.kind == "integer":
                require(type(value) is int and abs(value) <= 2**63 - 1, "integer_parameter")
            elif self.kind == "decimal":
                require(type(value) is str, "decimal_string_parameter")
                value = money(value, signed=True)
            elif self.kind == "boolean":
                require(type(value) is bool, "boolean_parameter")
            else:
                require(type(value) is str and len(text(value)) <= 128, "enum_parameter")
            if numeric:
                require(low <= Decimal(value) <= high, "parameter_outside_range")
            normalized.append(value)
        require(len({canonical(v) for v in normalized}) == len(normalized), "duplicate_parameter")
        object.__setattr__(self, "values", tuple(normalized))


@dataclass(frozen=True)
class DatasetPartition:
    role: str
    cohort: FrozenJSON
    dataset: FrozenJSON
    window: TimeWindow
    provenance: FrozenJSON

    def __post_init__(self) -> None:
        require(self.role in ("training", "tuning", "oos", "holdout"), "partition_role")
        for item in (self.cohort, self.dataset, self.provenance):
            bounded_json(item, 4096)
            reference(item.to_dict())
        require(type(self.window) is TimeWindow, "partition_window_required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "cohort": self.cohort.to_dict(),
            "dataset": self.dataset.to_dict(),
            "window": wire(self.window),
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class ResearchPlan:
    experiment_id: str
    strategy: FrozenJSON
    code: FrozenJSON
    execution_policy: FrozenJSON
    risk_policy: FrozenJSON
    valuation_policy: FrozenJSON
    provenance: FrozenJSON
    market: Market
    currency: Currency
    partitions: tuple[DatasetPartition, ...]
    grid: tuple[ParameterGrid, ...]
    seed: int
    limits: SimulationLimits
    max_candidates: int
    max_configuration_bytes: int

    def __post_init__(self) -> None:
        require(len(text(self.experiment_id)) <= 128, "experiment_id_bound")
        for item in (
            self.strategy,
            self.code,
            self.execution_policy,
            self.risk_policy,
            self.valuation_policy,
            self.provenance,
        ):
            bounded_json(item, 4096)
            reference(item.to_dict())
        require(
            (self.market is Market.KR and self.currency is Currency.KRW)
            or (self.market is Market.US and self.currency is Currency.USD),
            "market_currency_mismatch",
        )
        require(type(self.partitions) is tuple and len(self.partitions) == 4, "four_partitions")
        require(
            all(type(p) is DatasetPartition for p in self.partitions), "partition_owner_required"
        )
        require(
            tuple(p.role for p in self.partitions) == ("training", "tuning", "oos", "holdout"),
            "partition_order",
        )
        require(len({p.cohort.identifier for p in self.partitions}) == 4, "duplicate_cohort")
        for left, right in zip(self.partitions, self.partitions[1:], strict=False):
            require(left.window.end <= right.window.start, "partition_overlap")
        require(type(self.grid) is tuple and 0 < len(self.grid) <= 16, "grid_dimension_bound")
        require(all(type(p) is ParameterGrid for p in self.grid), "grid_owner_required")
        require(len({p.name for p in self.grid}) == len(self.grid), "duplicate_parameter_name")
        require(tuple(sorted(self.grid, key=lambda p: p.name)) == self.grid, "grid_name_order")
        integer(self.seed)
        require(type(self.limits) is SimulationLimits, "simulation_limits_required")
        self.limits.__post_init__()
        require(0 < integer(self.max_candidates) <= 64, "candidate_budget")
        require(0 < integer(self.max_configuration_bytes) <= 65536, "configuration_budget")
        cardinality = 1
        for axis in self.grid:
            axis.__post_init__()
            cardinality *= len(axis.values)
            require(cardinality <= self.max_candidates, "candidate_count_exceeded")
        # Bound before Cartesian materialization, including the largest configuration.
        largest = {
            a.name: max(a.values, key=lambda v: len(canonical(v).encode())) for a in self.grid
        }
        require(
            len(canonical(largest).encode()) <= self.max_configuration_bytes,
            "configuration_bytes_exceeded",
        )
        bounded_json(self.document, min(self.limits.max_spec_bytes, 1024 * 1024))

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "strategy_research_plan_v1",
                "experiment_id": self.experiment_id,
                "strategy": self.strategy.to_dict(),
                "code": self.code.to_dict(),
                "execution_policy": self.execution_policy.to_dict(),
                "risk_policy": self.risk_policy.to_dict(),
                "valuation_policy": self.valuation_policy.to_dict(),
                "provenance": self.provenance.to_dict(),
                "market": self.market.value,
                "currency": self.currency.value,
                "partitions": [p.to_dict() for p in self.partitions],
                "grid": [wire(p) for p in self.grid],
                "seed": self.seed,
                "limits": self.limits.to_dict(),
                "max_candidates": self.max_candidates,
                "max_configuration_bytes": self.max_configuration_bytes,
                "accessible_roles": ["training", "tuning"],
                "usage_restriction": "research_only_no_selection_or_activation",
            }
        )

    def accessible_partition(self, role: str) -> DatasetPartition:
        require(role in ("training", "tuning"), "evaluation_feedback_forbidden")
        return next(p for p in self.partitions if p.role == role)


@dataclass(frozen=True)
class StrategyCandidate:
    """A planned configuration, never proof that a strategy executed it."""

    plan_hash: str
    parameters: FrozenJSON
    candidate_id: str

    def __post_init__(self) -> None:
        sha(self.plan_hash)
        bounded_json(self.parameters, 65536)
        require(
            self.candidate_id
            == digest({"plan": self.plan_hash, "parameters": self.parameters.to_dict()}),
            "candidate_identity_mismatch",
        )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "strategy_candidate_v1",
                "plan_hash": self.plan_hash,
                "parameters": self.parameters.to_dict(),
                "candidate_id": self.candidate_id,
            }
        )


def candidates(plan: ResearchPlan) -> tuple[StrategyCandidate, ...]:
    validate_owner(plan)
    plan.__post_init__()
    result = []
    for values in product(*(a.values for a in plan.grid)):
        parameters = FrozenJSON.freeze(dict(zip((a.name for a in plan.grid), values, strict=True)))
        identity = digest({"plan": plan.document.identifier, "parameters": parameters.to_dict()})
        result.append(StrategyCandidate(plan.document.identifier, parameters, identity))
    require(len({c.candidate_id for c in result}) == len(result), "duplicate_candidate_id")
    return tuple(result)
