"""Small synthetic orchestration only; no training, providers or financial claims."""

from dataclasses import replace
from random import Random
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import (
    BatchScheduler,
    Cancellation,
    FailureCode,
    RunBudget,
    SimulationFault,
)
from donghak_stock_vision.backtest.simulation_spec import (
    ExperimentSpec,
    SimulationJob,
    SimulationLimits,
)
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Currency, Market


def ref(name: str) -> FrozenJSON:
    return FrozenJSON.freeze(
        {"artifact_id": name, "version": "synthetic_v1", "content_hash": digest({"name": name})}
    )


def experiment(count: int = 10) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="synthetic-orchestration",
        strategy=ref("fixture-strategy"),
        dataset=ref("fixture-data"),
        code=ref("fixture-code"),
        runner=ref("fixture-runner"),
        parameters=FrozenJSON.freeze({"purpose": "mechanics_only"}),
        execution_policy=FrozenJSON.freeze({"execution": "none"}),
        seed=42,
        market=Market.KR,
        currency=Currency.KRW,
        data_origin="synthetic",
        limits=SimulationLimits(
            max_runs=100,
            max_attempts_per_run=3,
            concurrency=1,
            max_steps_per_run=10,
            max_run_seconds=10,
            max_result_bytes=1024,
            max_total_result_bytes=100 * 1024,
            max_spec_bytes=100 * 1024,
        ),
        jobs=tuple(
            SimulationJob(f"job-{i:03}", FrozenJSON.freeze({"index": i})) for i in range(count)
        ),
    )


class SyntheticRunner:
    reference = ref("fixture-runner")

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(
        self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
    ) -> FrozenJSON:
        self.calls.append(job.key)
        budget.consume(2)
        return FrozenJSON.freeze(
            {
                "synthetic": True,
                "draw": Random(seed).randrange(1000),
                "index": job.parameters.to_dict()["index"],
                "strategy_loss": "-1.25",
                "rejected_actions": 1,
            }
        )


def failure_code(result: FrozenJSON) -> str:
    return str(result.to_dict()["failure"]["code"])


def repin(doc: dict[str, Any]) -> FrozenJSON:
    for record in doc["records"]:
        record["result_hash"] = digest({k: v for k, v in record.items() if k != "result_hash"})
    return FrozenJSON.freeze(doc)


@pytest.mark.parametrize("count", [10, 100])
def test_small_batches_are_deterministic_bounded_and_ordered(count: int) -> None:
    spec = experiment(count)
    a, b = BatchScheduler(spec), BatchScheduler(spec)
    adapter_a, adapter_b = SyntheticRunner(), SyntheticRunner()
    results = a.run(adapter_a)
    assert b.run(adapter_b) == results
    assert a.checkpoint() == b.checkpoint()
    assert len(results) == count
    assert [r.to_dict()["job_key"] for r in results] == [j.key for j in spec.jobs]
    assert adapter_a.calls == adapter_b.calls == [j.key for j in spec.jobs]
    assert all(r.to_dict()["status"] == "succeeded" for r in results)
    assert len({r.to_dict()["run_id"] for r in results}) == count
    assert all(r.to_dict()["output"]["strategy_loss"] == "-1.25" for r in results)
    assert spec.to_dict()["usage_restriction"] == "synthetic_test_only"
    # No adapter call is repeated, even when the finished scheduler is run again.
    assert a.run(adapter_a) == results
    assert len(adapter_a.calls) == count


@pytest.mark.parametrize("market,currency", [(Market.KR, Currency.KRW), (Market.US, Currency.USD)])
def test_native_market_currency_contract(market: Market, currency: Currency) -> None:
    spec = replace(experiment(), market=market, currency=currency)
    assert len(BatchScheduler(spec).run(SyntheticRunner())) == 10
    assert spec.to_dict()["currency"] == currency.value


@pytest.mark.parametrize(
    "change",
    [
        {"experiment_id": "other"},
        {"seed": 43},
        {"strategy": ref("strategy2")},
        {"dataset": ref("data2")},
        {"code": ref("code2")},
        {"runner": ref("runner2")},
        {"parameters": FrozenJSON.freeze({"other": 1})},
        {"execution_policy": FrozenJSON.freeze({"cost": "different"})},
        {"data_origin": "real"},
        {"market": Market.US, "currency": Currency.USD},
    ],
)
def test_immutable_input_changes_create_distinct_namespaces(change: dict[str, Any]) -> None:
    spec = experiment()
    other = replace(spec, **change)
    assert other.fingerprint != spec.fingerprint
    assert other.run_id(other.jobs[0]) != spec.run_id(spec.jobs[0])


