"""Explicit synthetic temporal validation; never historical PIT/OOS approval or model training."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from statistics import mean, median, pvariance
from typing import Any
from zoneinfo import ZoneInfo

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_comparison import (
    PerformanceInput,
    verify_performance_comparison,
)
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.validation import (
    ContractError,
    fields,
    integer,
    reference,
    require,
    sha,
    text,
    utc,
)
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.trading.domain import Exchange
from donghak_stock_vision.trading.paper_types import Cancellation as PaperCancellation
from donghak_stock_vision.trading.paper_types import FillEvent, PaperFrame, Submission, wire

VERSION = "simulation_walk_forward_validation_v1"


@dataclass(frozen=True)
class TimeWindow:
    """UTC-normalized, half-open [start, end); no calendar-day inference."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        require(self.start < self.end, "invalid_validation_window")

    def contains(self, at: datetime) -> bool:
        return self.start <= at < self.end


@dataclass(frozen=True)
class ValidationFold:
    fold_id: str
    training: TimeWindow
    validation: TimeWindow | None
    testing: TimeWindow
    expected_cases: int

    def __post_init__(self) -> None:
        require(len(text(self.fold_id)) <= 128, "fold_id_bound")
        integer(self.expected_cases, positive=True)
        windows = (
            (self.training, self.validation, self.testing)
            if self.validation
            else (self.training, self.testing)
        )
        for window in windows:
            require(type(window) is TimeWindow, "time_window_required")
            window.__post_init__()
        require(
            all(a.end <= b.start for a, b in zip(windows, windows[1:], strict=False)),
            "overlapping_fold_windows",
        )


def walk_forward_folds(
    mode: str,
    training: TimeWindow,
    *,
    count: int,
    step_seconds: int,
    test_seconds: int,
    validation_seconds: int | None,
    purge_seconds: int,
    embargo_seconds: int,
    expected_cases: int,
) -> tuple[ValidationFold, ...]:
    """Calendar-second planning only; session membership still requires pinned evidence."""
    require(mode in {"holdout", "rolling", "expanding"}, "unknown_walk_forward_mode")
    require(1 <= integer(count, positive=True) <= 20, "fold_count_bound")
    require(count == 1 if mode == "holdout" else count >= 2, "mode_fold_count")
    integer(step_seconds, positive=True)
    integer(test_seconds, positive=True)
    integer(purge_seconds)
    integer(embargo_seconds)
    require(step_seconds >= test_seconds, "overlapping_test_schedule")
    if validation_seconds is not None:
        integer(validation_seconds, positive=True)
    output = []
    gap = timedelta(seconds=purge_seconds + embargo_seconds)
    for i in range(count):
        shift = timedelta(seconds=step_seconds * i)
        train = TimeWindow(
            training.start if mode == "expanding" else training.start + shift, training.end + shift
        )
        val = (
            TimeWindow(train.end + gap, train.end + gap + timedelta(seconds=validation_seconds))
            if validation_seconds is not None
            else None
        )
        start = (val.end if val else train.end) + gap
        output.append(
            ValidationFold(
                f"fold-{i:03}",
                train,
                val,
                TimeWindow(start, start + timedelta(seconds=test_seconds)),
                expected_cases,
            )
        )
    return tuple(output)


