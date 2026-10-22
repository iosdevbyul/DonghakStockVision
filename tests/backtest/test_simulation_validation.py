"""Pinned synthetic timing/strategy attestations, never historical OOS approval."""

from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass, replace
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.simulation_comparison import compare_performance
from donghak_stock_vision.backtest.simulation_validation import (
    FoldSource,
    TimeWindow,
    ValidationFold,
    WalkForwardPlan,
    validate_walk_forward,
    verify_walk_forward_report,
    walk_forward_folds,
)
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Exchange
from donghak_stock_vision.trading.paper_types import PaperFrame
from tests.backtest.test_simulation_comparison import campaign, policy, registry
from tests.trading.test_risk import KR, US


def shift(value: Any, delta: timedelta, days: int) -> Any:
    if isinstance(value, datetime):
        return value + delta
    if type(value) is date:
        return value + timedelta(days=days)
    if isinstance(value, tuple):
        return tuple(shift(v, delta, days) for v in value)
    if is_dataclass(value) and not isinstance(value, type):
        return replace(
            value,
            **{
                f.name: shift(getattr(value, f.name), delta, days)
                for f in dataclass_fields(value)
                if f.init
            },
        )
    return value


def fixture(
    mode: str = "holdout",
    count: int = 1,
    exchange: Exchange = Exchange.KOSPI,
    *,
    validation: bool = True,
    start: date = date(2026, 1, 5),
    states: dict[int, str] | None = None,
) -> tuple[WalkForwardPlan, tuple[FoldSource, ...]]:
    zone = ZoneInfo("Asia/Seoul" if exchange.market.value == "KR" else "America/New_York")
    initial = datetime.combine(start, time(9, 30), zone)
    folds = walk_forward_folds(
        mode,
        TimeWindow(initial, initial + timedelta(days=2)),
        count=count,
        step_seconds=7 * 86400,
        test_seconds=600,
        validation_seconds=86400 if validation else None,
        purge_seconds=60,
        embargo_seconds=60,
        expected_cases=1,
    )
    templates = {
        s.scenario_id: s
        for s in registry(
            replace(KR if exchange.market.value == "KR" else US, exchange=exchange)
        ).scenarios
    }
    scenarios = []
    for fold in folds:
        for variant in ("a", "b"):
            base = templates[f"{variant}-up"]
            delta = fold.testing.start - base.initial_frame.now
            days = (fold.testing.start.date() - base.initial_frame.now.date()).days
            scenarios.append(
                replace(shift(base, delta, days), scenario_id=f"{variant}-{fold.fold_id}")
            )
    adapter = PaperSimulationAdapter(tuple(scenarios))
    sessions = []
    first_day = initial.date()
    last_day = folds[-1].testing.end.astimezone(zone).date()
    day = first_day
    while day <= last_day:
        if day.weekday() < 5:
            # Explicit test-fixture calendar, not an exchange holiday API/calendar claim.
            open_time = time(9) if exchange.market.value == "KR" else time(9, 30)
            close_time = time(15, 30) if exchange.market.value == "KR" else time(16)
            sessions.append(
                {
                    "session_id": day.isoformat(),
                    "date": day.isoformat(),
                    "open_at": datetime.combine(day, open_time, zone).isoformat(),
                    "close_at": datetime.combine(day, close_time, zone).isoformat(),
                }
            )
        day += timedelta(days=1)
    calendar = FrozenJSON.freeze(
        {
            "version": "synthetic_session_calendar_v1",
            "calendar_id": "fixture-only",
            "exchange": exchange.value,
            "timezone": str(zone),
            "data_origin": "synthetic",
            "sessions": sessions,
        }
    )
    plan = WalkForwardPlan(
        "synthetic-declared-provenance",
        mode,
        exchange,
        str(zone),
        adapter.dataset_reference,
        calendar,
        folds,
        60,
        60,
        1,
        1,
        1,
        count,
    )
    sources = []
    for index, fold in enumerate(folds):
        a = campaign(adapter, "a", (f"a-{fold.fold_id}",))
        b = campaign(
            adapter,
            "b",
            (f"b-{fold.fold_id}",),
            states={0: states[index]} if states and index in states else {},
        )
        p = policy(cohort_id=fold.fold_id)
        report = compare_performance((a, b), p, p.identifier, max_jobs=2)
        source = FoldSource(
            fold.fold_id,
            report,
            report.to_dict()["content_fingerprint"],
            (a, b),
            p,
            p.identifier,
            None,
            None,
        )
        proto = provenance(plan, fold, source)
        sources.append(replace(source, provenance=proto, trusted_provenance_hash=proto.identifier))
    return plan, tuple(sources)


