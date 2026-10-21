"""Sequential, bounded research scheduling. No data access, economic ledger or worker pool."""

import math
from collections.abc import Callable
from enum import Enum
from threading import Event
from time import monotonic
from typing import Any, ClassVar, Protocol

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import (
    ExperimentSpec,
    SimulationJob,
    bounded_json,
)
from donghak_stock_vision.backtest.validation import fields, integer, require, sha, text
from donghak_stock_vision.data.learning import canonical, digest


class FailureCode(Enum):
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_INPUT = "missing_input"
    INCOMPATIBLE_INPUT = "incompatible_input"
    EXECUTION_EXCEPTION = "execution_exception"
    RISK_DENIED = "risk_denied"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    CHECKPOINT_MISMATCH = "checkpoint_mismatch"
    CHECKPOINT_CORRUPTION = "checkpoint_corruption"
    RESULT_INTEGRITY = "result_integrity_mismatch"


class SimulationFault(ValueError):
    """Structured infrastructure/adapter diagnostic, never a profitability signal."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        require(isinstance(code, FailureCode), "failure_code_required")
        require(len(text(detail)) <= 256, "failure_detail_too_long")
        self.code, self.detail = code, detail
        super().__init__(f"{code.value}:{detail}")


class Cancellation:
    """Explicit caller-owned stop signal; never starts a background worker."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


class RunBudget:
    """Cooperative deadline and logical work budget; cannot preempt arbitrary adapter code."""

    def __init__(
        self, spec: ExperimentSpec, cancellation: Cancellation, clock: Callable[[], float]
    ) -> None:
        self._limit = spec.limits
        self._cancellation = cancellation
        self._clock = clock
        self._last = self._now()
        self._start = self._last
        self._steps = 0

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in {int, float} or not math.isfinite(value):
            raise SimulationFault(FailureCode.INVALID_CONFIGURATION, "invalid_monotonic_clock")
        return float(value)

    @property
    def steps(self) -> int:
        return self._steps

    def check(self) -> None:
        if self._cancellation.requested:
            raise SimulationFault(FailureCode.CANCELLED, "caller_requested_stop")
        now = self._now()
        if now < self._last:
            raise SimulationFault(FailureCode.INVALID_CONFIGURATION, "clock_moved_backwards")
        self._last = now
        if now - self._start >= self._limit.max_run_seconds:
            raise SimulationFault(FailureCode.TIMEOUT, "cooperative_deadline_exceeded")

    def consume(self, steps: int = 1) -> None:
        integer(steps, positive=True)
        self.check()
        if steps > self._limit.max_steps_per_run - self._steps:
            raise SimulationFault(FailureCode.BUDGET_EXHAUSTED, "logical_step_limit")
        self._steps += steps


class SimulationRunner(Protocol):
    """Adapter must validate referenced inputs and return bounded, deterministic JSON.

    No integration with BacktestRunner or Paper Broker is implied by this port.
    Adapters must use the supplied seed/clock policy and call budget.consume/check.
    """

    @property
    def reference(self) -> FrozenJSON: ...

    def run(
        self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
    ) -> FrozenJSON: ...