@dataclass(frozen=True)
class WalkForwardPlan:
    plan_id: str
    mode: str
    exchange: Exchange
    timezone: str
    dataset: FrozenJSON
    calendar: FrozenJSON
    folds: tuple[ValidationFold, ...]
    purge_seconds: int
    embargo_seconds: int
    min_training_samples: int
    min_validation_samples: int
    min_test_cases: int
    min_folds: int

    def __post_init__(self) -> None:
        require(len(text(self.plan_id)) <= 128, "plan_id_bound")
        require(type(self.exchange) is Exchange, "exchange_required")
        require(
            self.timezone
            == ("Asia/Seoul" if self.exchange.market.value == "KR" else "America/New_York"),
            "market_timezone_mismatch",
        )
        reference(self.dataset.to_dict())
        for n in (self.purge_seconds, self.embargo_seconds):
            integer(n)
        for n in (
            self.min_training_samples,
            self.min_validation_samples,
            self.min_test_cases,
            self.min_folds,
        ):
            integer(n, positive=True)
        require(
            type(self.folds) is tuple and self.min_folds <= len(self.folds) <= 20,
            "validation_fold_count",
        )
        require(self.mode in {"holdout", "rolling", "expanding"}, "unknown_walk_forward_mode")
        require(
            len(self.folds) == 1 if self.mode == "holdout" else len(self.folds) >= 2,
            "mode_fold_count",
        )
        require(len({f.fold_id for f in self.folds}) == len(self.folds), "duplicate_fold_id")
        for fold in self.folds:
            require(type(fold) is ValidationFold, "validation_fold_required")
            fold.__post_init__()
            require(fold.expected_cases >= self.min_test_cases, "minimum_test_cases")
            windows = (
                (fold.training, fold.validation, fold.testing)
                if fold.validation
                else (fold.training, fold.testing)
            )
            require(
                all(
                    a.end + timedelta(seconds=self.purge_seconds + self.embargo_seconds) <= b.start
                    for a, b in zip(windows, windows[1:], strict=False)
                ),
                "purge_embargo_boundary",
            )
        for a, b in zip(self.folds, self.folds[1:], strict=False):
            require(
                a.testing.end <= b.testing.start and a.training.end < b.training.end,
                "unordered_or_overlapping_folds",
            )
            require(
                (a.training.start == b.training.start)
                if self.mode == "expanding"
                else (
                    a.training.start < b.training.start
                    and a.training.end - a.training.start == b.training.end - b.training.start
                ),
                "invalid_training_window_progression",
            )
        _sessions(self)

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "synthetic_walk_forward_plan_v1",
                "plan_id": self.plan_id,
                "mode": self.mode,
                "exchange": self.exchange.value,
                "timezone": self.timezone,
                "dataset": self.dataset.to_dict(),
                "calendar": self.calendar.to_dict(),
                "folds": wire(self.folds),
                "purge_seconds": self.purge_seconds,
                "embargo_seconds": self.embargo_seconds,
                "min_training_samples": self.min_training_samples,
                "min_validation_samples": self.min_validation_samples,
                "min_test_cases": self.min_test_cases,
                "min_folds": self.min_folds,
            }
        )


@dataclass(frozen=True)
class FoldSource:
    fold_id: str
    comparison: FrozenJSON
    trusted_comparison_hash: str
    inputs: tuple[PerformanceInput, ...]
    comparison_policy: FrozenJSON
    trusted_policy_hash: str
    provenance: FrozenJSON | None
    trusted_provenance_hash: str | None


def _sessions(plan: WalkForwardPlan) -> dict[str, TimeWindow]:
    bounded_json(plan.calendar, 128 * 1024)
    c = fields(
        plan.calendar.to_dict(), "version calendar_id exchange timezone data_origin sessions"
    )
    require(
        c["version"] == "synthetic_session_calendar_v1"
        and c["data_origin"] == "synthetic"
        and c["exchange"] == plan.exchange.value
        and c["timezone"] == plan.timezone,
        "unsupported_session_calendar",
    )
    text(c["calendar_id"])
    require(type(c["sessions"]) is list and 0 < len(c["sessions"]) <= 256, "session_count_bound")
    output = {}
    previous = None
    zone = ZoneInfo(plan.timezone)
    for entry in c["sessions"]:
        row = fields(entry, "session_id date open_at close_at")
        key = text(row["session_id"])
        require(key not in output, "duplicate_calendar_session")
        window = TimeWindow(utc(row["open_at"]), utc(row["close_at"]))
        day = date.fromisoformat(row["date"])
        require(
            window.start.astimezone(zone).date() == day == window.end.astimezone(zone).date(),
            "session_timezone_date_mismatch",
        )
        require(previous is None or previous <= window.start, "unordered_session_calendar")
        previous = window.end
        output[key] = window
    return output


def _member_session(sessions: dict[str, TimeWindow], session_id: str, at: datetime) -> None:
    require(
        session_id in sessions and sessions[session_id].contains(at),
        "sample_outside_declared_session",
    )


