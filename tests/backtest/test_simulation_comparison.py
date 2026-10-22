"""Fair synthetic variant comparisons using real pinned valuation and checkpoint owners."""

from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import (
    BatchScheduler,
    FailureCode,
    RunBudget,
    SimulationFault,
)
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.paper_valuation import (
    FLOW_SCOPE,
    POLICY,
    ValuationEvidence,
    value_paper_simulation,
)
from donghak_stock_vision.backtest.simulation_comparison import (
    PerformanceInput,
    ValuationSource,
    compare_performance,
    verify_performance_comparison,
)
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, SimulationJob
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Instrument, Money
from donghak_stock_vision.trading.paper_types import PaperFrame
from tests.backtest.test_paper_simulation import experiment
from tests.backtest.test_paper_valuation import book
from tests.trading.test_risk import KR, US


def registry(instrument: Instrument = KR) -> PaperSimulationAdapter:
    return PaperSimulationAdapter(
        tuple(
            replace(book((("BUY", quantity, price),), instrument), scenario_id=f"{variant}-{case}")
            for variant, quantity in (("a", 1), ("b", 3))
            for case, price in (("up", "10"), ("down", "8"))
        )
    )


def policy(**changes: Any) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "simulation_performance_comparison_policy_v1",
            "cohort_id": "controlled-native-market-paths",
            "valuation_policy_version": POLICY,
            "max_age_seconds": 60,
            "cash_flow_scope": FLOW_SCOPE,
            **changes,
        }
    )


def campaign(
    adapter: PaperSimulationAdapter,
    variant: str,
    scenarios: tuple[str, ...] | None = None,
    *,
    states: dict[int, str] | None = None,
    omit: tuple[int, ...] = (),
    unavailable: tuple[int, ...] = (),
    keys: tuple[str, ...] | None = None,
    terminal_price: str = "12",
    **spec_changes: Any,
) -> PerformanceInput:
    selected = scenarios if scenarios is not None else (f"{variant}-up",)
    spec = experiment(adapter, len(selected), selected[0])
    spec = replace(
        spec,
        experiment_id=f"experiment-{variant}",
        strategy=FrozenJSON.freeze(
            {
                "artifact_id": "controlled-strategy",
                "version": variant,
                "content_hash": digest({"variant": variant}),
            }
        ),
        jobs=tuple(
            adapter.job(keys[i] if keys else f"case-{i}", sid) for i, sid in enumerate(selected)
        ),
        **spec_changes,
    )
    selected_states = states or {}

    class Runner:
        reference = adapter.reference

        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, budget: RunBudget
        ) -> FrozenJSON:
            state = selected_states.get(exp.jobs.index(job), "succeeded")
            if state != "succeeded":
                raise SimulationFault(
                    {
                        "failed": FailureCode.EXECUTION_EXCEPTION,
                        "cancelled": FailureCode.CANCELLED,
                        "interrupted": FailureCode.INTERRUPTED,
                    }[state],
                    "controlled_fixture_outcome",
                )
            return adapter.run(exp, job, seed, budget)

    scheduler = BatchScheduler(spec)
    scheduler.run(Runner())
    checkpoint = scheduler.checkpoint()
    values = []
    for i, record in enumerate(scheduler.results):
        r = record.to_dict()
        if r["status"] != "succeeded" or i in omit:
            continue
        result = FrozenJSON.freeze(r["output"])
        job = next(j for j in spec.jobs if j.key == r["job_key"])
        source = next(
            s for s in adapter.scenarios if s.scenario_id == job.parameters.to_dict()["scenario_id"]
        )
        final = source.initial_frame
        for command in source.commands:
            if isinstance(command.payload, PaperFrame):
                final = command.payload
        evidence = ValuationEvidence(
            "fixture-pinned-marks",
            spec.fingerprint,
            spec.run_id(job),
            result.to_dict()["execution_fingerprint"],
            source.initial_frame.now,
            final.now,
            source.initial_frame.quotes,
            ()
            if i in unavailable
            else tuple(
                replace(q, price=Money(Decimal(terminal_price), source.currency))
                for q in final.quotes
            ),
            60,
            FLOW_SCOPE,
        )
        report = value_paper_simulation(
            spec,
            job,
            result,
            result.to_dict()["result_fingerprint"],
            adapter,
            evidence,
            evidence.document.identifier,
        )
        values.append(
            ValuationSource(
                job.key,
                report,
                report.to_dict()["content_fingerprint"],
                result,
                result.to_dict()["result_fingerprint"],
                evidence,
                evidence.document.identifier,
            )
        )
    return PerformanceInput(spec, checkpoint, checkpoint.identifier, adapter, tuple(values))