def test_canonical_parameters_and_real_research_labels() -> None:
    a = replace(experiment(), parameters=FrozenJSON('{"z":1,"a":2}'), data_origin="real")
    b = replace(a, parameters=FrozenJSON('{"a":2,"z":1}'))
    assert a.fingerprint == b.fingerprint
    assert a.to_dict()["usage_restriction"] == "research_only"
    assert a.run_seed(a.jobs[0]) == b.run_seed(b.jobs[0])


@pytest.mark.parametrize(
    "change",
    [
        {"max_runs": 0},
        {"max_runs": 10_001},
        {"max_attempts_per_run": 0},
        {"max_attempts_per_run": 11},
        {"concurrency": 2},
        {"concurrency": 0},
        {"max_steps_per_run": True},
        {"max_run_seconds": 0},
        {"max_run_seconds": 86_401},
        {"max_result_bytes": 0},
        {"max_total_result_bytes": 17 * 1024 * 1024},
        {"max_spec_bytes": 5 * 1024 * 1024},
    ],
)
def test_explicit_resource_configuration_fails_closed(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(experiment().limits, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"experiment_id": ""},
        {"seed": -1},
        {"seed": True},
        {"market": "KR"},
        {"currency": Currency.USD},
        {"data_origin": "approved_live"},
        {"dataset": FrozenJSON.freeze({"artifact_id": "no_hash"})},
        {"code": FrozenJSON.freeze({"artifact_id": "x", "version": "1", "content_hash": "BAD"})},
        {"jobs": ()},
        {"jobs": experiment().jobs * 2},
        {"jobs": list(experiment().jobs)},
        {"limits": replace(experiment().limits, max_runs=1)},
        {"limits": replace(experiment().limits, max_spec_bytes=100)},
    ],
)
def test_invalid_experiments_are_rejected_before_execution(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(experiment(), **change)


def test_parameters_and_unknown_jobs_are_bounded() -> None:
    with pytest.raises(ValueError, match="json_budget"):
        SimulationJob("large", FrozenJSON.freeze({"data": "x" * (16 * 1024)}))
    with pytest.raises(ValueError, match="job_not_in_experiment"):
        experiment().run_id(SimulationJob("alien", FrozenJSON.freeze({})))


@pytest.mark.parametrize(
    "code", [FailureCode.MISSING_INPUT, FailureCode.INCOMPATIBLE_INPUT, FailureCode.RISK_DENIED]
)
def test_adapter_failures_are_isolated_with_structured_diagnostics(code: FailureCode) -> None:
    class Failing(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            if job.key == "job-002":
                raise SimulationFault(code, "synthetic_evidence_unavailable")
            return super().run(experiment, job, seed, budget)

    scheduler = BatchScheduler(experiment())
    results = scheduler.run(Failing())
    assert len(results) == 10
    assert failure_code(results[2]) == code.value
    assert sum(r.to_dict()["status"] == "succeeded" for r in results) == 9
    restored = BatchScheduler(
        experiment(),
        checkpoint=scheduler.checkpoint(),
        expected_checkpoint_hash=scheduler.checkpoint().identifier,
    )
    adapter = SyntheticRunner()
    assert restored.run(adapter) == results
    assert adapter.calls == []


def test_unexpected_exception_does_not_expose_secret_or_poison_batch() -> None:
    class Broken(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            if job.key == "job-001":
                raise RuntimeError("SECRET should not be archived")
            return super().run(experiment, job, seed, budget)

    scheduler = BatchScheduler(experiment())
    results = scheduler.run(Broken())
    assert failure_code(results[1]) == "execution_exception"
    assert results[1].to_dict()["failure"]["detail"] == "RuntimeError"
    assert "SECRET" not in scheduler.checkpoint().payload_json
    assert results[-1].to_dict()["status"] == "succeeded"


@pytest.mark.parametrize("interrupt", [False, True])
def test_cancellation_and_interrupt_preserve_history_and_resume_without_duplicates(
    interrupt: bool,
) -> None:
    token = Cancellation()

    class Stopped(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            if job.key == "job-003":
                if interrupt:
                    raise KeyboardInterrupt
                token.cancel()
                budget.check()
            return super().run(experiment, job, seed, budget)

    scheduler = BatchScheduler(experiment())
    results = scheduler.run(Stopped(), token)
    assert len(results) == 4
    assert results[-1].to_dict()["status"] == ("interrupted" if interrupt else "cancelled")
    original = scheduler.checkpoint()
    restored = BatchScheduler(
        experiment(),
        checkpoint=original,
        expected_checkpoint_hash=original.identifier,
    )
    adapter = SyntheticRunner()
    final = restored.run(adapter)
    assert adapter.calls == [j.key for j in experiment().jobs[3:]]
    assert len(final) == 10
    assert all(r.to_dict()["status"] == "succeeded" for r in final)
    assert final[:3] == results[:3]
    assert restored.checkpoint().to_dict()["records"][:4] == original.to_dict()["records"]
    assert len(restored.checkpoint().to_dict()["records"]) == 11
    assert final[3].to_dict()["attempt"] == 2


def test_cancel_before_start_and_retry_budget_is_finite() -> None:
    token = Cancellation()
    token.cancel()
    adapter = SyntheticRunner()
    scheduler = BatchScheduler(experiment())
    for _ in range(3):
        scheduler.run(adapter, token)
    assert adapter.calls == []
    assert len(scheduler.checkpoint().to_dict()["records"]) == 3
    assert failure_code(scheduler.results[0]) == "budget_exhausted"
    assert scheduler.results[0].to_dict()["failure"]["detail"] == "attempt_limit_after_cancelled"
    final = scheduler.run(adapter)
    assert len(final) == 10
    assert adapter.calls == [j.key for j in experiment().jobs[1:]]


@pytest.mark.parametrize("which", ["steps", "timeout", "backwards", "nan"])
def test_cooperative_budgets_and_invalid_clock(which: str) -> None:
    class Bounded(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            budget.consume(10 if which == "steps" else 1)
            if which == "steps":
                budget.consume()
            return FrozenJSON.freeze({"ok": True})

    spec = experiment(1)
    ticks = iter(
        {
            "timeout": [0.0, 0.0, 0.0, 10.0],
            "backwards": [1.0, 0.0],
            "nan": [float("nan")],
            "steps": [0.0] * 5,
        }[which]
    )

    def clock() -> float:
        return next(ticks)

    result = BatchScheduler(spec, clock=clock).run(Bounded())[0]
    assert (
        failure_code(result)
        == {
            "steps": "budget_exhausted",
            "timeout": "timeout",
            "backwards": "invalid_configuration",
            "nan": "invalid_configuration",
        }[which]
    )


def test_exact_step_boundary_is_allowed() -> None:
    class Exact(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            budget.consume(experiment.limits.max_steps_per_run)
            return FrozenJSON.freeze({"ok": True})

    result = BatchScheduler(experiment(1), clock=lambda: 0.0).run(Exact())[0].to_dict()
    assert result["status"] == "succeeded" and result["steps"] == 10


@pytest.mark.parametrize("bad", [None, {"mutable": True}, FrozenJSON.freeze({"big": "x" * 1024})])
def test_invalid_or_oversized_result_fails_closed(bad: Any) -> None:
    class Invalid(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            return bad  # type: ignore[no-any-return]

    result = BatchScheduler(experiment(1)).run(Invalid())[0]
    assert failure_code(result) == "result_integrity_mismatch"
    assert result.to_dict()["output"] is None


def test_total_output_budget_does_not_overwrite_prior_results() -> None:
    spec = experiment()
    spec = replace(
        spec, limits=replace(spec.limits, max_result_bytes=120, max_total_result_bytes=120)
    )
    results = BatchScheduler(spec).run(SyntheticRunner())
    assert results[0].to_dict()["status"] == "succeeded"
    assert all(failure_code(r) == "budget_exhausted" for r in results[1:])


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "version",
        "owner",
        "run",
        "seed",
        "result",
        "order",
        "duplicate",
        "attempt",
        "status",
        "failure",
        "steps",
        "extra",
        "output",
        "missing",
        "unknown_job",
    ],
)
def test_checkpoint_tampering_rejected_even_with_rehashed_records(mutation: str) -> None:
    spec = experiment(2)
    scheduler = BatchScheduler(spec)
    scheduler.run(SyntheticRunner())
    doc = scheduler.checkpoint().to_dict()
    record = doc["records"][0]
    if mutation == "hash":
        record["result_hash"] = "0" * 64
    elif mutation == "version":
        doc["version"] = "mass_simulation_checkpoint_v999"
    elif mutation == "owner":
        doc["experiment_fingerprint"] = "0" * 64
    elif mutation == "run":
        record["run_id"] = "0" * 64
    elif mutation == "seed":
        record["seed"] += 1
    elif mutation == "result":
        record["version"] = "unknown"
    elif mutation == "order":
        doc["records"].reverse()
    elif mutation == "duplicate":
        doc["records"].insert(1, dict(record))
    elif mutation == "attempt":
        record["attempt"] = 2
    elif mutation == "status":
        record["status"] = "approved_live"
    elif mutation == "failure":
        record["failure"] = {"code": "risk_denied", "detail": "conflict"}
    elif mutation == "steps":
        record["steps"] = 11
    elif mutation == "extra":
        record["unexpected"] = True
    elif mutation == "output":
        record["output"] = {"huge": "x" * 1024}
    elif mutation == "missing":
        del record["output"]
    elif mutation == "unknown_job":
        record["job_key"] = "alien"
    forged = FrozenJSON.freeze(doc) if mutation == "hash" else repin(doc)
    with pytest.raises(SimulationFault):
        BatchScheduler(spec, checkpoint=forged, expected_checkpoint_hash=forged.identifier)


def test_trusted_pin_is_required_and_catches_semantically_plausible_payload_tampering() -> None:
    scheduler = BatchScheduler(experiment())
    scheduler.run(SyntheticRunner())
    original = scheduler.checkpoint()
    doc = original.to_dict()
    doc["records"][0]["output"]["draw"] += 1
    modified = repin(doc)
    with pytest.raises(SimulationFault, match="trusted_pin_required"):
        BatchScheduler(experiment(), checkpoint=modified)
    with pytest.raises(SimulationFault, match="checkpoint_corruption"):
        BatchScheduler(
            experiment(), checkpoint=modified, expected_checkpoint_hash=original.identifier
        )
    with pytest.raises(SimulationFault, match="checkpoint_mismatch"):
        BatchScheduler(
            replace(experiment(), seed=43),
            checkpoint=original,
            expected_checkpoint_hash=original.identifier,
        )


def test_new_documents_are_verified_independently_and_empty_checkpoint_can_resume() -> None:
    spec = experiment()
    scheduler = BatchScheduler(spec)
    checkpoint = FrozenJSON(scheduler.checkpoint().payload_json)
    restored = BatchScheduler(
        spec, checkpoint=checkpoint, expected_checkpoint_hash=checkpoint.identifier
    )
    assert len(restored.run(SyntheticRunner())) == 10
    assert checkpoint.to_dict()["records"] == []


def test_wrong_adapter_reference_is_rejected_without_call() -> None:
    adapter = SyntheticRunner()
    adapter.reference = ref("other")
    with pytest.raises(ValueError, match="runner_reference_mismatch"):
        BatchScheduler(experiment()).run(adapter)
    assert adapter.calls == []


def test_reentrant_scheduler_is_rejected_without_mutating_original_head() -> None:
    scheduler = BatchScheduler(experiment(1))
    original = scheduler.checkpoint()

    class Reentrant(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            assert scheduler.checkpoint() == original
            with pytest.raises(ValueError, match="scheduler_already_running"):
                scheduler.run(self)
            return super().run(experiment, job, seed, budget)

    assert scheduler.run(Reentrant())[0].to_dict()["status"] == "succeeded"


def test_checkpoint_transition_failure_preserves_previous_immutable_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = BatchScheduler(experiment(1))
    original = scheduler.checkpoint()

    def fail(checkpoint: FrozenJSON) -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(scheduler, "_validate", fail)
    with pytest.raises(RuntimeError, match="commit failure"):
        scheduler.run(SyntheticRunner())
    assert scheduler.checkpoint() is original


def test_orchestration_does_not_call_existing_execution_or_research_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donghak_stock_vision.trading.paper import DeterministicPaperBroker
    from donghak_stock_vision.trading.risk import evaluate_risk

    calls: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> None:
        calls.append("forbidden")
        raise AssertionError("foundation must not activate execution")

    monkeypatch.setattr(DeterministicPaperBroker, "submit", forbidden)
    monkeypatch.setattr("donghak_stock_vision.trading.risk.evaluate_risk", forbidden)
    assert callable(evaluate_risk)
    assert len(BatchScheduler(experiment()).run(SyntheticRunner())) == 10
    assert calls == []


def test_invalid_runtime_controls_rejected_without_starting_jobs() -> None:
    adapter = SyntheticRunner()
    scheduler = BatchScheduler(experiment())
    with pytest.raises(ValueError, match="cancellation_token_required"):
        scheduler.run(adapter, False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="monotonic_clock_required"):
        BatchScheduler(experiment(), clock=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="simulation_runner_required"):
        scheduler.run(object())  # type: ignore[arg-type]
    assert scheduler.checkpoint().to_dict()["records"] == []
    assert adapter.calls == []


def test_exact_canonical_utf8_output_byte_boundary() -> None:
    payload = FrozenJSON.freeze({"unicode": "한국"})

    class ExactBytes(SyntheticRunner):
        def run(
            self, experiment: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            budget.consume()
            return payload

    spec = experiment(1)
    size = len(payload.payload_json.encode())
    exact = replace(
        spec, limits=replace(spec.limits, max_result_bytes=size, max_total_result_bytes=size)
    )
    assert BatchScheduler(exact).run(ExactBytes())[0].to_dict()["status"] == "succeeded"
    smaller = replace(exact, limits=replace(exact.limits, max_result_bytes=size - 1))
    assert failure_code(BatchScheduler(smaller).run(ExactBytes())[0]) == "result_integrity_mismatch"
