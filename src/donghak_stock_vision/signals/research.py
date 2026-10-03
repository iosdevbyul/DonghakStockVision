"""In-memory HGB research inference → signal; no DB, decisions or execution."""

from typing import Protocol

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, digest
from donghak_stock_vision.models.inference import ResearchPolicy, ResearchScore, validate_features
from donghak_stock_vision.signals.domain import (
    SignalCandidate,
    SignalContext,
    SignalPolicy,
    build_signal,
)


class ResearchInference(Protocol):
    """Satisfied by the unchanged HGBInference.research public interface."""

    def research(
        self,
        names: tuple[str, ...],
        values: tuple[float, ...],
        *,
        mode: str,
        quality_flags: frozenset[str],
        policy: ResearchPolicy,
    ) -> ResearchScore: ...


def generate_signal(
    context: SignalContext,
    policy: SignalPolicy,
    values: tuple[float, ...],
    *,
    names: tuple[str, ...],
    up_model: str,
    down_model: str,
    up: ResearchInference,
    down: ResearchInference,
) -> SignalCandidate:
    validate_features(names, values)
    if digest([list(FEATURES), list(values)]) != context.feature_hash:
        raise AnalysisError("signal_feature_mismatch")
    population = "prior_decline" if values[2] < 0 else "prior_rise" if values[2] > 0 else "flat"
    if context.population != population:
        raise AnalysisError("signal_population_mismatch")
    if policy.rejected_flags(context):
        return build_signal(
            context, policy, up_model=up_model, down_model=down_model, up=None, down=None
        )
    upward = up.research(
        names,
        values,
        mode=context.mode,
        quality_flags=frozenset(context.quality_flags),
        policy=policy.research,
    )
    downward = down.research(
        names,
        values,
        mode=context.mode,
        quality_flags=frozenset(context.quality_flags),
        policy=policy.research,
    )
    return build_signal(
        context, policy, up_model=up_model, down_model=down_model, up=upward, down=downward
    )