def compare(*sources: PerformanceInput, rules: FrozenJSON | None = None) -> FrozenJSON:
    p = policy() if rules is None else rules
    return compare_performance(sources, p, p.identifier, max_jobs=30)


def rational(value: dict[str, str]) -> Fraction:
    return Fraction(int(value["numerator"]), int(value["denominator"]))


@pytest.mark.parametrize("instrument", [KR, US])
def test_real_strategy_scripts_same_market_cohort_have_exact_paired_difference(
    instrument: Instrument,
) -> None:
    adapter = registry(instrument)
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    report = compare(a, b).to_dict()
    assert report["cohort"]["currency"] == instrument.currency.value
    left, right = report["experiments"]
    assert rational(left["return_statistics"]["mean"]) == Fraction(1, 450)
    assert rational(right["return_statistics"]["mean"]) == Fraction(1, 150)
    pair = report["paired_comparisons"][0]
    assert rational(pair["statistics"]["mean"]) == Fraction(1, 225)
    assert pair["statistics"]["evaluated_count"] == 1
    assert pair["scenarios"][0]["status"] == "evaluated"
    assert left["runs"][0]["scenario"] != right["runs"][0]["scenario"]
    assert left["runs"][0]["evaluation_case_hash"] == right["runs"][0]["evaluation_case_hash"]
    assert "winner" not in report


def test_statistics_mean_median_variance_wins_losses_and_flat() -> None:
    adapter = registry()
    # Two different exogenous market paths; each variant uses the exact same mix.
    a = campaign(adapter, "a", ("a-up", "a-down"), terminal_price="9")
    b = campaign(adapter, "b", ("b-up", "b-down"), terminal_price="9")
    v = compare(a, b).to_dict()
    stats = v["experiments"][0]["return_statistics"]
    assert (stats["wins"], stats["losses"], stats["flat"]) == (1, 1, 0)
    assert rational(stats["mean"]) == rational(stats["median"]) == 0
    assert rational(stats["population_variance"]) == Fraction(1, 810000)
    assert rational(stats["range"]) == Fraction(1, 450)
    assert rational(v["paired_comparisons"][0]["statistics"]["population_variance"]) == Fraction(
        1, 202500
    )
    flat_a = campaign(adapter, "a", terminal_price="10")
    flat_b = campaign(adapter, "b", terminal_price="10")
    flat = compare(flat_a, flat_b).to_dict()["experiments"][0]["return_statistics"]
    assert (flat["wins"], flat["losses"], flat["flat"]) == (0, 0, 1)


def test_odd_median_and_permutation_preserve_input_order() -> None:
    adapter = registry()
    a = campaign(adapter, "a", ("a-up", "a-up", "a-down"), terminal_price="9")
    b = campaign(adapter, "b", ("b-up", "b-up", "b-down"), terminal_price="9")
    v = compare(b, a).to_dict()
    stats = v["experiments"][1]["return_statistics"]
    assert rational(stats["median"]) == Fraction(-1, 900)
    assert rational(stats["mean"]) == Fraction(-1, 2700)
    assert [r["experiment_id"] for r in v["experiments"]] == [
        b.experiment.experiment_id,
        a.experiment.experiment_id,
    ]
    assert rational(v["paired_comparisons"][0]["statistics"]["mean"]) == Fraction(1, 1350)


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_non_success_is_not_zero_and_coverage_uses_entire_cohort(state: str) -> None:
    adapter = registry()
    a = campaign(adapter, "a", ("a-up",) * 3)
    b = campaign(adapter, "b", ("b-up",) * 3, states={1: state})
    v = compare(a, b).to_dict()
    result = v["experiments"][1]
    assert result["outcomes"][state] == 1
    assert result["return_statistics"]["expected_count"] == 3
    count = 2 if state == "failed" else 1
    assert result["return_statistics"]["evaluated_count"] == count
    assert rational(result["return_statistics"]["coverage"]) == Fraction(count, 3)
    assert result["runs"][1]["failure"]["code"]
    assert result["runs"][1]["attempt_history"]
    assert v["paired_comparisons"][0]["unavailable_matched_count"] == 3 - count
    if state != "failed":
        assert result["outcomes"]["unexamined"] == 1