def provenance(plan: WalkForwardPlan, fold: ValidationFold, source: FoldSource) -> FrozenJSON:
    zone = ZoneInfo(plan.timezone)

    def sample(role: str, span: TimeWindow) -> dict[str, Any]:
        anchor = span.start + timedelta(seconds=60)
        return {
            "sample_id": f"{fold.fold_id}-{role}",
            "source": {
                "fixture_id": f"{fold.fold_id}-{role}",
                "features": [1],
                "label": 0,
                "data_origin": "synthetic",
            },
            "feature_start": (anchor - timedelta(seconds=30)).isoformat(),
            "anchor_at": anchor.isoformat(),
            "features_available_at": anchor.isoformat(),
            "label_end": (anchor + timedelta(seconds=60)).isoformat(),
            "label_available_at": (anchor + timedelta(seconds=61)).isoformat(),
            "session_id": anchor.astimezone(zone).date().isoformat(),
        }

    train = [sample("train", fold.training)]
    val = [sample("validation", fold.validation)] if fold.validation else []
    c = source.comparison.to_dict()
    r = c["experiments"][0]["runs"][0]
    owner = source.inputs[0]
    scenario = next(
        s
        for s in owner.adapter.scenarios
        if s.scenario_id == owner.experiment.jobs[0].parameters.to_dict()["scenario_id"]
    )
    frames = [
        scenario.initial_frame,
        *[x.payload for x in scenario.commands if isinstance(x.payload, PaperFrame)],
    ]
    observed = [scenario.initial_account.observed_at, scenario.initial_account.available_at]
    for frame in frames:
        observed.append(frame.now)
        for values in (frame.quotes, frame.valuations, frame.sessions):
            for q in values:
                observed.extend((q.observed_at, q.available_at))
    members = [
        {
            "pair_id": r["pair_id"],
            "sample_id": f"{fold.fold_id}-oos",
            "evaluation_case_hash": r["evaluation_case_hash"],
            "session_id": fold.testing.start.astimezone(zone).date().isoformat(),
            "information_start": min(observed).isoformat(),
            "information_end": max(observed).isoformat(),
        }
    ]
    freezes = []
    for i in source.inputs:
        e = i.experiment
        freezes.append(
            {
                "experiment_fingerprint": e.fingerprint,
                "strategy_reference": e.strategy.to_dict(),
                "dataset_reference_hash": e.dataset.identifier,
                "strategy_artifact": {"variant": e.strategy.to_dict()["version"]},
                "frozen_at": (fold.testing.start - timedelta(minutes=10)).isoformat(),
                "parameters_hash": e.parameters.identifier,
                "execution_policy_hash": e.execution_policy.identifier,
                "valuation_policy_hash": source.comparison_policy.identifier,
                "training_membership_hash": digest({"train": train, "validation": val}),
                "used_oos_results": [],
            }
        )
    return FrozenJSON.freeze(
        {
            "version": "synthetic_walk_forward_provenance_v1",
            "fold_id": fold.fold_id,
            "dataset": plan.dataset.to_dict(),
            "dataset_available_at": (plan.folds[0].training.start - timedelta(days=1)).isoformat(),
            "train_samples": train,
            "validation_samples": val,
            "test_membership": members,
            "strategy_freezes": freezes,
        }
    )


def run(plan: WalkForwardPlan, sources: tuple[FoldSource, ...]) -> FrozenJSON:
    return validate_walk_forward(plan, plan.document.identifier, sources, max_jobs=30)


