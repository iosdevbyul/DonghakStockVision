"""Immutable orchestration inputs; references describe evidence, never approve it."""

from dataclasses import dataclass
from typing import Any, ClassVar

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import integer, reference, require, text
from donghak_stock_vision.data.learning import canonical, digest
from donghak_stock_vision.trading.domain import Currency, Market


def bounded_json(value: FrozenJSON, maximum: int) -> None:
    require(type(value) is FrozenJSON, "frozen_json_required")
    require(len(value.payload_json.encode()) <= maximum, "json_budget_exceeded")
    # Also rejects nonfinite numbers and detached documents modified after construction.
    require(canonical(value.to_dict()) == value.payload_json, "noncanonical_json")


@dataclass(frozen=True)
class SimulationLimits:
    """Mandatory budgets. Sequential/cooperative, not an OS resource sandbox."""

    max_runs: int
    max_attempts_per_run: int
    concurrency: int
    max_steps_per_run: int
    max_run_seconds: int
    max_result_bytes: int
    max_total_result_bytes: int
    max_spec_bytes: int

    def __post_init__(self) -> None:
        for value in vars(self).values():
            integer(value, positive=True)
        require(self.max_runs <= 10_000, "run_limit_exceeds_foundation_cap")
        require(self.max_attempts_per_run <= 10, "attempt_limit_exceeds_foundation_cap")
        require(self.concurrency == 1, "only_sequential_execution_supported")
        require(self.max_steps_per_run <= 10**9, "step_limit_exceeds_foundation_cap")
        require(self.max_run_seconds <= 86_400, "time_limit_exceeds_foundation_cap")
        require(
            self.max_result_bytes <= self.max_total_result_bytes <= 16 * 1024 * 1024,
            "invalid_result_byte_limits",
        )
        require(self.max_spec_bytes <= 4 * 1024 * 1024, "spec_limit_exceeds_foundation_cap")

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


@dataclass(frozen=True)
class SimulationJob:
    key: str
    parameters: FrozenJSON

    def __post_init__(self) -> None:
        require(len(text(self.key)) <= 128, "job_key_too_long")
        bounded_json(self.parameters, 16 * 1024)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "parameters": self.parameters.to_dict()}


@dataclass(frozen=True)
class ExperimentSpec:
    """One immutable namespace; actual input custody remains the adapter's obligation."""

    experiment_id: str
    strategy: FrozenJSON
    dataset: FrozenJSON
    code: FrozenJSON
    runner: FrozenJSON
    parameters: FrozenJSON
    execution_policy: FrozenJSON
    seed: int
    market: Market
    currency: Currency
    data_origin: str
    limits: SimulationLimits
    jobs: tuple[SimulationJob, ...]

    version: ClassVar[str] = "mass_simulation_experiment_v1"

    def __post_init__(self) -> None:
        require(len(text(self.experiment_id)) <= 128, "experiment_id_too_long")
        integer(self.seed)
        require(isinstance(self.market, Market), "invalid_market")
        require(isinstance(self.currency, Currency), "invalid_currency")
        require(
            self.currency is (Currency.KRW if self.market is Market.KR else Currency.USD),
            "market_currency_mismatch",
        )
        require(self.data_origin in {"synthetic", "real"}, "invalid_data_origin")
        require(type(self.limits) is SimulationLimits, "limits_required")
        self.limits.__post_init__()
        for value in (self.strategy, self.dataset, self.code, self.runner):
            bounded_json(value, 4096)
            reference(value.to_dict())  # Reuse existing artifact_id/version/content_hash contract.
        for value in (self.parameters, self.execution_policy):
            bounded_json(value, 16 * 1024)
        require(type(self.jobs) is tuple, "immutable_jobs_required")
        require(0 < len(self.jobs) <= self.limits.max_runs, "run_count_outside_limits")
        for job in self.jobs:
            require(type(job) is SimulationJob, "job_required")
            job.__post_init__()
        require(len({j.key for j in self.jobs}) == len(self.jobs), "duplicate_job_id")
        require(
            len(canonical(self.to_dict()).encode()) <= self.limits.max_spec_bytes,
            "spec_budget_exceeded",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "experiment_id": self.experiment_id,
            "strategy": self.strategy.to_dict(),
            "dataset": self.dataset.to_dict(),
            "code": self.code.to_dict(),
            "runner": self.runner.to_dict(),
            "parameters": self.parameters.to_dict(),
            "execution_policy": self.execution_policy.to_dict(),
            "seed": self.seed,
            "market": self.market.value,
            "currency": self.currency.value,
            "data_origin": self.data_origin,
            "usage_restriction": (
                "synthetic_test_only" if self.data_origin == "synthetic" else "research_only"
            ),
            "limits": self.limits.to_dict(),
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def run_id(self, job: SimulationJob) -> str:
        require(job in self.jobs, "job_not_in_experiment")
        return digest({"experiment": self.fingerprint, "job": job.to_dict()})

    def run_seed(self, job: SimulationJob) -> int:
        require(job in self.jobs, "job_not_in_experiment")
        return int(digest({"seed": self.seed, "job_key": job.key})[:16], 16)