@pytest.mark.parametrize("missing", ["omitted", "unavailable"])
def test_missing_values_remain_explicit_and_do_not_bias_denominator(missing: str) -> None:
    adapter = registry()
    a = campaign(adapter, "a", ("a-up",) * 2)
    b = campaign(
        adapter,
        "b",
        ("b-up",) * 2,
        omit=(1,) if missing == "omitted" else (),
        unavailable=(1,) if missing == "unavailable" else (),
    )
    v = compare(a, b).to_dict()
    stats = v["experiments"][1]["return_statistics"]
    assert stats["evaluated_count"] == 1 and stats["expected_count"] == 2
    assert rational(stats["coverage"]) == Fraction(1, 2)
    assert v["experiments"][1]["unavailable_succeeded_count"] == 1
    assert v["experiments"][1]["runs"][1]["unavailable_reason"] == (
        "valuation_not_supplied" if missing == "omitted" else "opening_or_closing_marks_unavailable"
    )
    assert v["paired_comparisons"][0]["statistics"]["evaluated_count"] == 1


def test_all_failed_empty_statistics_are_unavailable() -> None:
    adapter = registry()
    a = campaign(adapter, "a", states={0: "failed"})
    b = campaign(adapter, "b", states={0: "failed"})
    v = compare(a, b).to_dict()
    for item in v["experiments"]:
        stats = item["return_statistics"]
        assert stats["mean"] is stats["median"] is stats["population_variance"] is None
        assert stats["unavailable_reason"] == "no_evaluated_returns"
        assert rational(stats["coverage"]) == 0


def test_unmatched_keys_and_seeds_remain_in_union_without_false_pairing() -> None:
    adapter = registry()
    a = campaign(adapter, "a", keys=("left-only",))
    b = campaign(adapter, "b", keys=("right-only",))
    v = compare(a, b).to_dict()
    pair = v["paired_comparisons"][0]
    assert pair["unmatched_count"] == 2
    assert pair["statistics"]["expected_count"] == 2
    assert pair["statistics"]["evaluated_count"] == 0
    assert all(r["status"] == "unmatched" for r in pair["scenarios"])
    assert all(item["return_statistics"]["evaluated_count"] == 1 for item in v["experiments"])


@pytest.mark.parametrize(
    "change",
    ["market", "mix", "count", "capital", "cost", "period", "seed", "limits", "marks", "dataset"],
)
def test_incompatible_cohorts_are_rejected(change: str) -> None:
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    if change == "market":
        b = campaign(registry(US), "b")
    elif change == "mix":
        b = campaign(adapter, "b", ("b-down",))
    elif change == "count":
        b = campaign(adapter, "b", ("b-up",) * 2)
    elif change in {"capital", "cost", "dataset"}:
        scenarios = list(adapter.scenarios)
        source = scenarios[0]
        if change == "capital":
            account = source.initial_account
            balances = tuple(
                replace(x, available=Money(Decimal(800), KR.currency)) for x in account.balances
            )
            source = replace(source, initial_account=replace(account, balances=balances))
        elif change == "cost":
            source = replace(source, policy=replace(source.policy, buy_fee_rate=Decimal("0.001")))
        else:
            source = replace(source, scenario_id="extra-scenario")
        changed = PaperSimulationAdapter((source, *scenarios[1:]))
        b = campaign(changed, "b")
    elif change == "period":
        b = campaign(adapter, "b", parameters=FrozenJSON.freeze({"period": "different"}))
    elif change == "seed":
        b = campaign(adapter, "b", seed=43)
    elif change == "limits":
        b = campaign(adapter, "b", limits=replace(b.experiment.limits, max_run_seconds=59))
    elif change == "marks":
        b = campaign(adapter, "b", terminal_price="13")
    with pytest.raises(ValueError, match="incompatible"):
        compare(a, b)


@pytest.mark.parametrize(
    "change", ["policy_version", "max_age", "cash_flow", "policy_pin", "cohort_version"]
)
def test_valuation_policy_and_approval_pins_cannot_be_bypassed(change: str) -> None:
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    p = policy()
    if change == "policy_version":
        p = policy(valuation_policy_version="new_unknown")
    elif change == "max_age":
        p = policy(max_age_seconds=59)
    elif change == "cash_flow":
        p = policy(cash_flow_scope=None)
    elif change == "cohort_version":
        p = policy(version="unknown")
    with pytest.raises(ValueError):
        compare_performance(
            (a, b), p, "0" * 64 if change == "policy_pin" else p.identifier, max_jobs=2
        )