def mutate(source: FoldSource, edit: Any, *, repin: bool = True) -> FoldSource:
    assert source.provenance is not None
    p = source.provenance.to_dict()
    edit(p)
    value = FrozenJSON.freeze(p)
    return replace(
        source,
        provenance=value,
        trusted_provenance_hash=value.identifier if repin else source.trusted_provenance_hash,
    )


@pytest.mark.parametrize("exchange", list(Exchange))
def test_single_holdout_uses_verified_returns_without_historical_approval(
    exchange: Exchange,
) -> None:
    plan, sources = fixture(exchange=exchange)
    v = run(plan, sources).to_dict()
    assert v["status"] == "complete"
    assert v["fold_counts"]["valid"] == 1
    assert v["folds"][0]["complete_oos"]
    assert (
        not v["historical_oos_eligible"]
        and not v["strict_pit_approved"]
        and not v["operational_eligible"]
    )
    means = {a["strategy"]["version"]: a["mean"] for a in v["aggregates"]}
    assert means["a"] == {"numerator": "1", "denominator": "450"}
    assert means["b"] == {"numerator": "1", "denominator": "150"}
    assert v["folds"][0]["paired_comparisons"][0]["statistics"]["mean"] == {
        "numerator": "1",
        "denominator": "225",
    }


@pytest.mark.parametrize("mode", ["rolling", "expanding"])
def test_walk_forward_ordering_aggregation_and_fold_mean_consistency(mode: str) -> None:
    plan, sources = fixture(mode, 3)
    v = run(plan, tuple(reversed(sources))).to_dict()
    assert v["status"] == "complete"
    assert [r["fold"]["fold_id"] for r in v["folds"]] == [f.fold_id for f in plan.folds]
    for a in v["aggregates"]:
        assert a["expected_cases"] == a["evaluated_cases"] == 3
        assert a["positive_mean_folds"] == 3
        assert a["population_variance"] == {"numerator": "0", "denominator": "1"}
    starts = [f.training.start for f in plan.folds]
    assert (len(set(starts)) == 1) is (mode == "expanding")
    assert all(
        a.testing.end <= b.testing.start for a, b in zip(plan.folds, plan.folds[1:], strict=False)
    )


def test_optional_validation_and_half_open_boundaries() -> None:
    plan, sources = fixture(validation=False)
    assert plan.folds[0].validation is None
    assert run(plan, sources).to_dict()["status"] == "complete"
    w = plan.folds[0].testing
    assert w.contains(w.start) and not w.contains(w.end)
    with pytest.raises(ValueError, match="overlapping"):
        replace(plan.folds[0], training=TimeWindow(w.start, w.end))


@pytest.mark.parametrize(
    "change", ["purge", "embargo", "minimum", "ordering", "duplicate", "mode", "timezone", "naive"]
)
def test_invalid_plans_and_boundaries_fail_closed(change: str) -> None:
    plan, _ = fixture("rolling", 2)
    with pytest.raises(ValueError):
        if change == "purge":
            replace(plan, purge_seconds=121)
        elif change == "embargo":
            replace(plan, embargo_seconds=121)
        elif change == "minimum":
            replace(plan, min_folds=3)
        elif change == "ordering":
            replace(plan, folds=tuple(reversed(plan.folds)))
        elif change == "duplicate":
            replace(plan, folds=(plan.folds[0], plan.folds[0]))
        elif change == "mode":
            replace(plan, mode="unknown")
        elif change == "timezone":
            replace(plan, timezone="UTC")
        else:
            TimeWindow(datetime(2026, 1, 1), datetime(2026, 1, 2))