def _temporal(plan: WalkForwardPlan, fold: ValidationFold, source: FoldSource) -> None:
    if source.provenance is None or source.trusted_provenance_hash is None:
        raise ContractError("temporal_provenance_unavailable")
    bounded_json(source.provenance, 512 * 1024)
    sha(source.trusted_provenance_hash)
    require(
        source.provenance.identifier == source.trusted_provenance_hash,
        "temporal_provenance_pin_mismatch",
    )
    p = fields(
        source.provenance.to_dict(),
        "version fold_id dataset dataset_available_at train_samples validation_samples "
        "test_membership strategy_freezes",
    )
    require(
        p["version"] == "synthetic_walk_forward_provenance_v1", "unsupported_training_provenance"
    )
    require(
        p["fold_id"] == fold.fold_id and p["dataset"] == plan.dataset.to_dict(),
        "temporal_dataset_or_fold_mismatch",
    )
    sessions = _sessions(plan)
    require(
        type(p["train_samples"]) is list
        and type(p["validation_samples"]) is list
        and len(p["train_samples"]) + len(p["validation_samples"]) <= 1000,
        "training_membership_bound",
    )
    require(len(p["train_samples"]) >= plan.min_training_samples, "insufficient_training_samples")
    require(
        len(p["validation_samples"]) >= plan.min_validation_samples
        if fold.validation
        else not p["validation_samples"],
        "invalid_validation_sample_count",
    )
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    last_available = fold.training.start
    for rows, span, next_start in (
        (
            p["train_samples"],
            fold.training,
            fold.validation.start if fold.validation else fold.testing.start,
        ),
        (p["validation_samples"], fold.validation, fold.testing.start),
    ):
        for item in rows:
            r = fields(
                item,
                "sample_id source feature_start anchor_at features_available_at "
                "label_end label_available_at session_id",
            )
            require(
                type(r["source"]) is dict and bool(r["source"]),
                "original_synthetic_sample_required",
            )
            require(
                r["source"].get("data_origin") == "synthetic", "unsupported_training_data_origin"
            )
            key, content = text(r["sample_id"]), digest(r["source"])
            require(
                key not in seen_ids and content not in seen_hashes,
                "duplicate_training_or_validation_membership",
            )
            seen_ids.add(key)
            seen_hashes.add(content)
            start, anchor, available, end, label_available = (
                utc(r[k])
                for k in (
                    "feature_start",
                    "anchor_at",
                    "features_available_at",
                    "label_end",
                    "label_available_at",
                )
            )
            require(
                span is not None
                and span.contains(start)
                and span.contains(anchor)
                and span.contains(label_available),
                "sample_footprint_outside_partition",
            )
            require(
                start <= available <= anchor <= end <= label_available,
                "lookahead_training_evidence",
            )
            require(
                label_available + timedelta(seconds=plan.purge_seconds + plan.embargo_seconds)
                <= next_start,
                "label_purge_embargo_violation",
            )
            _member_session(sessions, r["session_id"], anchor)
            require(
                any(s.contains(end) for s in sessions.values()), "label_outside_declared_sessions"
            )
            last_available = max(last_available, label_available)
    comp = source.comparison.to_dict()
    require(
        type(p["strategy_freezes"]) is list and len(p["strategy_freezes"]) == len(source.inputs),
        "strategy_freeze_unavailable",
    )
    freezes = {}
    membership_hash = digest({"train": p["train_samples"], "validation": p["validation_samples"]})
    for item in p["strategy_freezes"]:
        f = fields(
            item,
            "experiment_fingerprint strategy_reference strategy_artifact frozen_at parameters_hash "
            "execution_policy_hash dataset_reference_hash "
            "valuation_policy_hash training_membership_hash used_oos_results",
        )
        identity = sha(f["experiment_fingerprint"])
        require(identity not in freezes, "duplicate_strategy_freeze")
        freezes[identity] = f
    variant_ids = set()
    for owner in source.inputs:
        exp = owner.experiment
        require(
            exp.dataset == plan.dataset and exp.market is plan.exchange.market,
            "validation_dataset_or_market_mismatch",
        )
        require(exp.fingerprint in freezes, "strategy_freeze_unavailable")
        f = freezes[exp.fingerprint]
        require(
            f["strategy_reference"] == exp.strategy.to_dict()
            and f["dataset_reference_hash"] == exp.dataset.identifier,
            "strategy_freeze_identity_mismatch",
        )
        require(
            type(f["strategy_artifact"]) is dict
            and digest(f["strategy_artifact"]) == exp.strategy.to_dict()["content_hash"],
            "unverifiable_strategy_artifact",
        )
        require(
            f["parameters_hash"] == exp.parameters.identifier
            and f["execution_policy_hash"] == exp.execution_policy.identifier
            and f["valuation_policy_hash"] == source.comparison_policy.identifier
            and f["training_membership_hash"] == membership_hash,
            "strategy_freeze_binding_mismatch",
        )
        frozen = utc(f["frozen_at"])
        require(
            utc(p["dataset_available_at"]) <= frozen
            and last_available <= frozen
            and frozen + timedelta(seconds=plan.embargo_seconds) < fold.testing.start,
            "strategy_not_frozen_before_oos",
        )
        require(
            type(f["used_oos_results"]) is list and not f["used_oos_results"],
            "oos_feedback_not_supported",
        )
        variant = digest(
            {"strategy": exp.strategy.to_dict(), "parameters": exp.parameters.to_dict()}
        )
        require(variant not in variant_ids, "duplicate_strategy_variant")
        variant_ids.add(variant)
    runs = [e["runs"] for e in comp["experiments"]]
    pairs = [{r["pair_id"] for r in group} for group in runs]
    require(all(group == pairs[0] for group in pairs), "incomplete_cohort_pairing")
    require(len(pairs[0]) == fold.expected_cases, "fold_case_count_mismatch")
    require(
        type(p["test_membership"]) is list and len(p["test_membership"]) == len(pairs[0]),
        "test_membership_unavailable",
    )
    test_rows = {}
    cases = set()
    for item in p["test_membership"]:
        row = fields(
            item,
            "pair_id sample_id evaluation_case_hash session_id information_start information_end",
        )
        require(row["pair_id"] not in test_rows, "duplicate_test_membership")
        require(
            row["sample_id"] not in seen_ids and row["evaluation_case_hash"] not in seen_hashes,
            "training_test_membership_overlap",
        )
        require(row["evaluation_case_hash"] not in cases, "duplicate_evaluation_case")
        cases.add(row["evaluation_case_hash"])
        seen_ids.add(text(row["sample_id"]))
        test_rows[row["pair_id"]] = row
    require(set(test_rows) == pairs[0], "test_membership_identity_mismatch")
    for owner, group in zip(source.inputs, runs, strict=True):
        for job, r in zip(owner.experiment.jobs, group, strict=True):
            row = test_rows[r["pair_id"]]
            require(
                row["evaluation_case_hash"] == r["evaluation_case_hash"],
                "test_case_identity_mismatch",
            )
            scenario = next(
                s
                for s in owner.adapter.scenarios
                if s.scenario_id == job.parameters.to_dict()["scenario_id"]
            )
            require(
                all(i.exchange is plan.exchange for i in scenario.instruments),
                "validation_exchange_mismatch",
            )
            frames = [
                scenario.initial_frame,
                *[c.payload for c in scenario.commands if isinstance(c.payload, PaperFrame)],
            ]
            observed = [scenario.initial_account.observed_at, scenario.initial_account.available_at]
            for frame in frames:
                require(
                    all(s.exchange is plan.exchange for s in frame.sessions),
                    "validation_session_exchange_mismatch",
                )
                require(fold.testing.contains(frame.now), "scenario_outside_oos_window")
                _member_session(sessions, row["session_id"], frame.now)
                times = [(q.observed_at, q.available_at) for q in frame.quotes]
                times += [(q.observed_at, q.available_at) for q in frame.valuations]
                times += [(q.observed_at, q.available_at) for q in frame.sessions]
                for observed_at, available_at in times:
                    require(observed_at <= available_at <= frame.now, "future_scenario_observation")
                    observed.extend((observed_at, available_at))
                observed.append(frame.now)
            for command in scenario.commands:
                payload = command.payload
                if isinstance(payload, Submission):
                    observed.append(payload.order.created_at)
                elif isinstance(payload, (FillEvent, PaperCancellation)):
                    observed.append(payload.at)
            require(
                utc(row["information_start"]) == min(observed)
                and utc(row["information_end"]) == max(observed),
                "unverifiable_temporal_membership",
            )
            require(
                all(fold.testing.contains(t) for t in observed), "scenario_information_outside_oos"
            )
            for at in observed:
                _member_session(sessions, row["session_id"], at)