@pytest.mark.parametrize(
    "change",
    [
        "report",
        "result",
        "price_pin",
        "valuation_pin",
        "checkpoint",
        "cross_run",
        "duplicate",
        "unexpected",
    ],
)
def test_forged_or_misbound_source_evidence_fails_closed(change: str) -> None:
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    source = b.valuations[0]
    if change == "report":
        v = source.report.to_dict()
        v["performance"]["total_return"]["exact_ratio"]["numerator"] = "999"
        v["content_fingerprint"] = digest(
            {k: x for k, x in v.items() if k != "content_fingerprint"}
        )
        source = replace(
            source, report=FrozenJSON.freeze(v), trusted_report_hash=v["content_fingerprint"]
        )
    elif change == "result":
        v = source.result.to_dict()
        v["execution"]["costs"]["fees"]["amount"] = "999"
        source = replace(source, result=FrozenJSON.freeze(v))
    elif change == "price_pin":
        source = replace(source, trusted_evidence_hash="0" * 64)
    elif change == "valuation_pin":
        source = replace(source, trusted_report_hash="0" * 64)
    elif change == "checkpoint":
        b = replace(b, trusted_checkpoint_hash="0" * 64)
    elif change == "cross_run":
        source = replace(a.valuations[0], job_key=source.job_key)
    elif change == "unexpected":
        source = replace(source, job_key="not-a-job")
    b = replace(b, valuations=(source, source) if change == "duplicate" else (source,))
    with pytest.raises(ValueError):
        compare(a, b)


@pytest.mark.parametrize("change", ["statistics", "pair", "coverage", "version", "digest"])
def test_tampered_comparison_even_with_fresh_self_hash_requires_full_replay(change: str) -> None:
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    p = policy()
    report = compare(a, b)
    v = report.to_dict()
    if change == "statistics":
        v["experiments"][0]["return_statistics"]["mean"]["numerator"] = "999"
    elif change == "pair":
        v["paired_comparisons"][0]["scenarios"][0]["right_minus_left"]["numerator"] = "999"
    elif change == "coverage":
        v["experiments"][0]["return_statistics"]["coverage"]["numerator"] = "999"
    elif change == "version":
        v["version"] = "unknown"
    else:
        v["content_fingerprint"] = "0" * 64
    if change != "digest":
        v["content_fingerprint"] = digest(
            {k: x for k, x in v.items() if k != "content_fingerprint"}
        )
    with pytest.raises(ValueError):
        verify_performance_comparison(
            FrozenJSON.freeze(v), v["content_fingerprint"], (a, b), p, p.identifier, max_jobs=2
        )


def test_deterministic_replay_read_only_views_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("network is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    before = (
        a.checkpoint.payload_json,
        b.valuations[0].report.payload_json,
        adapter.dataset_reference,
    )
    report = compare(a, b)
    assert report == compare(a, b)
    with localcontext() as context:
        context.prec = 2
        assert report == compare(a, b)
    p = policy()
    verify_performance_comparison(
        report, report.to_dict()["content_fingerprint"], (a, b), p, p.identifier, max_jobs=2
    )
    assert (
        a.checkpoint.payload_json,
        b.valuations[0].report.payload_json,
        adapter.dataset_reference,
    ) == before
    detached = report.to_dict()
    detached["experiments"].clear()
    assert len(report.to_dict()["experiments"]) == 2


def test_input_counts_duplicates_and_job_limits() -> None:
    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    p = policy()
    for sources in ((a,), (a, a), (a, b, a, b, a, b)):
        with pytest.raises(ValueError):
            compare_performance(sources, p, p.identifier, max_jobs=30)
    with pytest.raises(ValueError, match="job_budget"):
        compare_performance((a, b), p, p.identifier, max_jobs=1)


def test_missing_valuation_never_skips_original_execution_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = registry()
    a, b = campaign(adapter, "a", omit=(0,)), campaign(adapter, "b")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise ValueError("semantic_replay_mandatory")

    monkeypatch.setattr(PaperSimulationAdapter, "replay_validate", forbidden)
    with pytest.raises(ValueError, match="semantic_replay_mandatory"):
        compare(a, b)