@pytest.mark.parametrize(
    "change",
    [
        "future_feature",
        "future_label",
        "train_test_overlap",
        "frozen_late",
        "frozen_stale",
        "strategy",
        "parameters",
        "dataset",
        "policy",
        "feedback",
        "membership",
        "duplicate_member",
        "sample_minimum",
        "wrong_fold",
        "session",
        "outside_oos",
        "provenance_version",
        "missing_freeze",
    ],
)
def test_leakage_and_missing_temporal_evidence_invalidate_not_guess(change: str) -> None:
    plan, sources = fixture()
    fold = plan.folds[0]

    def edit(p: dict[str, Any]) -> None:
        train = p["train_samples"][0]
        f = p["strategy_freezes"][0]
        if change == "future_feature":
            train["features_available_at"] = (fold.testing.start + timedelta(seconds=1)).isoformat()
        elif change == "future_label":
            train["label_end"] = fold.testing.start.isoformat()
            train["label_available_at"] = fold.testing.start.isoformat()
        elif change == "train_test_overlap":
            p["test_membership"][0]["sample_id"] = train["sample_id"]
        elif change == "frozen_late":
            f["frozen_at"] = fold.testing.start.isoformat()
        elif change == "frozen_stale":
            f["frozen_at"] = fold.training.start.isoformat()
        elif change == "strategy":
            f["strategy_artifact"] = {"variant": "forged"}
        elif change == "parameters":
            f["parameters_hash"] = "0" * 64
        elif change == "dataset":
            p["dataset"]["content_hash"] = "0" * 64
        elif change == "policy":
            f["valuation_policy_hash"] = "0" * 64
        elif change == "feedback":
            f["used_oos_results"] = [sources[0].trusted_comparison_hash]
        elif change == "membership":
            p["test_membership"][0]["evaluation_case_hash"] = "0" * 64
        elif change == "duplicate_member":
            p["test_membership"] *= 2
        elif change == "sample_minimum":
            p["train_samples"] = []
        elif change == "wrong_fold":
            p["fold_id"] = "another-fold"
        elif change == "session":
            p["test_membership"][0]["session_id"] = "unavailable"
        elif change == "outside_oos":
            p["test_membership"][0]["information_start"] = fold.training.start.isoformat()
        elif change == "provenance_version":
            p["version"] = "unknown"
        else:
            p["strategy_freezes"] = []

    v = run(plan, (mutate(sources[0], edit),)).to_dict()
    assert v["status"] == "ineligible"
    assert v["folds"][0]["reason"]
    assert v["aggregates"] == []
    assert not v["folds"][0]["complete_oos"]


def test_existing_paper_contract_without_new_provenance_is_ineligible() -> None:
    plan, sources = fixture()
    source = replace(sources[0], provenance=None, trusted_provenance_hash=None)
    v = run(plan, (source,)).to_dict()
    assert v["folds"][0]["reason"] == "temporal_provenance_unavailable"
    assert v["aggregates"] == []


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_failed_cases_never_become_complete_oos_or_zero_return(state: str) -> None:
    plan, sources = fixture(states={0: state})
    v = run(plan, sources).to_dict()
    row = v["folds"][0]
    assert row["status"] == "incomplete" and not row["complete_oos"]
    assert row["performance"][1]["outcomes"][state] == 1
    assert not v["minimum_fold_requirement_met"]
    b = next(g for g in v["aggregates"] if g["strategy"]["version"] == "b")
    assert b["expected_cases"] == 1 and b["evaluated_cases"] == 0
    assert b["mean"] is None


def test_skipped_folds_stay_in_denominator_and_minimum_blocks_aggregate_metrics() -> None:
    plan, sources = fixture("rolling", 2)
    v = run(plan, sources[:1]).to_dict()
    assert v["fold_counts"]["skipped"] == 1 and v["status"] == "incomplete"
    for a in v["aggregates"]:
        assert a["coverage"] == {"numerator": "1", "denominator": "2"}
        assert a["mean"] is None and a["unavailable_reason"] == "minimum_valid_folds_not_met"


def test_market_calendar_dst_and_date_identity() -> None:
    plan, sources = fixture("rolling", 2, Exchange.NASDAQ, validation=False, start=date(2026, 3, 2))
    assert run(plan, sources).to_dict()["status"] == "complete"
    rows = plan.calendar.to_dict()["sessions"]
    before = next(r for r in rows if r["date"] == "2026-03-06")
    after = next(r for r in rows if r["date"] == "2026-03-09")
    assert before["open_at"].endswith("-05:00") and after["open_at"].endswith("-04:00")
    c = plan.calendar.to_dict()
    c["sessions"][0]["date"] = "2025-01-01"
    with pytest.raises(ValueError, match="timezone_date"):
        replace(plan, calendar=FrozenJSON.freeze(c))