def _ratio(value: Fraction | None) -> dict[str, str] | None:
    return (
        {"numerator": str(value.numerator), "denominator": str(value.denominator)}
        if value is not None
        else None
    )


def _number(value: dict[str, str]) -> Fraction:
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def validate_walk_forward(
    plan: WalkForwardPlan,
    trusted_plan_hash: str,
    sources: tuple[FoldSource, ...],
    *,
    max_jobs: int,
) -> FrozenJSON:
    require(type(plan) is WalkForwardPlan, "walk_forward_plan_required")
    plan.__post_init__()
    bounded_json(plan.document, 256 * 1024)
    sha(trusted_plan_hash)
    require(plan.document.identifier == trusted_plan_hash, "walk_forward_plan_pin_mismatch")
    require(
        type(sources) is tuple and len(sources) <= len(plan.folds), "bounded_fold_sources_required"
    )
    require(all(type(s) is FoldSource for s in sources), "fold_source_required")
    require(len({s.fold_id for s in sources}) == len(sources), "duplicate_fold_source")
    require(
        {s.fold_id for s in sources} <= {f.fold_id for f in plan.folds}, "unexpected_fold_source"
    )
    integer(max_jobs, positive=True)
    require(
        sum(len(i.experiment.jobs) for s in sources for i in s.inputs) <= max_jobs <= 300,
        "validation_job_budget",
    )
    lookup = {s.fold_id: s for s in sources}
    output = []
    aggregates: dict[str, dict[str, Any]] = {}
    compatibility = None
    used_cases: set[str] = set()
    for fold in plan.folds:
        source = lookup.get(fold.fold_id)
        row: dict[str, Any] = {
            "fold": wire(fold),
            "status": "skipped",
            "temporal_eligible": False,
            "complete_oos": False,
            "reason": "missing_fold_source",
            "comparison_hash": None,
            "dataset": plan.dataset.to_dict(),
            "calendar_hash": plan.calendar.identifier,
        }
        output.append(row)
        if source is None:
            continue
        verify_performance_comparison(
            source.comparison,
            source.trusted_comparison_hash,
            source.inputs,
            source.comparison_policy,
            source.trusted_policy_hash,
            max_jobs=max_jobs,
        )
        comp = source.comparison.to_dict()
        row.update(
            comparison_hash=source.trusted_comparison_hash,
            provenance_hash=source.trusted_provenance_hash,
            performance=[
                {
                    k: e[k]
                    for k in (
                        "experiment_id",
                        "strategy",
                        "outcomes",
                        "unavailable_succeeded_count",
                        "return_statistics",
                        "runs",
                    )
                }
                for e in comp["experiments"]
            ],
            paired_comparisons=comp["paired_comparisons"],
        )
        try:
            _temporal(plan, fold, source)
            policy = {
                k: v for k, v in source.comparison_policy.to_dict().items() if k != "cohort_id"
            }
            signature = {
                "market": comp["cohort"]["market"],
                "currency": comp["cohort"]["currency"],
                "dataset": comp["cohort"]["dataset"],
                "execution_policy": comp["cohort"]["execution_policy"],
                "policy": policy,
                "limits": comp["cohort"]["limits"],
                "seed": comp["cohort"]["seed"],
                "initial_books": sorted(
                    {
                        digest(
                            {k: case["initial_account"][k] for k in ("balances", "positions")}
                        ): {k: case["initial_account"][k] for k in ("balances", "positions")}
                        for case in comp["cohort"]["evaluation_cases"].values()
                    }.values(),
                    key=digest,
                ),
                "variants": sorted(
                    [{k: e[k] for k in ("strategy", "code")} for e in comp["experiments"]],
                    key=digest,
                ),
                "parameters": [
                    i.experiment.parameters.to_dict()
                    for i in sorted(
                        source.inputs, key=lambda i: digest(i.experiment.strategy.to_dict())
                    )
                ],
            }
            require(
                compatibility is None or compatibility == signature, "incompatible_fold_aggregation"
            )
            cases = {r["evaluation_case_hash"] for e in comp["experiments"] for r in e["runs"]}
            require(not used_cases & cases, "duplicate_cross_fold_evaluation_membership")
            used_cases.update(cases)
            compatibility = signature
        except (ContractError, KeyError, TypeError) as error:
            row.update(
                status="ineligible",
                reason=str(error)
                if isinstance(error, ContractError)
                else "temporal_provenance_incomplete",
            )
            continue
        assert source.provenance is not None
        row["strategy_freezes"] = [
            {
                **{
                    k: f[k]
                    for k in (
                        "experiment_fingerprint",
                        "strategy_reference",
                        "dataset_reference_hash",
                        "frozen_at",
                        "parameters_hash",
                        "execution_policy_hash",
                        "valuation_policy_hash",
                        "training_membership_hash",
                    )
                },
                "strategy_artifact_hash": digest(f["strategy_artifact"]),
                "evidence_scope": "pinned_synthetic_declaration",
            }
            for f in source.provenance.to_dict()["strategy_freezes"]
        ]
        complete = all(
            e["return_statistics"]["evaluated_count"] == fold.expected_cases
            for e in comp["experiments"]
        )
        row.update(
            status="valid" if complete else "incomplete",
            temporal_eligible=True,
            complete_oos=complete,
            reason=None if complete else "incomplete_simulation_coverage",
        )
        for e, owner in zip(comp["experiments"], source.inputs, strict=True):
            key = digest(
                {"strategy": e["strategy"], "parameters": owner.experiment.parameters.to_dict()}
            )
            group = aggregates.setdefault(
                key,
                {
                    "strategy": e["strategy"],
                    "values": [],
                    "fold_means": [],
                    "fold_ids": [],
                    "outcomes": Counter(),
                },
            )
            group["values"].extend(
                _number(r["return"]) for r in e["runs"] if r["return"] is not None
            )
            m = e["return_statistics"]["mean"]
            group["fold_means"].append({"fold_id": fold.fold_id, "mean": m})
            group["fold_ids"].append(fold.fold_id)
            group["outcomes"].update(e["outcomes"])
    expected = sum(f.expected_cases for f in plan.folds)
    summary = []
    counts = Counter(r["status"] for r in output)
    minimum_met = counts["valid"] >= plan.min_folds
    for key, g in sorted(aggregates.items()):
        values = g["values"]
        qualified = values if minimum_met else []
        fm = [_number(f["mean"]) for f in g["fold_means"] if f["mean"] is not None]
        summary.append(
            {
                "variant_id": key,
                "minimum_folds_met": minimum_met,
                "unavailable_reason": None
                if minimum_met and values
                else "no_eligible_returns"
                if minimum_met
                else "minimum_valid_folds_not_met",
                "strategy": g["strategy"],
                "folds_used": g["fold_ids"],
                "outcomes": dict(g["outcomes"]),
                "expected_cases": expected,
                "evaluated_cases": len(values),
                "coverage": _ratio(Fraction(len(values), expected)),
                "mean": _ratio(mean(qualified)) if qualified else None,
                "median": _ratio(median(qualified)) if qualified else None,
                "population_variance": _ratio(pvariance(qualified)) if qualified else None,
                "positive": sum(v > 0 for v in values),
                "negative": sum(v < 0 for v in values),
                "flat": sum(v == 0 for v in values),
                "fold_means": g["fold_means"],
                "positive_mean_folds": sum(m > 0 for m in fm),
                "negative_mean_folds": sum(m < 0 for m in fm),
                "flat_mean_folds": sum(m == 0 for m in fm),
                "fold_mean_range": _ratio(max(fm) - min(fm)) if fm else None,
            }
        )
    status = (
        "complete"
        if counts["valid"] == len(plan.folds)
        else "incomplete"
        if summary
        else "ineligible"
    )
    value = {
        "version": VERSION,
        "simulated": True,
        "scope": "synthetic_temporal_validation_only",
        "market": plan.exchange.market.value,
        "currency": plan.exchange.currency.value,
        "expected_cases_per_variant": expected,
        "plan": plan.document.to_dict(),
        "trusted_plan_hash": trusted_plan_hash,
        "status": status,
        "minimum_fold_requirement_met": counts["valid"] >= plan.min_folds,
        "fold_counts": {k: counts[k] for k in ("valid", "incomplete", "ineligible", "skipped")},
        "folds": output,
        "aggregates": summary,
        "historical_oos_eligible": False,
        "strict_pit_approved": False,
        "operational_eligible": False,
        "limitations": [
            "independently_pinned_synthetic_attestations_not_proof_of_real_model_training",
            "timestamp_separation_does_not_prove_pit",
            "unavailable_returns_not_zero",
            "statistics_conditional_on_eligible_evaluated_cases",
            "no_strategy_selection_or_retuning",
            "no_sharpe_annualization_drawdown_or_significance",
        ],
    }
    value["content_fingerprint"] = digest(value)
    result = FrozenJSON.freeze(value)
    bounded_json(result, 8 * 1024 * 1024)
    return result


def verify_walk_forward_report(
    report: FrozenJSON,
    trusted_report_hash: str,
    plan: WalkForwardPlan,
    trusted_plan_hash: str,
    sources: tuple[FoldSource, ...],
    *,
    max_jobs: int,
) -> None:
    bounded_json(report, 8 * 1024 * 1024)
    sha(trusted_report_hash)
    v = report.to_dict()
    require(v.get("version") == VERSION, "unknown_walk_forward_report_version")
    require(
        v.get("content_fingerprint")
        == trusted_report_hash
        == digest({k: x for k, x in v.items() if k != "content_fingerprint"}),
        "walk_forward_report_hash_mismatch",
    )
    require(
        report == validate_walk_forward(plan, trusted_plan_hash, sources, max_jobs=max_jobs),
        "walk_forward_report_replay_mismatch",
    )