@pytest.mark.parametrize("kind", ["capital", "cost"])
def test_same_registry_does_not_hide_different_starting_accounts_or_costs(kind: str) -> None:
    a = replace(book((("BUY", 1, "10"),)), scenario_id="a-up")
    b = replace(book((("BUY", 3, "10"),)), scenario_id="b-up")
    if kind == "capital":
        account = b.initial_account
        b = replace(
            b,
            initial_account=replace(
                account,
                balances=tuple(
                    replace(balance, available=Money(Decimal(800), KR.currency))
                    for balance in account.balances
                ),
            ),
        )
    else:
        b = replace(b, policy=replace(b.policy, buy_fee_rate=Decimal("0.001")))
    adapter = PaperSimulationAdapter((a, b))
    with pytest.raises(ValueError, match="incompatible_evaluation_cohort"):
        compare(campaign(adapter, "a"), campaign(adapter, "b"))


def test_five_variants_have_all_request_order_pairs_without_ranking() -> None:
    adapter = registry()
    sources = tuple(campaign(adapter, v, ("a-up",)) for v in ("e", "b", "d", "a", "c"))
    v = compare(*sources).to_dict()
    assert [x["strategy"]["version"] for x in v["experiments"]] == ["e", "b", "d", "a", "c"]
    assert len(v["paired_comparisons"]) == 10
    assert all(rational(p["statistics"]["mean"]) == 0 for p in v["paired_comparisons"])


@pytest.mark.parametrize("kind", ["stale", "future"])
def test_unavailable_mark_diagnostics_are_retained_without_zero_imputation(kind: str) -> None:
    from datetime import timedelta

    adapter = registry()
    a, b = campaign(adapter, "a"), campaign(adapter, "b")
    source = b.valuations[0]
    q = source.evidence.closing_marks[0]
    delta = timedelta(seconds=-61 if kind == "stale" else 1)
    evidence = replace(
        source.evidence,
        closing_marks=(
            replace(q, observed_at=q.observed_at + delta, available_at=q.available_at + delta),
        ),
    )
    report = value_paper_simulation(
        b.experiment,
        b.experiment.jobs[0],
        source.result,
        source.trusted_result_fingerprint,
        adapter,
        evidence,
        evidence.document.identifier,
    )
    source = replace(
        source,
        report=report,
        trusted_report_hash=report.to_dict()["content_fingerprint"],
        evidence=evidence,
        trusted_evidence_hash=evidence.document.identifier,
    )
    v = compare(a, replace(b, valuations=(source,))).to_dict()
    assert v["experiments"][1]["return_statistics"]["mean"] is None
    assert v["experiments"][1]["unavailable_succeeded_count"] == 1
    assert v["experiments"][1]["runs"][0]["valuation_diagnostics"][0]["reason"] == f"{kind}_mark"
    assert v["paired_comparisons"][0]["unavailable_matched_count"] == 1


def test_failed_job_registry_owner_cannot_hide_tampered_scenario_behind_cached_pin() -> None:
    adapter = registry()
    a, b = (
        campaign(adapter, "a", states={0: "failed"}),
        campaign(adapter, "b", states={0: "failed"}),
    )
    bad = PaperSimulationAdapter(adapter.scenarios)
    first = bad.scenarios[0]
    changed = replace(first, initial_frame=replace(first.initial_frame, trading_enabled=False))
    object.__setattr__(bad, "scenarios", (changed, *bad.scenarios[1:]))
    with pytest.raises(ValueError, match="registry_pin_mismatch"):
        compare(replace(a, adapter=bad), b)


def test_rehashed_corrupt_success_with_missing_valuation_still_requires_economic_replay() -> None:
    adapter = registry()
    a, b = campaign(adapter, "a", omit=(0,)), campaign(adapter, "b")
    cp = a.checkpoint.to_dict()
    record = cp["records"][0]
    output = record["output"]
    output["execution"]["costs"]["fees"]["amount"] = "999"
    output["execution_fingerprint"] = digest(output["execution"])
    output["result_fingerprint"] = digest(
        {k: v for k, v in output.items() if k != "result_fingerprint"}
    )
    record["result_hash"] = digest({k: v for k, v in record.items() if k != "result_hash"})
    changed = FrozenJSON.freeze(cp)
    a = replace(a, checkpoint=changed, trusted_checkpoint_hash=changed.identifier)
    with pytest.raises(ValueError, match="replay_mismatch"):
        compare(a, b)