def test_session_close_exclusive_and_forged_calendar_not_official() -> None:
    plan, sources = fixture()
    c = plan.calendar.to_dict()
    day = plan.folds[0].testing.start.astimezone(ZoneInfo(plan.timezone)).date().isoformat()
    next(r for r in c["sessions"] if r["date"] == day)["close_at"] = plan.folds[
        0
    ].testing.start.isoformat()
    changed = replace(plan, calendar=FrozenJSON.freeze(c))
    assert run(changed, sources).to_dict()["folds"][0]["status"] == "ineligible"
    c["data_origin"] = "real"
    with pytest.raises(ValueError, match="unsupported_session_calendar"):
        replace(plan, calendar=FrozenJSON.freeze(c))


@pytest.mark.parametrize(
    "target", ["plan_pin", "provenance_pin", "comparison", "report", "report_version"]
)
def test_trusted_pins_and_rehashed_economic_or_validation_tampering(target: str) -> None:
    plan, sources = fixture()
    if target == "plan_pin":
        with pytest.raises(ValueError, match="plan_pin"):
            validate_walk_forward(plan, "0" * 64, sources, max_jobs=2)
    elif target == "provenance_pin":
        source = mutate(
            sources[0],
            lambda p: p.update(dataset_available_at="2025-01-01T00:00:00+00:00"),
            repin=False,
        )
        assert (
            run(plan, (source,)).to_dict()["folds"][0]["reason"]
            == "temporal_provenance_pin_mismatch"
        )
    elif target == "comparison":
        c = sources[0].comparison.to_dict()
        c["experiments"][0]["return_statistics"]["mean"]["numerator"] = "999"
        c["content_fingerprint"] = digest(
            {k: v for k, v in c.items() if k != "content_fingerprint"}
        )
        with pytest.raises(ValueError, match="replay_mismatch"):
            run(
                plan,
                (
                    replace(
                        sources[0],
                        comparison=FrozenJSON.freeze(c),
                        trusted_comparison_hash=c["content_fingerprint"],
                    ),
                ),
            )
    else:
        report = run(plan, sources).to_dict()
        if target == "report":
            report["aggregates"][0]["mean"]["numerator"] = "999"
        else:
            report["version"] = "unknown"
        report["content_fingerprint"] = digest(
            {k: v for k, v in report.items() if k != "content_fingerprint"}
        )
        with pytest.raises(ValueError):
            verify_walk_forward_report(
                FrozenJSON.freeze(report),
                report["content_fingerprint"],
                plan,
                plan.document.identifier,
                sources,
                max_jobs=2,
            )


def test_deterministic_full_verification_read_only_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("no network")

    monkeypatch.setattr(socket, "socket", forbidden)
    plan, sources = fixture()
    before = plan.document, sources[0].comparison, sources[0].inputs[0].checkpoint
    a = run(plan, sources)
    assert run(plan, sources) == a
    verify_walk_forward_report(
        a, a.to_dict()["content_fingerprint"], plan, plan.document.identifier, sources, max_jobs=2
    )
    assert (plan.document, sources[0].comparison, sources[0].inputs[0].checkpoint) == before
    assert (
        Fraction(
            int(a.to_dict()["aggregates"][0]["coverage"]["numerator"]),
            int(a.to_dict()["aggregates"][0]["coverage"]["denominator"]),
        )
        == 1
    )


def test_unmatched_comparison_cannot_be_presented_as_complete_oos() -> None:
    plan, sources = fixture()
    original = sources[0]
    a = original.inputs[0]
    b = campaign(a.adapter, "b", (f"b-{plan.folds[0].fold_id}",), keys=("unmatched-key",))
    p = original.comparison_policy
    comp = compare_performance((a, b), p, p.identifier, max_jobs=2)
    source = replace(
        original,
        comparison=comp,
        trusted_comparison_hash=comp.to_dict()["content_fingerprint"],
        inputs=(a, b),
    )
    proto = provenance(plan, plan.folds[0], source)
    source = replace(source, provenance=proto, trusted_provenance_hash=proto.identifier)
    v = run(plan, (source,)).to_dict()
    assert v["folds"][0]["reason"] == "incomplete_cohort_pairing"
    assert not v["folds"][0]["complete_oos"]


