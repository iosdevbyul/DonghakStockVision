"""Immutable research signal domain; scores are not orders or calibrated probabilities."""

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from donghak_stock_vision.data.learning import AnalysisError, digest, timestamp
from donghak_stock_vision.models.inference import ResearchPolicy, ResearchScore


class SignalDirection(StrEnum):
    UP = "up"
    DOWN = "down"


class SignalStrength(StrEnum):
    # No unapproved numeric bins, ranking or trade threshold.
    UNCLASSIFIED = "unclassified"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


def identity(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise AnalysisError("missing_signal_identity")


def fingerprint(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise AnalysisError("invalid_signal_fingerprint")


@dataclass(frozen=True)
class SignalContext:
    ticker: str
    as_of: datetime
    data_cutoff: datetime
    dataset_id: str
    snapshot_id: str
    input_hash: str
    feature_hash: str
    population: str
    quality_flags: tuple[str, ...]
    mode: str
    data_origin: str

    def __post_init__(self) -> None:
        for value in (self.ticker, self.dataset_id, self.snapshot_id):
            identity(value)
        fingerprint(self.input_hash)
        fingerprint(self.feature_hash)
        if self.mode != "historical_research" or self.data_origin not in {"real", "synthetic"}:
            raise AnalysisError("research_signal_only")
        if self.population not in {"prior_decline", "prior_rise", "flat"}:
            raise AnalysisError("invalid_population")
        if (
            type(self.quality_flags) is not tuple
            or any(type(flag) is not str or not flag for flag in self.quality_flags)
            or self.quality_flags != tuple(sorted(set(self.quality_flags)))
        ):
            raise AnalysisError("invalid_quality_flags")
        object.__setattr__(self, "as_of", timestamp(self.as_of))
        object.__setattr__(self, "data_cutoff", timestamp(self.data_cutoff))
        if self.data_cutoff > self.as_of:
            raise AnalysisError("future_signal_input")


@dataclass(frozen=True)
class SignalPolicy:
    version: str
    research: ResearchPolicy

    def __post_init__(self) -> None:
        identity(self.version)
        if type(self.research) is not ResearchPolicy:
            raise AnalysisError("invalid_signal_policy")

    @property
    def fingerprint(self) -> str:
        return digest(["raw_conditional_score_v1", self.version, self.research.fingerprint])

    def rejected_flags(self, context: SignalContext) -> tuple[str, ...]:
        return tuple(sorted(set(context.quality_flags) - self.research.allowed_quality_flags))


@dataclass(frozen=True)
class DirectionalSignal:
    direction: SignalDirection
    raw_score: float | None
    status: str
    strength: SignalStrength
    model_fingerprint: str

    def __post_init__(self) -> None:
        fingerprint(self.model_fingerprint)
        if type(self.direction) is not SignalDirection or type(self.strength) is not SignalStrength:
            raise AnalysisError("invalid_signal_enum")
        expected = {
            "scored": SignalStrength.UNCLASSIFIED,
            "context_mismatch": SignalStrength.NOT_APPLICABLE,
            "quality_blocked": SignalStrength.UNAVAILABLE,
        }
        if self.status not in expected or self.strength != expected[self.status]:
            raise AnalysisError("invalid_signal_status")
        if self.status == "scored":
            if (
                self.raw_score is None
                or type(self.raw_score) not in {int, float}
                or not math.isfinite(self.raw_score)
                or not (0 <= self.raw_score <= 1)
            ):
                raise AnalysisError("invalid_signal_score")
        elif self.raw_score is not None:
            raise AnalysisError("invalid_signal_null")


@dataclass(frozen=True)
class SignalCandidate:
    context: SignalContext
    up: DirectionalSignal
    down: DirectionalSignal
    policy_fingerprint: str
    quality_policy_fingerprint: str
    rejected_quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        fingerprint(self.policy_fingerprint)
        fingerprint(self.quality_policy_fingerprint)
        if self.up.direction != SignalDirection.UP or self.down.direction != SignalDirection.DOWN:
            raise AnalysisError("signal_direction_mismatch")
        if (
            type(self.rejected_quality_flags) is not tuple
            or self.rejected_quality_flags != tuple(sorted(set(self.rejected_quality_flags)))
            or not set(self.rejected_quality_flags) <= set(self.context.quality_flags)
        ):
            raise AnalysisError("invalid_quality_result")
        for signal in (self.up, self.down):
            eligible = self.context.population == (
                "prior_decline" if signal.direction == SignalDirection.UP else "prior_rise"
            )
            expected = (
                "quality_blocked"
                if self.rejected_quality_flags
                else ("scored" if eligible else "context_mismatch")
            )
            if signal.status != expected:
                raise AnalysisError("conditional_signal_conflict")

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["context"]["as_of"] = self.context.as_of.isoformat()
        result["context"]["data_cutoff"] = self.context.data_cutoff.isoformat()
        result.update(
            operational_eligible=False,
            executable=False,
            usage_restriction="synthetic_test_only"
            if self.context.data_origin == "synthetic"
            else "research_only",
            quality_allowed=not self.rejected_quality_flags,
        )
        return result

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())


def build_signal(
    context: SignalContext,
    policy: SignalPolicy,
    *,
    up_model: str,
    down_model: str,
    up: ResearchScore | None,
    down: ResearchScore | None,
) -> SignalCandidate:
    """Validate inference outputs against independently supplied model identities."""
    rejected = policy.rejected_flags(context)
    signals = []
    for direction, expected_model, output in (
        (SignalDirection.UP, up_model, up),
        (SignalDirection.DOWN, down_model, down),
    ):
        fingerprint(expected_model)
        if rejected:
            if output is not None:
                raise AnalysisError("scores_on_blocked_quality")
            signal = DirectionalSignal(
                direction, None, "quality_blocked", SignalStrength.UNAVAILABLE, expected_model
            )
        else:
            if output is None or type(output) is not ResearchScore:
                raise AnalysisError("missing_research_score")
            if (
                output.direction != direction
                or output.artifact_fingerprint != expected_model
                or output.policy_fingerprint != policy.research.fingerprint
                or output.quality_flags != context.quality_flags
                or output.operational_eligible is not False
                or output.executable is not False
            ):
                raise AnalysisError("incompatible_research_score")
            strength = (
                SignalStrength.UNCLASSIFIED
                if output.status == "scored"
                else SignalStrength.NOT_APPLICABLE
            )
            signal = DirectionalSignal(
                direction, output.score, output.status, strength, expected_model
            )
        signals.append(signal)
    return SignalCandidate(
        context, signals[0], signals[1], policy.fingerprint, policy.research.fingerprint, rejected
    )
