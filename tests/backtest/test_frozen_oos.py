"""Independently retained synthetic custody fixtures, never historical OOS approval."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.frozen_oos_contracts import (
    FrozenOOSAuthorization,
    FrozenOOSRequest,
    FrozenStrategySpec,
    OOSCaseMembership,
    OOSMethodology,
    SyntheticFreezeCustody,
    lineage_reference,
)
from donghak_stock_vision.backtest.frozen_oos_execution import (
    evaluate_frozen_oos,
    verify_frozen_oos,
)
from donghak_stock_vision.backtest.frozen_oos_walk_forward import (
    PinnedFrozenFold,
    summarize_frozen_walk_forward,
    verify_frozen_walk_forward,
)
from donghak_stock_vision.backtest.paper_valuation import ValuationEvidence
from donghak_stock_vision.backtest.simulation_validation import (
    TimeWindow,
    WalkForwardPlan,
    walk_forward_folds,
)
from donghak_stock_vision.backtest.strategy_execution import (
    StrategyExecutionRequest,
    execute_strategy_candidates,
)
from donghak_stock_vision.backtest.strategy_execution_analysis import PinnedValuation
from donghak_stock_vision.backtest.strategy_experiment import DatasetPartition, candidates
from donghak_stock_vision.backtest.strategy_observations import (
    PartitionAuthorization,
    SyntheticObservations,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Exchange
from donghak_stock_vision.trading.paper_types import wire
from tests.backtest.test_simulation_comparison import policy
from tests.backtest.test_simulation_validation import shift
from tests.backtest.test_strategy_execution import fixture as parameter_fixture
from tests.backtest.test_strategy_experiment import ref
from tests.trading.test_risk import KR, US


def fixture(
    mode: str = "holdout",
    count: int = 1,
    exchange: Exchange = Exchange.KOSPI,
    *,
    entry: str = "10",
    missing_quote: bool = False,
    wrong_member: bool = False,
    bad_session: bool = False,
    expected_cases: int = 1,
    budget: int = 65,
    exit_price: str = "12",
    alternate_entry: str | None = None,
) -> tuple[WalkForwardPlan, tuple[FrozenOOSRequest, ...]]:
    zone = ZoneInfo("Asia/Seoul" if exchange.market.value == "KR" else "America/New_York")
    initial = datetime(2026, 1, 5, 9, 30, tzinfo=zone)
    declared = walk_forward_folds(
        mode,
        TimeWindow(initial, initial + timedelta(days=1)),
        count=count,
        step_seconds=86400,
        test_seconds=600,
        validation_seconds=86400,
        purge_seconds=60,
        embargo_seconds=60,
        expected_cases=expected_cases,
    )
    instrument = replace(KR if exchange.market.value == "KR" else US, exchange=exchange)
    base = parameter_fixture(instrument, entries=(entry,))
    base = replace(
        base, plan=replace(base.plan, limits=replace(base.plan.limits, max_steps_per_run=budget))
    )
    template = base.observations.cases[0]
    calendars = []
    for i in range(5 + count):
        day = date(2026, 1, 5) + timedelta(days=i)
        calendars.append(
            {
                "session_id": day.isoformat(),
                "date": day.isoformat(),
                "open_at": datetime.combine(
                    day, time(9) if instrument.market.value == "KR" else time(9, 30), zone
                ).isoformat(),
                "close_at": datetime.combine(
                    day, time(15, 30) if instrument.market.value == "KR" else time(16), zone
                ).isoformat(),
            }
        )
    calendar = FrozenJSON.freeze(
        {
            "version": "synthetic_session_calendar_v1",
            "calendar_id": "fixture-only",
            "exchange": exchange.value,
            "timezone": str(zone),
            "data_origin": "synthetic",
            "sessions": calendars,
        }
    )
    prepared = []
    for fold in declared:
        sources = []
        for window in (fold.training, fold.validation, fold.testing):
            assert window is not None
            at = window.start + timedelta(seconds=10)
            delta = at - template.steps[0].decision_frame.now
            case = shift(
                template, delta, (at.date() - template.steps[0].decision_frame.now.date()).days
            )
            sid = at.astimezone(zone).date().isoformat()
            steps = tuple(
                replace(
                    s,
                    decision_frame=replace(
                        s.decision_frame,
                        sessions=tuple(
                            replace(v, source_id=sid) for v in s.decision_frame.sessions
                        ),
                    ),
                    execution_frame=replace(
                        s.execution_frame,
                        sessions=tuple(
                            replace(v, source_id=sid) for v in s.execution_frame.sessions
                        ),
                    ),
                )
                for s in case.steps
            )
            sources.append(SyntheticObservations((replace(case, steps=steps),)))
        train, tuning, oos = sources
        if missing_quote:
            case = oos.cases[0]
            step = replace(
                case.steps[0], decision_frame=replace(case.steps[0].decision_frame, quotes=())
            )
            oos = SyntheticObservations((replace(case, steps=(step, *case.steps[1:])),))
        cohorts = tuple(ref(f"cohort-{r}") for r in ("training", "tuning", "oos", "holdout"))
        auth = FrozenOOSAuthorization(
            "sealed-oos",
            f"cycle-{fold.fold_id}",
            fold.fold_id,
            "oos",
            oos.document.identifier,
            cohorts[2].identifier,
            fold.testing,
            fold.training.start - timedelta(seconds=1),
            fold.testing.start,
            (
                OOSCaseMembership(
                    template.case_id,
                    "0" * 64 if wrong_member else digest(wire(oos.cases[0])),
                    "unknown-session"
                    if bad_session
                    else fold.testing.start.astimezone(zone).date().isoformat(),
                ),
            ),
            (),
        )
        train_auth = PartitionAuthorization(
            "training-custody",
            "training",
            train.document.identifier,
            cohorts[0].identifier,
            fold.training,
        )
        assert fold.validation is not None
        tune_auth = PartitionAuthorization(
            "tuning-custody",
            "tuning",
            tuning.document.identifier,
            cohorts[1].identifier,
            fold.validation,
        )
        prepared.append((fold, train, tuning, oos, cohorts, auth, train_auth, tune_auth))
    manifest = FrozenJSON.freeze(
        {
            "version": "frozen_oos_dataset_manifest_v1",
            "folds": [
                {
                    "fold_id": fold.fold_id,
                    "dataset": oos.reference.to_dict(),
                    "cohort": cohorts[2].to_dict(),
                    "authorization_hash": auth.document.identifier,
                }
                for fold, _, _, oos, cohorts, auth, _, _ in prepared
            ],
        }
    )
    wf = WalkForwardPlan(
        "controlled-frozen-walk-forward",
        mode,
        exchange,
        str(zone),
        ref("oos-dataset-group", manifest.identifier),
        calendar,
        declared,
        60,
        60,
        1,
        1,
        1,
        count,
    )
    requests = []
    for fold, train, tuning, oos, cohorts, auth, train_auth, tune_auth in prepared:
        assert fold.validation is not None
        partitions = (
            DatasetPartition(
                "training",
                cohorts[0],
                train.reference,
                fold.training,
                ref("training-custody", train_auth.document.identifier),
            ),
            DatasetPartition(
                "tuning",
                cohorts[1],
                tuning.reference,
                fold.validation,
                ref("tuning-custody", tune_auth.document.identifier),
            ),
            DatasetPartition(
                "oos",
                cohorts[2],
                oos.reference,
                fold.testing,
                ref("sealed-oos", auth.document.identifier),
            ),
            DatasetPartition(
                "holdout",
                cohorts[3],
                ref("untouched-holdout-only-metadata"),
                TimeWindow(
                    fold.testing.end + timedelta(days=1), fold.testing.end + timedelta(days=2)
                ),
                ref("holdout-not-accessible"),
            ),
        )
        p = policy(cohort_id="cohort-oos")
        plan = replace(
            base.plan,
            experiment_id=f"frozen-{fold.fold_id}",
            partitions=partitions,
            valuation_policy=ref("frozen-valuation", p.identifier),
        )
        if exit_price != "12" or (alternate_entry is not None and fold.fold_id == "fold-001"):
            grid = plan.grid
            plan = replace(
                plan,
                grid=(
                    replace(grid[0], values=(alternate_entry,))
                    if alternate_entry is not None and fold.fold_id == "fold-001"
                    else grid[0],
                    replace(grid[1], values=(exit_price,)),
                    grid[2],
                ),
            )
        lineage = tuple(
            StrategyExecutionRequest(
                plan,
                plan.document.identifier,
                source,
                source.document.identifier,
                a,
                a.document.identifier,
            )
            for source, a in ((train, train_auth), (tuning, tune_auth))
        )
        declaration = FrozenStrategySpec(
            f"evaluation-{fold.fold_id}",
            auth.cycle_id,
            fold.fold_id,
            plan,
            candidates(plan)[0],
            wf.document.identifier,
            (lineage_reference(lineage[0]), lineage_reference(lineage[1])),
            OOSMethodology("raw-price-only-no-feedback", 0, 0, 60, 60),
            p,
            fold.validation.end,
        )
        custody = SyntheticFreezeCustody(
            "separately-retained-synthetic-custodian",
            declaration.document.identifier,
            auth.document.identifier,
            declaration.frozen_at + timedelta(seconds=1),
            fold.validation.end,
            (),
            None,
        )
        request = FrozenOOSRequest(
            declaration,
            declaration.document.identifier,
            custody,
            custody.document.identifier,
            oos,
            oos.document.identifier,
            auth,
            auth.document.identifier,
            (lineage[0], lineage[1]),
            wf,
            wf.document.identifier,
            manifest,
        )
        requests.append(request)
    return wf, tuple(requests)


def valued(request: FrozenOOSRequest) -> tuple[Any, tuple[PinnedValuation, ...]]:
    base = evaluate_frozen_oos(request)
    assert base.owner is not None
    marks = []
    for record in base.owner.checkpoint.to_dict()["records"]:
        if record["status"] != "succeeded":
            continue
        case = next(c for c in request.observations.cases if c.case_id == record["job_key"])
        p = request.spec.valuation_policy.to_dict()
        evidence = ValuationEvidence(
            "independently-retained-oos-endpoints",
            base.owner.experiment.fingerprint,
            record["run_id"],
            record["output"]["execution_fingerprint"],
            case.steps[0].decision_frame.now,
            case.steps[-1].execution_frame.now,
            case.steps[0].decision_frame.quotes,
            case.steps[-1].execution_frame.quotes,
            p["max_age_seconds"],
            p["cash_flow_scope"],
        )
        marks.append(PinnedValuation(evidence, evidence.document.identifier))
    result = evaluate_frozen_oos(request, tuple(marks))
    assert result.owner is not None and result.owner.checkpoint == base.owner.checkpoint
    return result, tuple(marks)


def test_one_frozen_candidate_full_oos_flow() -> None:
    _, (request,) = fixture()
    result, marks = valued(request)
    v = result.report.to_dict()
    assert v["completed_cases"] == v["planned_cases"] == 1
    assert v["cases"][0]["execution_counts"]["fills"] == 2
    assert v["return_statistics"]["mean"] == {"numerator": "1", "denominator": "450"}
    assert (
        not v["historical_oos_eligible"]
        and not v["strict_pit_approved"]
        and not v["operational_eligible"]
    )
    verify_frozen_oos(result, v["content_fingerprint"], request, marks)
    assert evaluate_frozen_oos(request, marks) == result


def pinned(
    request: FrozenOOSRequest, *, cancel: bool = False, missing: bool = False
) -> PinnedFrozenFold:
    if cancel:
        result = evaluate_frozen_oos(request, cancelled=True)
        marks: tuple[PinnedValuation, ...] = ()
    elif missing:
        result = evaluate_frozen_oos(request)
        marks = ()
    else:
        result, marks = valued(request)
    return PinnedFrozenFold(
        request, result, result.report.to_dict()["content_fingerprint"], marks, cancel
    )


@pytest.mark.parametrize(
    "exchange", [Exchange.KOSPI, Exchange.KOSDAQ, Exchange.NYSE, Exchange.NASDAQ]
)
def test_native_exchange_calendar_and_economics(exchange: Exchange) -> None:
    _, (request,) = fixture(exchange=exchange)
    result, _ = valued(request)
    assert result.report.to_dict()["return_statistics"]["mean"] == {
        "numerator": "1",
        "denominator": "450",
    }
    assert result.owner is not None and result.owner.experiment.market == exchange.market


@pytest.mark.parametrize("mode", ["rolling", "expanding"])
def test_multiple_frozen_walk_forward_folds(mode: str) -> None:
    wf, requests = fixture(mode, 2)
    sources = tuple(pinned(r) for r in requests)
    report = summarize_frozen_walk_forward(wf, wf.document.identifier, tuple(reversed(sources)))
    value = report.to_dict()
    assert value["status"] == "complete"
    assert value["executed_cases"] == value["completed_cases"] == 2
    assert value["aggregate_statistics"]["evaluated_count"] == 2
    assert value["aggregate_statistics"]["mean"] == {"numerator": "1", "denominator": "450"}
    assert [r["fold_id"] for r in value["folds"]] == [f.fold_id for f in wf.folds]
    verify_frozen_walk_forward(
        report, value["content_fingerprint"], wf, wf.document.identifier, sources
    )


@pytest.mark.parametrize("role", ["training", "tuning", "holdout"])
def test_only_oos_authorization_can_be_constructed(role: str) -> None:
    _, (request,) = fixture()
    with pytest.raises(ContractError, match="oos_only_authorization"):
        replace(request.authorization, role=role)


def test_training_tuning_path_still_rejects_oos() -> None:
    _, (request,) = fixture()
    old = PartitionAuthorization(
        "cannot-relabel",
        "oos",
        request.trusted_observations_hash,
        request.authorization.cohort_hash,
        request.authorization.window,
    )
    path = StrategyExecutionRequest(
        request.spec.plan,
        request.spec.plan.document.identifier,
        request.observations,
        request.trusted_observations_hash,
        old,
        old.document.identifier,
    )
    with pytest.raises(ContractError, match="feedback_forbidden"):
        execute_strategy_candidates(path)


@pytest.mark.parametrize(
    "field",
    [
        "trusted_spec_hash",
        "trusted_custody_hash",
        "trusted_authorization_hash",
        "trusted_observations_hash",
        "trusted_walk_forward_hash",
    ],
)
def test_independent_pins_required(field: str) -> None:
    _, (request,) = fixture()
    changes: dict[str, Any] = {field: "0" * 64}
    with pytest.raises(ContractError, match="pin_mismatch"):
        evaluate_frozen_oos(replace(request, **changes))


def test_self_rehashed_timestamp_without_custody_is_not_a_freeze() -> None:
    _, (request,) = fixture()
    declaration = replace(request.spec, frozen_at=request.spec.frozen_at - timedelta(seconds=1))
    with pytest.raises(ContractError, match="freeze_spec_pin"):
        evaluate_frozen_oos(
            replace(request, spec=declaration, trusted_spec_hash=declaration.document.identifier)
        )


@pytest.mark.parametrize("direction", ["after_oos", "before_tuning"])
def test_invalid_freeze_chronology_even_with_consistent_synthetic_pins(direction: str) -> None:
    _, (request,) = fixture()
    at = (
        request.authorization.released_at + timedelta(seconds=1)
        if direction == "after_oos"
        else (request.spec.plan.partitions[1].window.start - timedelta(seconds=1))
    )
    declaration = replace(request.spec, frozen_at=at)
    custody = replace(
        request.custody,
        spec_hash=declaration.document.identifier,
        recorded_at=at + timedelta(seconds=1),
    )
    changed = replace(
        request,
        spec=declaration,
        trusted_spec_hash=declaration.document.identifier,
        custody=custody,
        trusted_custody_hash=custody.document.identifier,
    )
    with pytest.raises(ContractError, match="custody_boundary"):
        evaluate_frozen_oos(changed)


def test_feedback_and_prior_access_rejected() -> None:
    _, (request,) = fixture()
    with pytest.raises(ContractError, match="same_cycle_oos_feedback"):
        replace(request.custody, oos_feedback_hashes=("0" * 64,))
    with pytest.raises(ContractError, match="same_cycle_oos_feedback"):
        replace(request.custody, feedback_observed_at=request.spec.frozen_at - timedelta(seconds=1))
    with pytest.raises(ContractError, match="previously_accessed"):
        replace(request.authorization, prior_research_accesses=("tuning-feedback",))


def test_parameters_code_and_policies_bound_to_original_custody() -> None:
    _, (request,) = fixture()
    for field in ("code", "execution_policy", "risk_policy"):
        changes: dict[str, Any] = {field: ref("changed")}
        plan = replace(request.spec.plan, **changes)
        declaration = replace(request.spec, plan=plan, candidate=candidates(plan)[0])
        with pytest.raises(ContractError, match="freeze_spec_pin_mismatch"):
            evaluate_frozen_oos(
                replace(
                    request, spec=declaration, trusted_spec_hash=declaration.document.identifier
                )
            )
    grid = request.spec.plan.grid
    plan = replace(request.spec.plan, grid=(replace(grid[0], values=("11",)), *grid[1:]))
    declaration = replace(request.spec, plan=plan, candidate=candidates(plan)[0])
    with pytest.raises(ContractError, match="freeze_spec_pin_mismatch"):
        evaluate_frozen_oos(
            replace(request, spec=declaration, trusted_spec_hash=declaration.document.identifier)
        )


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"missing_quote": True}, "price_or_session_unavailable"),
        ({"wrong_member": True}, "case_membership_mismatch"),
        ({"bad_session": True}, "session_unavailable"),
        ({"expected_cases": 2}, "case_count_mismatch"),
    ],
)
def test_independently_pinned_but_invalid_oos_membership(
    kwargs: dict[str, Any], reason: str
) -> None:
    _, (request,) = fixture(**kwargs)
    with pytest.raises(ContractError, match=reason):
        evaluate_frozen_oos(request)


def test_purge_and_embargo_evidence_required_and_bound() -> None:
    _, (request,) = fixture()
    changes: dict[str, Any] = {"methodology": None}
    with pytest.raises(ContractError, match="purge_embargo_evidence"):
        replace(request.spec, **changes)
    for field in ("purge_seconds", "embargo_seconds"):
        changes = {field: 0}
        method = replace(request.spec.methodology, **changes)
        declaration = replace(request.spec, methodology=method)
        custody = replace(request.custody, spec_hash=declaration.document.identifier)
        with pytest.raises(ContractError, match="purge_embargo_mismatch"):
            evaluate_frozen_oos(
                replace(
                    request,
                    spec=declaration,
                    trusted_spec_hash=declaration.document.identifier,
                    custody=custody,
                    trusted_custody_hash=custody.document.identifier,
                )
            )
    with pytest.raises(ContractError, match="only_available_raw_prices"):
        replace(request.spec.methodology, lookback_seconds=60)


def test_duplicate_and_omitted_oos_membership() -> None:
    _, (request,) = fixture()
    with pytest.raises(ContractError, match="duplicate_oos_membership"):
        replace(request.authorization, members=(request.authorization.members[0],) * 2)
    with pytest.raises(ContractError, match="membership_bound"):
        replace(request.authorization, members=())


def test_risk_denial_remains_completed_zero_economic_outcome() -> None:
    _, (request,) = fixture(entry="9")
    result, _ = valued(request)
    value = result.report.to_dict()
    assert value["cases"][0]["execution_counts"]["risk_denied_orders"] == 1
    assert value["completed_cases"] == 1 and value["failed_cases"] == 0
    assert value["return_statistics"]["mean"] == {"numerator": "0", "denominator": "1"}


def test_missing_returns_failed_and_cancelled_cases_never_zero() -> None:
    _, (request,) = fixture()
    missing = evaluate_frozen_oos(request).report.to_dict()
    cancelled = evaluate_frozen_oos(request, cancelled=True)
    assert missing["unavailable_returns"] == 1 and missing["return_statistics"]["mean"] is None
    assert cancelled.report.to_dict()["cancelled_cases"] == 1 and cancelled.owner is None
    verify_frozen_oos(
        cancelled, cancelled.report.to_dict()["content_fingerprint"], request, cancelled=True
    )
    with pytest.raises(ContractError, match="semantic_replay"):
        verify_frozen_oos(cancelled, cancelled.report.to_dict()["content_fingerprint"], request)
    _, (limited,) = fixture(budget=1)
    failed = evaluate_frozen_oos(limited).report.to_dict()
    assert failed["failed_cases"] == 1 and failed["return_statistics"]["mean"] is None
    assert failed["cases"][0]["failure"]["code"] == "budget_exhausted"


@pytest.mark.parametrize("target", ["decision", "fill", "return", "eligibility"])
def test_rehashed_semantic_result_tampering(target: str) -> None:
    _, (request,) = fixture()
    result, marks = valued(request)
    value = result.report.to_dict()
    if target == "decision":
        value["cases"][0]["decisions"][0]["action"] = "SELL"
    elif target == "fill":
        value["cases"][0]["execution_counts"]["fills"] = 99
    elif target == "return":
        value["cases"][0]["return"]["numerator"] = "999"
    else:
        value["historical_oos_eligible"] = True
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="semantic_replay"):
        verify_frozen_oos(
            replace(result, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            request,
            marks,
        )


def test_valuation_substitution_and_untrusted_pin_rejected() -> None:
    from decimal import Decimal

    from donghak_stock_vision.trading.domain import Money

    _, (request,) = fixture()
    _, marks = valued(request)
    evidence = marks[0].evidence
    quote = replace(
        evidence.closing_marks[0], price=Money(Decimal("999"), request.spec.plan.currency)
    )
    evidence = replace(evidence, closing_marks=(quote,))
    forged = PinnedValuation(evidence, evidence.document.identifier)
    with pytest.raises(ContractError, match="mark_substitution"):
        evaluate_frozen_oos(request, (forged,))
    with pytest.raises(ContractError, match="valuation_pin"):
        evaluate_frozen_oos(request, (replace(marks[0], trusted_hash="0" * 64),))


def test_walk_forward_missing_fold_and_custody_reuse_fail_closed() -> None:
    wf, requests = fixture("rolling", 2)
    first = pinned(requests[0])
    value = summarize_frozen_walk_forward(wf, wf.document.identifier, (first,)).to_dict()
    assert value["expected_cases"] == 2 and value["status"] == "incomplete"
    assert value["aggregate_statistics"] is None
    assert value["folds"][1]["status"] == "skipped"
    with pytest.raises(ContractError, match="duplicate_or_unknown"):
        summarize_frozen_walk_forward(wf, wf.document.identifier, (first, first))


def test_source_network_and_hashes_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    _, (request,) = fixture()
    before = request.observations.document, request.spec.document, request.custody.document

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No provider or broker network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    result, marks = valued(request)
    verify_frozen_oos(result, result.report.to_dict()["content_fingerprint"], request, marks)
    assert before == (
        request.observations.document,
        request.spec.document,
        request.custody.document,
    )


def test_open_position_missing_terminal_mark_is_unavailable() -> None:
    _, (request,) = fixture(exit_price="20")
    _, marks = valued(request)
    evidence = replace(marks[0].evidence, closing_marks=())
    supplied = (PinnedValuation(evidence, evidence.document.identifier),)
    result = evaluate_frozen_oos(request, supplied)
    value = result.report.to_dict()
    assert value["completed_cases"] == 1 and value["unavailable_returns"] == 1
    assert value["cases"][0]["return"] is None
    assert (
        value["cases"][0]["valuation"]["closing"]["positions"][0]["market_value"]["available"]
        is False
    )
    assert value["return_statistics"]["mean"] is None


def test_distinct_frozen_variants_are_not_silently_pooled() -> None:
    wf, requests = fixture("rolling", 2, alternate_entry="9")
    sources = tuple(pinned(r) for r in requests)
    value = summarize_frozen_walk_forward(wf, wf.document.identifier, sources).to_dict()
    assert value["complete_folds"] == 2
    assert value["aggregate_statistics"] is None
    assert value["aggregate_unavailable_reason"] == "heterogeneous_frozen_strategy_or_policies"
    assert value["folds"][0]["return_statistics"]["mean"] == {
        "numerator": "1",
        "denominator": "450",
    }
    assert value["folds"][1]["return_statistics"]["mean"] == {"numerator": "0", "denominator": "1"}


def test_future_and_missing_information_cannot_enter_oos() -> None:
    _, (request,) = fixture()
    case = request.observations.cases[0]
    step = case.steps[0]
    quote = step.decision_frame.quotes[0]
    invalid: dict[str, Any] = {"available_at": None}
    with pytest.raises(ContractError):
        replace(quote, **invalid)
    future = replace(quote, available_at=step.decision_frame.now + timedelta(seconds=1))
    with pytest.raises(ContractError, match="lookahead"):
        replace(
            case,
            steps=(
                replace(step, decision_frame=replace(step.decision_frame, quotes=(future,))),
                *case.steps[1:],
            ),
        )
    execution = step.execution_frame
    quote = replace(execution.quotes[0], available_at=execution.now + timedelta(seconds=1))
    with pytest.raises(ContractError, match="lookahead"):
        replace(
            case,
            steps=(
                replace(step, execution_frame=replace(execution, quotes=(quote,))),
                *case.steps[1:],
            ),
        )


def test_modified_installed_strategy_reference_fails_even_with_rehashed_spec() -> None:
    _, (request,) = fixture()
    plan = replace(request.spec.plan, code=ref("other-installed-code"))
    declaration = replace(request.spec, plan=plan, candidate=candidates(plan)[0])
    custody = replace(request.custody, spec_hash=declaration.document.identifier)
    # Even internally consistent synthetic pins cannot choose another executable implementation.
    with pytest.raises(ContractError, match="strategy_code_mismatch"):
        evaluate_frozen_oos(
            replace(
                request,
                spec=declaration,
                trusted_spec_hash=declaration.document.identifier,
                custody=custody,
                trusted_custody_hash=custody.document.identifier,
            )
        )


def test_paper_and_valuation_owners_cannot_be_substituted() -> None:
    _, (request,) = fixture()
    result, marks = valued(request)
    assert result.owner is not None
    owner = result.owner
    checkpoint = owner.checkpoint.to_dict()
    checkpoint["records"][0]["output"]["execution"]["fills"] = []
    changed = FrozenJSON.freeze(checkpoint)
    forged = replace(
        result, owner=replace(owner, checkpoint=changed, trusted_checkpoint_hash=changed.identifier)
    )
    with pytest.raises(ContractError, match="semantic_replay"):
        verify_frozen_oos(forged, result.report.to_dict()["content_fingerprint"], request, marks)
    valuation = owner.valuations[0].report.to_dict()
    valuation["performance"]["total_return"]["exact_ratio"]["numerator"] = "999"
    valuation["content_fingerprint"] = digest(
        {k: v for k, v in valuation.items() if k != "content_fingerprint"}
    )
    source = replace(
        owner.valuations[0],
        report=FrozenJSON.freeze(valuation),
        trusted_report_hash=valuation["content_fingerprint"],
    )
    with pytest.raises(ContractError, match="semantic_replay"):
        verify_frozen_oos(
            replace(result, owner=replace(owner, valuations=(source,))),
            result.report.to_dict()["content_fingerprint"],
            request,
            marks,
        )


def test_no_cross_fold_freeze_or_original_dataset_reuse() -> None:
    wf, requests = fixture("rolling", 2)
    first = pinned(requests[0])
    with pytest.raises(ContractError, match="freeze_spec_pin|oos_authorization_pin"):
        evaluate_frozen_oos(
            replace(
                requests[1],
                custody=requests[0].custody,
                trusted_custody_hash=requests[0].trusted_custody_hash,
            )
        )
    changed = replace(requests[0].observations.cases[0], case_id="different-original-case")
    with pytest.raises(ContractError, match="oos_dataset_pin"):
        evaluate_frozen_oos(replace(requests[0], observations=SyntheticObservations((changed,))))
    # Source OOS observations cannot become lineage in the same frozen request.
    old = requests[0].lineage[1]
    forged = replace(
        old,
        observations=requests[0].observations,
        trusted_observations_hash=requests[0].trusted_observations_hash,
    )
    with pytest.raises(ContractError):
        evaluate_frozen_oos(replace(requests[0], lineage=(requests[0].lineage[0], forged)))
    with pytest.raises(ContractError, match="original_lineage_required"):
        changes: dict[str, Any] = {"lineage": ()}
        evaluate_frozen_oos(replace(requests[0], **changes))
    assert first.request.walk_forward == wf


def test_rehashed_walk_forward_summary_cannot_claim_complete_missing_fold() -> None:
    wf, requests = fixture("expanding", 2)
    sources = (pinned(requests[0]),)
    value = summarize_frozen_walk_forward(wf, wf.document.identifier, sources).to_dict()
    value["status"] = "complete"
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="walk_forward_replay"):
        verify_frozen_walk_forward(
            FrozenJSON.freeze(value),
            value["content_fingerprint"],
            wf,
            wf.document.identifier,
            sources,
        )


def test_untyped_valuation_summary_cannot_replace_original_evidence() -> None:
    _, (request,) = fixture()
    invalid: dict[str, Any] = {
        "evidence": FrozenJSON.freeze({"return": "999"}),
        "trusted_hash": "0" * 64,
    }
    with pytest.raises(ContractError, match="valuation_owners_required"):
        evaluate_frozen_oos(request, (PinnedValuation(**invalid),))