def test_duplicate_market_case_is_not_two_independent_oos_samples() -> None:
    plan, sources = fixture()
    fold = replace(plan.folds[0], expected_cases=2)
    plan = replace(plan, folds=(fold,))
    original = sources[0]
    adapter = original.inputs[0].adapter
    inputs = tuple(campaign(adapter, v, (f"{v}-{fold.fold_id}",) * 2) for v in ("a", "b"))
    p = original.comparison_policy
    c = compare_performance(inputs, p, p.identifier, max_jobs=4)
    source = replace(
        original,
        comparison=c,
        trusted_comparison_hash=c.to_dict()["content_fingerprint"],
        inputs=inputs,
    )
    proto = provenance(plan, fold, source).to_dict()
    r = c.to_dict()["experiments"][0]["runs"][1]
    proto["test_membership"].append(
        {
            **proto["test_membership"][0],
            "pair_id": r["pair_id"],
            "sample_id": "different-name-same-case",
        }
    )
    document = FrozenJSON.freeze(proto)
    source = replace(source, provenance=document, trusted_provenance_hash=document.identifier)
    assert run(plan, (source,)).to_dict()["folds"][0]["reason"] == "duplicate_evaluation_case"


def test_skipped_sources_are_not_fabricated_failures_and_duplicate_sources_rejected() -> None:
    plan, sources = fixture()
    v = run(plan, ()).to_dict()
    assert v["status"] == "ineligible" and v["fold_counts"]["skipped"] == 1
    assert v["expected_cases_per_variant"] == 1
    assert v["aggregates"] == []
    with pytest.raises(ValueError):
        run(plan, (sources[0], sources[0]))
    with pytest.raises(ValueError, match="unexpected"):
        run(plan, (replace(sources[0], fold_id="unknown-fold"),))
    with pytest.raises(ValueError, match="job_budget"):
        validate_walk_forward(plan, plan.document.identifier, sources, max_jobs=1)


def test_missing_training_original_payload_is_not_a_training_summary() -> None:
    plan, sources = fixture()
    changed = mutate(sources[0], lambda p: p["train_samples"][0].update(source={}))
    assert (
        run(plan, (changed,)).to_dict()["folds"][0]["reason"]
        == "original_synthetic_sample_required"
    )


def test_freeze_embargo_strict_and_dataset_availability_checked() -> None:
    plan, sources = fixture()
    boundary = plan.folds[0].testing.start - timedelta(seconds=plan.embargo_seconds)
    late = mutate(
        sources[0], lambda p: p["strategy_freezes"][0].update(frozen_at=boundary.isoformat())
    )
    assert run(plan, (late,)).to_dict()["folds"][0]["reason"] == "strategy_not_frozen_before_oos"
    unavailable = mutate(
        sources[0], lambda p: p.update(dataset_available_at=plan.folds[0].testing.start.isoformat())
    )
    assert (
        run(plan, (unavailable,)).to_dict()["folds"][0]["reason"]
        == "strategy_not_frozen_before_oos"
    )


def test_only_mutually_compatible_verified_folds_are_aggregated() -> None:
    plan, sources = fixture("rolling", 2)
    original = sources[1]
    fold = plan.folds[1]
    inputs = tuple(
        campaign(
            original.inputs[0].adapter,
            v,
            (f"{v}-{fold.fold_id}",),
            parameters=FrozenJSON.freeze({"parameters": "changed"}),
        )
        for v in ("a", "b")
    )
    p = original.comparison_policy
    comp = compare_performance(inputs, p, p.identifier, max_jobs=2)
    source = replace(
        original,
        inputs=inputs,
        comparison=comp,
        trusted_comparison_hash=comp.to_dict()["content_fingerprint"],
    )
    proto = provenance(plan, fold, source)
    source = replace(source, provenance=proto, trusted_provenance_hash=proto.identifier)
    v = run(plan, (sources[0], source)).to_dict()
    assert v["folds"][1]["reason"] == "incompatible_fold_aggregation"
    assert all(a["coverage"] == {"numerator": "1", "denominator": "2"} for a in v["aggregates"])
    assert all(a["mean"] is None for a in v["aggregates"])
