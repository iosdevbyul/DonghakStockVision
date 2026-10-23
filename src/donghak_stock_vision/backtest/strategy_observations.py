"""Pinned raw synthetic cases and observation-level authorization, no holdout reader."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.simulation_spec import bounded_json
from donghak_stock_vision.backtest.simulation_validation import TimeWindow
from donghak_stock_vision.backtest.strategy_experiment import ResearchPlan, candidates
from donghak_stock_vision.backtest.validation import flag, integer, require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.strategy.paper_reference import code_reference, strategy_reference
from donghak_stock_vision.trading.domain import AccountSnapshot, Instrument
from donghak_stock_vision.trading.paper_types import FixturePolicy, PaperFrame, validate_owner, wire


@dataclass(frozen=True)
class MarketStep:
    decision_frame: PaperFrame
    execution_frame: PaperFrame
    capacity: int
    liquidity_source_id: str
    cancel_remainder: bool

    def __post_init__(self) -> None:
        require(
            type(self.decision_frame) is PaperFrame and type(self.execution_frame) is PaperFrame,
            "explicit_frames_required",
        )
        require(self.decision_frame.now < self.execution_frame.now, "later_execution_required")
        require(integer(self.capacity) <= 100, "fixture_capacity_bound")
        require(len(text(self.liquidity_source_id)) <= 128, "liquidity_identity_bound")
        flag(self.cancel_remainder)


@dataclass(frozen=True)
class ObservationCase:
    case_id: str
    instrument: Instrument
    initial_account: AccountSnapshot
    policy: FixturePolicy
    steps: tuple[MarketStep, ...]

    def __post_init__(self) -> None:
        require(len(text(self.case_id)) <= 64, "case_identity_bound")
        require(type(self.instrument) is Instrument, "instrument_required")
        require(
            type(self.initial_account) is AccountSnapshot and type(self.policy) is FixturePolicy,
            "paper_owners_required",
        )
        require(type(self.steps) is tuple and 0 < len(self.steps) <= 4, "step_limit")
        require(all(type(s) is MarketStep for s in self.steps), "step_owner_required")
        require(
            5 * len(self.steps) <= self.policy.max_commands <= 64,
            "fixture_command_capacity_required",
        )
        for left, right in zip(self.steps, self.steps[1:], strict=False):
            require(left.execution_frame.now < right.decision_frame.now, "observation_order")
        require(
            len({f.frame_id for s in self.steps for f in (s.decision_frame, s.execution_frame)})
            == 2 * len(self.steps),
            "duplicate_frame_identity",
        )
        require(
            len(self.initial_account.balances) == 1
            and self.initial_account.balances[0].available.currency is self.instrument.currency,
            "native_account_required",
        )
        require(
            all(p.instrument == self.instrument for p in self.initial_account.positions),
            "single_instrument_case",
        )
        require(
            len(self.policy.currencies) == len(self.policy.risk_policies) == 1
            and self.policy.currencies[0].currency is self.instrument.currency,
            "native_policy_required",
        )
        require(
            self.initial_account.available_at <= self.steps[0].decision_frame.now,
            "future_initial_account",
        )
        for step in self.steps:
            for frame in (step.decision_frame, step.execution_frame):
                require(
                    not frame.fx_rates
                    and len(frame.quotes) <= 1
                    and all(q.instrument == self.instrument for q in frame.quotes),
                    "single_native_observation_required",
                )
                require(
                    len(frame.sessions) <= 1 and len(frame.valuations) <= 1, "native_frame_bounds"
                )
                require(
                    all(
                        v.equity.currency == self.instrument.currency
                        and v.account_id == self.initial_account.account_id
                        for v in frame.valuations
                    ),
                    "observation_valuation_mismatch",
                )
                require(
                    all(s.exchange == self.instrument.exchange for s in frame.sessions),
                    "observation_session_mismatch",
                )
                # All evidence available at this frame, including risk/session inputs.
                times = timestamps(frame)
                require(all(t <= frame.now for t in times), "lookahead_observation")
        bounded_json(FrozenJSON.freeze(wire(self)), 128 * 1024)


def timestamps(owner: Any) -> list[datetime]:
    if isinstance(owner, datetime):
        return [owner]
    if is_dataclass(owner) and not isinstance(owner, type):
        return [t for f in fields(owner) for t in timestamps(getattr(owner, f.name))]
    if isinstance(owner, tuple):
        return [t for v in owner for t in timestamps(v)]
    return []


@dataclass(frozen=True)
class SyntheticObservations:
    cases: tuple[ObservationCase, ...]

    def __post_init__(self) -> None:
        require(type(self.cases) is tuple and 0 < len(self.cases) <= 4, "case_limit")
        require(all(type(c) is ObservationCase for c in self.cases), "case_owners_required")
        require(len({c.case_id for c in self.cases}) == len(self.cases), "duplicate_case_identity")
        require(tuple(sorted(self.cases, key=lambda c: c.case_id)) == self.cases, "case_order")
        bounded_json(self.document, 512 * 1024)

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "strategy_synthetic_observations_v1",
                "data_origin": "synthetic",
                "cases": wire(self.cases),
            }
        )

    @property
    def reference(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "artifact_id": "strategy-synthetic-observations",
                "version": "v1",
                "content_hash": self.document.identifier,
            }
        )

    @property
    def execution_policy(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "strategy_fixture_execution_v1",
                "cases": [
                    {
                        "case_id": c.case_id,
                        "policy": wire(c.policy),
                        "liquidity": [
                            {
                                "capacity": s.capacity,
                                "source": s.liquidity_source_id,
                                "cancel_remainder": s.cancel_remainder,
                            }
                            for s in c.steps
                        ],
                    }
                    for c in self.cases
                ],
            }
        )

    @property
    def risk_hash(self) -> str:
        return digest(
            [{"case_id": c.case_id, "policies": wire(c.policy.risk_policies)} for c in self.cases]
        )


@dataclass(frozen=True)
class PartitionAuthorization:
    """Independent source custodian attestation; explicit synthetic metadata, not PIT approval."""

    authorization_id: str
    role: str
    dataset_hash: str
    cohort_hash: str
    window: TimeWindow

    def __post_init__(self) -> None:
        require(len(text(self.authorization_id)) <= 128, "authorization_identity_bound")
        require(self.role in ("training", "tuning", "oos", "holdout"), "authorization_role")
        sha(self.dataset_hash)
        sha(self.cohort_hash)
        require(type(self.window) is TimeWindow, "authorization_window_required")

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {"version": "synthetic_observation_authorization_v1", **wire(self)}
        )


def authorize_observations(
    plan: ResearchPlan,
    trusted_plan_hash: str,
    source: SyntheticObservations,
    trusted_source_hash: str,
    authorization: PartitionAuthorization,
    trusted_authorization_hash: str,
) -> None:
    candidates(plan)  # Existing schema, cardinality, immutable owner and resource validation.
    require(plan.document.identifier == sha(trusted_plan_hash), "plan_pin_mismatch")
    require(
        type(source) is SyntheticObservations and type(authorization) is PartitionAuthorization,
        "original_source_owners_required",
    )
    validate_owner(source)
    validate_owner(authorization)
    require(
        source.document.identifier == sha(trusted_source_hash) == authorization.dataset_hash,
        "dataset_pin_mismatch",
    )
    require(
        authorization.document.identifier == sha(trusted_authorization_hash),
        "authorization_pin_mismatch",
    )
    partition = plan.accessible_partition(authorization.role)
    require(
        partition.dataset == source.reference
        and partition.cohort.identifier == authorization.cohort_hash
        and partition.window == authorization.window,
        "partition_binding_mismatch",
    )
    require(
        partition.provenance.to_dict()["content_hash"] == trusted_authorization_hash,
        "partition_provenance_mismatch",
    )
    require(
        plan.strategy == strategy_reference() and plan.code == code_reference(),
        "unsupported_strategy_artifact",
    )
    require(
        plan.execution_policy.to_dict()["content_hash"] == source.execution_policy.identifier
        and plan.risk_policy.to_dict()["content_hash"] == source.risk_hash,
        "source_policy_mismatch",
    )
    for case in source.cases:
        require(
            case.instrument.market == plan.market and case.instrument.currency == plan.currency,
            "source_market_currency_mismatch",
        )
        times = timestamps(case)
        require(
            bool(times) and all(partition.window.contains(t) for t in times),
            "observation_outside_authorized_partition",
        )