class BatchScheduler:
    """In-memory immutable checkpoint head, owned by one caller; sequential execution only.

    Results are kept in spec order. Cancelled/interrupted attempts are retained on
    resume; succeeded/failed jobs are never retried. No durable crash recovery.
    """

    version: ClassVar[str] = "mass_simulation_checkpoint_v1"
    result_version: ClassVar[str] = "mass_simulation_run_v1"

    def __init__(
        self,
        experiment: ExperimentSpec,
        *,
        checkpoint: FrozenJSON | None = None,
        expected_checkpoint_hash: str | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        require(type(experiment) is ExperimentSpec, "experiment_required")
        experiment.__post_init__()
        require(callable(clock), "monotonic_clock_required")
        self._experiment = experiment
        self._fingerprint = experiment.fingerprint
        self._indices = {job.key: i for i, job in enumerate(experiment.jobs)}
        self._run_ids = {job.key: experiment.run_id(job) for job in experiment.jobs}
        self._seeds = {job.key: experiment.run_seed(job) for job in experiment.jobs}
        self._clock = clock
        self._running = False
        if checkpoint is None:
            require(expected_checkpoint_hash is None, "checkpoint_required_for_pin")
            self._head = self._document([])
        else:
            if expected_checkpoint_hash is None:
                raise SimulationFault(FailureCode.CHECKPOINT_MISMATCH, "trusted_pin_required")
            sha(expected_checkpoint_hash)
            try:
                require(type(checkpoint) is FrozenJSON, "frozen_checkpoint_required")
                require(
                    checkpoint.identifier == expected_checkpoint_hash, "checkpoint_hash_mismatch"
                )
                document = checkpoint.to_dict()
                if (
                    document.get("version") != self.version
                    or document.get("experiment_fingerprint") != self._fingerprint
                ):
                    raise SimulationFault(
                        FailureCode.CHECKPOINT_MISMATCH, "version_or_experiment_mismatch"
                    )
                self._validate(checkpoint)
            except SimulationFault:
                raise
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
                raise SimulationFault(
                    FailureCode.CHECKPOINT_CORRUPTION, "invalid_checkpoint_or_pin"
                ) from error
            self._head = checkpoint

    @property
    def experiment(self) -> ExperimentSpec:
        return self._experiment

    def _document(self, records: list[dict[str, Any]]) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": self.version,
                "experiment_fingerprint": self._fingerprint,
                "records": records,
            }
        )

    def _validate(self, checkpoint: FrozenJSON) -> None:
        # Fixed metadata allowance per bounded attempt, plus retained successful output bytes.
        maximum = (
            self.experiment.limits.max_total_result_bytes
            + len(self.experiment.jobs) * self.experiment.limits.max_attempts_per_run * 4096
        )
        bounded_json(checkpoint, maximum)
        doc = fields(checkpoint.to_dict(), "version experiment_fingerprint records")
        require(doc["version"] == self.version, "unknown_checkpoint_version")
        require(
            doc["experiment_fingerprint"] == self._fingerprint,
            "checkpoint_experiment_mismatch",
        )
        records = doc["records"]
        require(type(records) is list, "records_required")
        require(
            len(records) <= len(self.experiment.jobs) * self.experiment.limits.max_attempts_per_run,
            "attempt_history_limit",
        )
        previous_index = -1
        counts: dict[str, int] = {}
        terminal: set[str] = set()
        output_bytes = 0
        for r in records:
            fields(
                r,
                "version experiment_fingerprint run_id job_key attempt seed status steps output"
                " failure result_hash",
            )
            require(r["version"] == self.result_version, "unknown_result_version")
            require(r["experiment_fingerprint"] == self._fingerprint, "run_owner_mismatch")
            index = self._indices.get(r["job_key"], -1)
            require(
                index >= 0 and previous_index <= index <= previous_index + 1, "run_order_mismatch"
            )
            job = self.experiment.jobs[index]
            require(job.key not in terminal, "duplicate_terminal_run")
            count = counts.get(job.key, 0) + 1
            require(
                integer(r["attempt"], positive=True)
                == count
                <= self.experiment.limits.max_attempts_per_run,
                "attempt_limit",
            )
            require(r["run_id"] == self._run_ids[job.key], "run_identity_mismatch")
            require(r["seed"] == self._seeds[job.key], "run_seed_mismatch")
            require(integer(r["steps"]) <= self.experiment.limits.max_steps_per_run, "step_limit")
            require(
                r["status"] in {"succeeded", "failed", "cancelled", "interrupted"}, "invalid_status"
            )
            sha(r["result_hash"])
            require(
                digest({k: v for k, v in r.items() if k != "result_hash"}) == r["result_hash"],
                "result_hash_mismatch",
            )
            if r["status"] == "succeeded":
                require(r["failure"] is None, "successful_failure_conflict")
                payload = FrozenJSON.freeze(fields_object(r["output"]))
                bounded_json(payload, self.experiment.limits.max_result_bytes)
                output_bytes += len(payload.payload_json.encode())
            else:
                require(r["output"] is None, "unsuccessful_output")
                failure = fields(r["failure"], "code detail")
                code = FailureCode(failure["code"])
                require(len(text(failure["detail"])) <= 256, "failure_detail_too_long")
                expected = {
                    "cancelled": FailureCode.CANCELLED,
                    "interrupted": FailureCode.INTERRUPTED,
                }
                require(
                    (code is expected[r["status"]])
                    if r["status"] in expected
                    else code not in {FailureCode.CANCELLED, FailureCode.INTERRUPTED},
                    "failure_status_mismatch",
                )
            if index > previous_index and previous_index >= 0:
                require(
                    self.experiment.jobs[previous_index].key in terminal, "unfinished_previous_run"
                )
            if r["status"] in {"succeeded", "failed"}:
                terminal.add(job.key)
            else:
                require(
                    count < self.experiment.limits.max_attempts_per_run, "exhausted_retryable_run"
                )
            counts[job.key] = count
            previous_index = index
        require(
            output_bytes <= self.experiment.limits.max_total_result_bytes, "total_result_budget"
        )

    def checkpoint(self) -> FrozenJSON:
        """Immutable logical checkpoint; retain its hash in a separate trusted location."""
        return self._head

    @property
    def results(self) -> tuple[FrozenJSON, ...]:
        latest = {r["job_key"]: r for r in self._head.to_dict()["records"]}
        return tuple(
            FrozenJSON.freeze(latest[j.key]) for j in self.experiment.jobs if j.key in latest
        )

    def run(
        self, runner: SimulationRunner, cancellation: Cancellation | None = None
    ) -> tuple[FrozenJSON, ...]:
        require(not self._running, "scheduler_already_running")
        require(
            cancellation is None or type(cancellation) is Cancellation,
            "cancellation_token_required",
        )
        require(callable(getattr(runner, "run", None)), "simulation_runner_required")
        if runner.reference != self.experiment.runner:
            raise SimulationFault(FailureCode.INCOMPATIBLE_INPUT, "runner_reference_mismatch")
        self._running = True
        try:
            return self._run(runner, cancellation if cancellation is not None else Cancellation())
        finally:
            self._running = False

    def _run(self, runner: SimulationRunner, cancellation: Cancellation) -> tuple[FrozenJSON, ...]:
        records = self._head.to_dict()["records"]
        latest = {r["job_key"]: r for r in records}
        output_bytes = sum(
            len(canonical(r["output"]).encode()) for r in records if r["output"] is not None
        )
        for job in self.experiment.jobs:
            prior = latest.get(job.key)
            if prior is not None and prior["status"] in {"succeeded", "failed"}:
                continue
            budget: RunBudget | None = None
            output: FrozenJSON | None = None
            failure: SimulationFault | None = None
            try:
                budget = RunBudget(self.experiment, cancellation, self._clock)
                budget.check()
                output = runner.run(self.experiment, job, self._seeds[job.key], budget)
                budget.check()
                try:
                    bounded_json(output, self.experiment.limits.max_result_bytes)
                except (ValueError, TypeError, AttributeError) as error:
                    raise SimulationFault(
                        FailureCode.RESULT_INTEGRITY, "invalid_or_oversized_adapter_result"
                    ) from error
                if (
                    output_bytes + len(output.payload_json.encode())
                    > self.experiment.limits.max_total_result_bytes
                ):
                    raise SimulationFault(FailureCode.BUDGET_EXHAUSTED, "total_result_bytes")
            except SimulationFault as error:
                failure = error
            except KeyboardInterrupt:
                failure = SimulationFault(FailureCode.INTERRUPTED, "caller_interrupt")
            except Exception as error:
                # No exception message/traceback in deterministic archives (may leak secrets).
                failure = SimulationFault(
                    FailureCode.EXECUTION_EXCEPTION, type(error).__name__[:256]
                )
            status = "succeeded"
            if failure is not None:
                status = {
                    FailureCode.CANCELLED: "cancelled",
                    FailureCode.INTERRUPTED: "interrupted",
                }.get(failure.code, "failed")
                output = None
            attempt = 1 if prior is None else prior["attempt"] + 1
            stop = status in {"cancelled", "interrupted"}
            if stop and attempt == self.experiment.limits.max_attempts_per_run:
                failure = SimulationFault(
                    FailureCode.BUDGET_EXHAUSTED, f"attempt_limit_after_{status}"
                )
                status = "failed"
            record: dict[str, Any] = {
                "version": self.result_version,
                "experiment_fingerprint": self._fingerprint,
                "run_id": self._run_ids[job.key],
                "job_key": job.key,
                "attempt": attempt,
                "seed": self._seeds[job.key],
                "status": status,
                "steps": budget.steps if budget is not None else 0,
                "output": output.to_dict() if output is not None else None,
                "failure": (
                    {"code": failure.code.value, "detail": failure.detail} if failure else None
                ),
            }
            record["result_hash"] = digest(record)
            candidate = self._document([*records, record])
            self._validate(candidate)
            self._head = candidate  # Single immutable logical transition, after full validation.
            records.append(record)
            if output is not None:
                output_bytes += len(output.payload_json.encode())
            if stop:
                break
        return self.results


def fields_object(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "result_object_required")
    return dict(value)
