"""Separate synthetic OOS custody. Never grants access through training/tuning authorization."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.simulation_validation import (
    TimeWindow,
    WalkForwardPlan,
    _sessions,
)
from donghak_stock_vision.backtest.strategy_execution import StrategyExecutionRequest
from donghak_stock_vision.backtest.strategy_experiment import (
    ResearchPlan,
    StrategyCandidate,
    candidates,
)
from donghak_stock_vision.backtest.strategy_observations import SyntheticObservations, timestamps
from donghak_stock_vision.backtest.validation import (
    fields,
    integer,
    reference,
    require,
    sha,
    text,
    utc,
)
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.paper_reference import (
    code_reference,
    parameters,
    strategy_reference,
)
from donghak_stock_vision.trading.paper_types import validate_owner, wire


def lineage_reference(owner: StrategyExecutionRequest) -> str:
    return digest(
        {
            "plan_hash": owner.trusted_plan_hash,
            "role": owner.authorization.role,
            "dataset_hash": owner.trusted_observations_hash,
            "authorization_hash": owner.trusted_authorization_hash,
        }
    )


@dataclass(frozen=True)
class OOSMethodology:
    method_id: str
    lookback_seconds: int
    label_horizon_seconds: int
    purge_seconds: int
    embargo_seconds: int

    def __post_init__(self) -> None:
        require(len(text(self.method_id)) <= 128, "method_identity_bound")
        for value in (
            self.lookback_seconds,
            self.label_horizon_seconds,
            self.purge_seconds,
            self.embargo_seconds,
        ):
            require(integer(value) <= 86400, "method_timing_bound")
        require(
            self.lookback_seconds == self.label_horizon_seconds == 0,
            "only_available_raw_prices_supported",
        )


@dataclass(frozen=True)
class OOSCaseMembership:
    case_id: str
    case_hash: str
    session_id: str

    def __post_init__(self) -> None:
        require(
            len(text(self.case_id)) <= 64 and len(text(self.session_id)) <= 128,
            "membership_identity_bound",
        )
        sha(self.case_hash)


@dataclass(frozen=True)
class FrozenOOSAuthorization:
    authorization_id: str
    cycle_id: str
    fold_id: str
    role: str
    dataset_hash: str
    cohort_hash: str
    window: TimeWindow
    sealed_at: datetime
    released_at: datetime
    members: tuple[OOSCaseMembership, ...]
    prior_research_accesses: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.authorization_id, self.cycle_id, self.fold_id):
            require(len(text(value)) <= 128, "oos_authorization_identity_bound")
        require(self.role == "oos", "oos_only_authorization")
        sha(self.dataset_hash)
        sha(self.cohort_hash)
        require(type(self.window) is TimeWindow, "oos_window_required")
        object.__setattr__(self, "sealed_at", utc(self.sealed_at))
        object.__setattr__(self, "released_at", utc(self.released_at))
        require(self.sealed_at < self.released_at <= self.window.start, "oos_release_boundary")
        require(type(self.members) is tuple and 0 < len(self.members) <= 4, "oos_membership_bound")
        require(
            all(type(m) is OOSCaseMembership for m in self.members), "membership_owner_required"
        )
        require(
            len({m.case_id for m in self.members}) == len(self.members)
            and len({m.case_hash for m in self.members}) == len(self.members),
            "duplicate_oos_membership",
        )
        require(
            tuple(sorted(self.members, key=lambda m: m.case_id)) == self.members,
            "oos_membership_order",
        )
        require(
            type(self.prior_research_accesses) is tuple and not self.prior_research_accesses,
            "oos_previously_accessed",
        )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze({"version": "synthetic_frozen_oos_authorization_v1", **wire(self)})


@dataclass(frozen=True)
class FrozenStrategySpec:
    evaluation_id: str
    cycle_id: str
    fold_id: str
    plan: ResearchPlan
    candidate: StrategyCandidate
    walk_forward_hash: str
    lineage_hashes: tuple[str, str]
    methodology: OOSMethodology
    valuation_policy: FrozenJSON
    frozen_at: datetime

    def __post_init__(self) -> None:
        for value in (self.evaluation_id, self.cycle_id, self.fold_id):
            require(len(text(value)) <= 128, "freeze_identity_bound")
        require(
            type(self.plan) is ResearchPlan
            and type(self.candidate) is StrategyCandidate
            and self.candidate in candidates(self.plan),
            "frozen_candidate_not_in_plan",
        )
        parameters(self.candidate.parameters)
        sha(self.walk_forward_hash)
        require(
            type(self.lineage_hashes) is tuple and len(self.lineage_hashes) == 2,
            "training_tuning_lineage_required",
        )
        for pin in self.lineage_hashes:
            sha(pin)
        require(type(self.methodology) is OOSMethodology, "purge_embargo_evidence_required")
        bounded_json(self.valuation_policy, 4096)
        require(
            self.valuation_policy.identifier
            == self.plan.valuation_policy.to_dict()["content_hash"],
            "frozen_valuation_policy_mismatch",
        )
        object.__setattr__(self, "frozen_at", utc(self.frozen_at))

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "frozen_strategy_oos_spec_v1",
                "evaluation_id": self.evaluation_id,
                "cycle_id": self.cycle_id,
                "fold_id": self.fold_id,
                "research_plan": self.plan.document.to_dict(),
                "candidate": self.candidate.document.to_dict(),
                "walk_forward_hash": self.walk_forward_hash,
                "lineage_hashes": list(self.lineage_hashes),
                "methodology": {
                    "version": "raw_price_oos_methodology_v1",
                    "mark_selection": "first_decision_and_terminal_execution_quotes_only",
                    **wire(self.methodology),
                },
                "valuation_policy": self.valuation_policy.to_dict(),
                "frozen_at": wire(self.frozen_at),
                "usage_restriction": "synthetic_research_only",
            }
        )


@dataclass(frozen=True)
class SyntheticFreezeCustody:
    custodian_id: str
    spec_hash: str
    authorization_hash: str
    recorded_at: datetime
    tuning_closed_at: datetime
    oos_feedback_hashes: tuple[str, ...]
    feedback_observed_at: datetime | None

    def __post_init__(self) -> None:
        require(len(text(self.custodian_id)) <= 128, "custodian_identity_bound")
        sha(self.spec_hash)
        sha(self.authorization_hash)
        object.__setattr__(self, "recorded_at", utc(self.recorded_at))
        object.__setattr__(self, "tuning_closed_at", utc(self.tuning_closed_at))
        require(
            type(self.oos_feedback_hashes) is tuple
            and not self.oos_feedback_hashes
            and self.feedback_observed_at is None,
            "same_cycle_oos_feedback_forbidden",
        )

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze({"version": "synthetic_freeze_custody_v1", **wire(self)})


@dataclass(frozen=True)
class FrozenOOSRequest:
    spec: FrozenStrategySpec
    trusted_spec_hash: str
    custody: SyntheticFreezeCustody
    trusted_custody_hash: str
    observations: SyntheticObservations
    trusted_observations_hash: str
    authorization: FrozenOOSAuthorization
    trusted_authorization_hash: str
    lineage: tuple[StrategyExecutionRequest, StrategyExecutionRequest]
    walk_forward: WalkForwardPlan
    trusted_walk_forward_hash: str
    dataset_manifest: FrozenJSON

    def verify(self) -> None:
        require(
            type(self.spec) is FrozenStrategySpec
            and type(self.custody) is SyntheticFreezeCustody
            and type(self.observations) is SyntheticObservations
            and type(self.authorization) is FrozenOOSAuthorization
            and type(self.walk_forward) is WalkForwardPlan,
            "original_oos_owners_required",
        )
        for validated_owner in (
            self.spec,
            self.custody,
            self.observations,
            self.authorization,
            self.walk_forward,
        ):
            validate_owner(validated_owner)
        spec, auth, custody = self.spec, self.authorization, self.custody
        require(
            spec.document.identifier == sha(self.trusted_spec_hash) == custody.spec_hash,
            "freeze_spec_pin_mismatch",
        )
        require(
            custody.document.identifier == sha(self.trusted_custody_hash),
            "freeze_custody_pin_mismatch",
        )
        require(
            auth.document.identifier
            == sha(self.trusted_authorization_hash)
            == custody.authorization_hash,
            "oos_authorization_pin_mismatch",
        )
        require(
            self.observations.document.identifier
            == sha(self.trusted_observations_hash)
            == auth.dataset_hash,
            "oos_dataset_pin_mismatch",
        )
        require(
            self.walk_forward.document.identifier
            == sha(self.trusted_walk_forward_hash)
            == spec.walk_forward_hash,
            "walk_forward_pin_mismatch",
        )
        bounded_json(spec.document, 1024 * 1024)
        bounded_json(self.dataset_manifest, 65536)
        manifest = fields(self.dataset_manifest.to_dict(), "version folds")
        require(manifest["version"] == "frozen_oos_dataset_manifest_v1", "unknown_oos_manifest")
        require(
            self.walk_forward.dataset.to_dict()["content_hash"] == self.dataset_manifest.identifier,
            "oos_manifest_pin_mismatch",
        )
        require(
            type(manifest["folds"]) is list
            and len(manifest["folds"]) == len(self.walk_forward.folds),
            "oos_manifest_fold_count",
        )
        entries = [
            fields(row, "fold_id dataset cohort authorization_hash") for row in manifest["folds"]
        ]
        require(
            [r["fold_id"] for r in entries] == [f.fold_id for f in self.walk_forward.folds],
            "oos_manifest_fold_order",
        )
        for row in entries:
            reference(row["dataset"])
            reference(row["cohort"])
            sha(row["authorization_hash"])
        fold = next((f for f in self.walk_forward.folds if f.fold_id == spec.fold_id), None)
        require(fold is not None and fold.validation is not None, "explicit_tuning_fold_required")
        assert fold is not None and fold.validation is not None
        require(
            auth.cycle_id == spec.cycle_id
            and auth.fold_id == spec.fold_id
            and auth.window == fold.testing,
            "frozen_fold_binding_mismatch",
        )
        partitions = {p.role: p for p in spec.plan.partitions}
        oos = partitions["oos"]
        require(
            oos.dataset
            not in tuple(partitions[r].dataset for r in ("training", "tuning", "holdout")),
            "oos_dataset_not_independent",
        )
        entry = next(r for r in entries if r["fold_id"] == spec.fold_id)
        require(
            oos.dataset == self.observations.reference
            and oos.window == auth.window
            and oos.cohort.identifier == auth.cohort_hash
            and oos.provenance.to_dict()["content_hash"] == auth.document.identifier
            and entry["dataset"] == oos.dataset.to_dict()
            and entry["cohort"] == oos.cohort.to_dict()
            and entry["authorization_hash"] == auth.document.identifier,
            "oos_partition_binding_mismatch",
        )
        require(
            partitions["training"].window == fold.training
            and partitions["tuning"].window == fold.validation,
            "lineage_fold_window_mismatch",
        )
        method = spec.methodology
        require(
            method.purge_seconds == self.walk_forward.purge_seconds
            and method.embargo_seconds == self.walk_forward.embargo_seconds,
            "purge_embargo_mismatch",
        )
        require(
            spec.plan.strategy == strategy_reference() and spec.plan.code == code_reference(),
            "frozen_strategy_code_mismatch",
        )
        require(
            spec.plan.execution_policy.to_dict()["content_hash"]
            == self.observations.execution_policy.identifier
            and spec.plan.risk_policy.to_dict()["content_hash"] == self.observations.risk_hash,
            "frozen_execution_policy_mismatch",
        )
        require(
            type(self.lineage) is tuple
            and len(self.lineage) == 2
            and all(type(r) is StrategyExecutionRequest for r in self.lineage),
            "original_lineage_required",
        )
        require(
            tuple(r.authorization.role for r in self.lineage) == ("training", "tuning"),
            "lineage_roles_mismatch",
        )
        sessions = _sessions(self.walk_forward)
        latest = fold.training.start
        for index, (owner, window, next_start, minimum) in enumerate(
            (
                (
                    self.lineage[0],
                    fold.training,
                    fold.validation.start,
                    self.walk_forward.min_training_samples,
                ),
                (
                    self.lineage[1],
                    fold.validation,
                    fold.testing.start,
                    self.walk_forward.min_validation_samples,
                ),
            )
        ):
            owner.verify()
            for case in owner.observations.cases:
                require(
                    case.instrument.exchange == self.walk_forward.exchange,
                    "lineage_exchange_mismatch",
                )
                for step in case.steps:
                    for frame in (step.decision_frame, step.execution_frame):
                        require(
                            len(frame.quotes) == len(frame.sessions) == 1,
                            "lineage_price_or_session_unavailable",
                        )
                        session = frame.sessions[0]
                        require(
                            session.source_id in sessions
                            and session.is_open is True
                            and all(
                                sessions[session.source_id].contains(t) for t in timestamps(frame)
                            ),
                            "lineage_session_membership_mismatch",
                        )
            require(
                owner.plan == spec.plan
                and owner.trusted_plan_hash == spec.plan.document.identifier
                and lineage_reference(owner) == spec.lineage_hashes[index],
                "frozen_lineage_mismatch",
            )
            require(
                sum(len(c.steps) for c in owner.observations.cases) >= minimum,
                "lineage_minimum_samples",
            )
            times = timestamps(owner.observations)
            require(all(window.contains(t) for t in times), "lineage_temporal_membership_mismatch")
            latest = max(latest, max(times))
            require(
                max(times) + timedelta(seconds=method.purge_seconds + method.embargo_seconds)
                <= next_start,
                "lineage_purge_embargo_violation",
            )
        require(
            latest <= custody.tuning_closed_at <= spec.frozen_at <= custody.recorded_at
            and custody.recorded_at + timedelta(seconds=method.embargo_seconds) < fold.testing.start
            and auth.sealed_at <= spec.frozen_at < auth.released_at,
            "freeze_custody_boundary_violation",
        )
        require(
            len(auth.members) == len(self.observations.cases) == fold.expected_cases,
            "oos_case_count_mismatch",
        )
        seen = set()
        for case, member in zip(self.observations.cases, auth.members, strict=True):
            require(
                member.case_id == case.case_id and member.case_hash == digest(wire(case)),
                "oos_case_membership_mismatch",
            )
            identity = digest({k: v for k, v in wire(case).items() if k != "case_id"})
            require(identity not in seen, "duplicate_oos_observations")
            seen.add(identity)
            require(
                case.instrument.exchange == self.walk_forward.exchange
                and case.instrument.market == spec.plan.market
                and case.instrument.currency == spec.plan.currency,
                "oos_instrument_mismatch",
            )
            require(member.session_id in sessions, "oos_session_unavailable")
            require(
                all(
                    auth.window.contains(t) and sessions[member.session_id].contains(t)
                    for t in timestamps(case)
                ),
                "oos_observation_outside_session_or_window",
            )
            for step in case.steps:
                for frame in (step.decision_frame, step.execution_frame):
                    require(
                        len(frame.quotes) == len(frame.sessions) == 1,
                        "oos_price_or_session_unavailable",
                    )
                    session = frame.sessions[0]
                    require(
                        session.source_id == member.session_id and session.is_open is True,
                        "oos_session_evidence_mismatch",
                    )
