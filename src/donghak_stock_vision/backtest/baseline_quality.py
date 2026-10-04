"""Static research-quality approval above existing validators; never a repair layer."""

from dataclasses import dataclass
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.inference import ResearchPolicy
from donghak_stock_vision.signals.domain import SignalCandidate, SignalPolicy

# Exact existing flags, statuses and guard diagnostics. Unknown strings fail closed.
# ALLOWED statuses are not interchangeable with approved snapshot flags.
INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    (
        "adjustment_mismatch",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: adjustment mismatch.",
    ),
    (
        "artifact_provenance_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: artifact provenance mismatch.",
    ),
    (
        "calendar_unverified",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Missing verified coverage cannot establish complete session ordering.",
    ),
    (
        "checkpoint_mismatch",
        "models/artifact_resolver.py,models/inference.py",
        "BLOCKED",
        "Existing validation guard: checkpoint mismatch.",
    ),
    (
        "conditional_signal_conflict",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: conditional signal conflict.",
    ),
    (
        "configuration_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: configuration mismatch.",
    ),
    (
        "constant_volatility",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: constant volatility.",
    ),
    (
        "context_mismatch",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Requested direction is outside its conditional population; null is not a score.",
    ),
    (
        "contract_conflict",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Stored analysis conflicts with its contract.",
    ),
    (
        "corporate_actions_unverified",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Coverage does not establish corporate-action quality; unsupported uncertainty.",
    ),
    ("empty_signal_prefix", "baseline_inputs", "BLOCKED", "No published prefix is available."),
    (
        "excluded_corporate_action",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: excluded corporate action.",
    ),
    (
        "experiment_configuration_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: experiment configuration mismatch.",
    ),
    (
        "experiment_link_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: experiment link mismatch.",
    ),
    (
        "feature_contract_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: feature contract mismatch.",
    ),
    (
        "feature_label_contract_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: feature label contract mismatch.",
    ),
    (
        "future_signal_input",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: future signal input.",
    ),
    (
        "future_signal_prefix",
        "baseline_inputs",
        "BLOCKED",
        "Prefix contains data beyond the decision cutoff.",
    ),
    (
        "gap_through_barrier",
        "baseline_runner",
        "BLOCKED",
        "No approved execution-price rule for a gap through barriers.",
    ),
    (
        "incompatible_artifact",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: incompatible artifact.",
    ),
    (
        "incompatible_parameters",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: incompatible parameters.",
    ),
    (
        "incompatible_research_score",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: incompatible research score.",
    ),
    (
        "incomplete_equity",
        "baseline_runner",
        "BLOCKED",
        "Required causal position valuation is incomplete.",
    ),
    (
        "inference_failed",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: inference failed.",
    ),
    (
        "insufficient_history",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: insufficient history.",
    ),
    (
        "insufficient_model_data",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "No usable directional model.",
    ),
    (
        "insufficient_point_in_time_data",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: insufficient point in time data.",
    ),
    (
        "insufficient_remaining_sessions",
        "baseline_runner",
        "BLOCKED",
        "No sufficient remaining observations for a resolved exit.",
    ),
    ("invalid", "snapshot/signal/entry/legacy quality", "BLOCKED", "Quality evidence is invalid."),
    (
        "invalid_artifact_path",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: invalid artifact path.",
    ),
    (
        "invalid_artifact_pin",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: invalid artifact pin.",
    ),
    (
        "invalid_checkpoint",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: invalid checkpoint.",
    ),
    (
        "invalid_features",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: invalid features.",
    ),
    (
        "invalid_market_order_or_duplicates",
        "data/snapshot.py,features/engine.py",
        "BLOCKED",
        "Existing validation guard: invalid market order or duplicates.",
    ),
    (
        "invalid_market_range",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: invalid market range.",
    ),
    (
        "invalid_next_bar",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Schema validation failed.",
    ),
    (
        "invalid_next_open",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Open is not a usable positive price.",
    ),
    (
        "invalid_population",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid population.",
    ),
    (
        "invalid_provenance",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: invalid provenance.",
    ),
    (
        "invalid_quality_flags",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid quality flags.",
    ),
    (
        "invalid_quality_manifest",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: invalid quality manifest.",
    ),
    (
        "invalid_quality_result",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid quality result.",
    ),
    (
        "invalid_research_policy",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: invalid research policy.",
    ),
    (
        "invalid_research_signal_request",
        "signals/integration.py",
        "BLOCKED",
        "Existing validation guard: invalid research signal request.",
    ),
    (
        "invalid_runtime",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: invalid runtime.",
    ),
    (
        "invalid_signal_enum",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal enum.",
    ),
    (
        "invalid_signal_fingerprint",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal fingerprint.",
    ),
    (
        "invalid_signal_null",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal null.",
    ),
    (
        "invalid_signal_policy",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal policy.",
    ),
    (
        "invalid_signal_score",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal score.",
    ),
    (
        "invalid_signal_status",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: invalid signal status.",
    ),
    (
        "invalid_snapshot_range",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: invalid snapshot range.",
    ),
    (
        "known_incomplete_trading_date",
        "features/engine.py,signals/integration.py",
        "BLOCKED",
        "Existing validation guard: known incomplete trading date.",
    ),
    (
        "locked_model_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: locked model mismatch.",
    ),
    (
        "malformed_artifact",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: malformed artifact.",
    ),
    (
        "malformed_provenance",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: malformed provenance.",
    ),
    (
        "missing_anchor_bar",
        "signals/integration.py",
        "BLOCKED",
        "Existing validation guard: missing anchor bar.",
    ),
    (
        "missing_artifact",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: missing artifact.",
    ),
    (
        "missing_artifact_root",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: missing artifact root.",
    ),
    ("missing_bar", "snapshot/signal/entry/legacy quality", "BLOCKED", "Required bar is absent."),
    (
        "missing_portfolio_session",
        "baseline_runner",
        "BLOCKED",
        "A held ticker is absent from an observed market session.",
    ),
    (
        "missing_research_score",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: missing research score.",
    ),
    (
        "missing_signal_bars",
        "baseline_inputs",
        "BLOCKED",
        "No bars are available to construct a signal.",
    ),
    (
        "missing_signal_identity",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: missing signal identity.",
    ),
    (
        "missing_verified_session",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: missing verified session.",
    ),
    (
        "missing_warmup_session",
        "baseline_inputs",
        "BLOCKED",
        "Required causal warmup has a missing bar.",
    ),
    (
        "mixed_data_origin_or_price_basis",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: mixed data origin or price basis.",
    ),
    (
        "mixed_series_metadata",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: mixed series metadata.",
    ),
    (
        "next_bar_unavailable",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Immediate next bar is absent.",
    ),
    ("no_trade_bar", "features/engine.py", "BLOCKED", "Existing validation guard: no trade bar."),
    (
        "nonfinite_features",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: nonfinite features.",
    ),
    (
        "not_applicable",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Direction is outside its applicable population.",
    ),
    (
        "observed_bars_unverified",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Calendar completeness is not verified; synthetic legacy fixtures are not real evidence.",
    ),
    (
        "ok",
        "signal/snapshot/legacy quality",
        "ALLOWED",
        "Legacy input validation succeeded; not a trade signal.",
    ),
    (
        "price_basis_mismatch",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Model and market price bases differ.",
    ),
    (
        "price_jump",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Phase 1 warning may indicate a corporate action; no automatic waiver.",
    ),
    (
        "provenance_hash_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: provenance hash mismatch.",
    ),
    (
        "quality_blocked",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Upstream approval failed; never override it.",
    ),
    (
        "quality_coverage_missing",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Verified coverage does not span required bars.",
    ),
    (
        "quality_not_allowed",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: quality not allowed.",
    ),
    (
        "quality_not_available_at_snapshot",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: quality not available at snapshot.",
    ),
    (
        "quality_review_may_be_retrospective",
        "snapshot",
        "ALLOWED",
        "Review timing disclaimer; verified coverage and segment checks still apply.",
    ),
    (
        "quality_unverified",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: quality unverified.",
    ),
    (
        "research_artifact_required",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: research artifact required.",
    ),
    (
        "research_calendar_mismatch",
        "data/snapshot.py,signals/integration.py",
        "BLOCKED",
        "Existing validation guard: research calendar mismatch.",
    ),
    (
        "research_calendar_requires_historical_research",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: research calendar requires historical research.",
    ),
    (
        "research_limitations_acknowledgement_required",
        "data/snapshot.py,signals/integration.py",
        "BLOCKED",
        "Existing validation guard: research limitations acknowledgement required.",
    ),
    (
        "research_only",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: research only.",
    ),
    (
        "research_sessions_excluded_not_holidays",
        "snapshot",
        "ALLOWED",
        "Explicit calendar exclusions are recorded, not converted into holidays.",
    ),
    (
        "research_signal_only",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: research signal only.",
    ),
    (
        "retrospective_revisions_possible",
        "snapshot",
        "ALLOWED",
        "Historical snapshot limitation; not evidence of a detected defective segment.",
    ),
    (
        "revision_history_unavailable",
        "snapshot",
        "ALLOWED",
        "Latest-only provenance; no PIT claim is permitted.",
    ),
    (
        "runtime_mismatch",
        "models/inference.py",
        "BLOCKED",
        "Existing validation guard: runtime mismatch.",
    ),
    (
        "runtime_provenance_mismatch",
        "models/artifact_resolver.py",
        "BLOCKED",
        "Existing validation guard: runtime provenance mismatch.",
    ),
    (
        "scored",
        "signal/snapshot/legacy quality",
        "ALLOWED",
        "Usable raw score; strategy eligibility still required.",
    ),
    (
        "scores_on_blocked_quality",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: scores on blocked quality.",
    ),
    (
        "signal_direction_mismatch",
        "signals/domain.py",
        "BLOCKED",
        "Existing validation guard: signal direction mismatch.",
    ),
    (
        "signal_quality_blocked",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Upstream signal rejected quality.",
    ),
    (
        "stale_data",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Observation is outside the accepted freshness window.",
    ),
    (
        "suspected_discontinuity",
        "features/engine.py",
        "BLOCKED",
        "Existing validation guard: suspected discontinuity.",
    ),
    ("suspected_gap", "features/engine.py", "BLOCKED", "Existing validation guard: suspected gap."),
    (
        "synthetic_mode_required",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: synthetic mode required.",
    ),
    (
        "synthetic_mode_requires_fake_provider",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: synthetic mode requires fake provider.",
    ),
    (
        "unavailable",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Required usable directional output is absent.",
    ),
    (
        "unclassified",
        "signal/snapshot/legacy quality",
        "ALLOWED",
        "No strength bin or trading implication.",
    ),
    (
        "universe_incomplete",
        "snapshot",
        "ALLOWED",
        "Incomplete universe limits interpretation, not individual bar validity.",
    ),
    (
        "unknown",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Unknown provenance/quality is not approval.",
    ),
    (
        "unsupported_or_mixed_price_basis",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Price basis is unsupported or inconsistent.",
    ),
    (
        "untrusted_artifact",
        "models/artifact_resolver.py,models/inference.py",
        "BLOCKED",
        "Existing validation guard: untrusted artifact.",
    ),
    (
        "unverified",
        "snapshot/signal/entry/legacy quality",
        "BLOCKED",
        "Quality evidence is not verified.",
    ),
    (
        "unverified_adjustment",
        "data/snapshot.py",
        "BLOCKED",
        "Existing validation guard: unverified adjustment.",
    ),
    (
        "verified",
        "signal/snapshot/legacy quality",
        "ALLOWED",
        "Verified quality state; evidence still required.",
    ),
    (
        "verified_sessions",
        "signal/snapshot/legacy quality",
        "ALLOWED",
        "Verified calendar basis; coverage and segment checks remain mandatory.",
    ),
)


APPROVED_FLAGS = (
    "quality_review_may_be_retrospective",
    "research_sessions_excluded_not_holidays",
    "retrospective_revisions_possible",
    "revision_history_unavailable",
    "universe_incomplete",
)


@dataclass(frozen=True)
class BaselineQualityPolicy:
    version: str = "baseline_research_quality_v1"
    classifications: tuple[tuple[str, str], ...] = tuple((r[0], r[2]) for r in INVENTORY)
    unknown_behavior: str = "fail_closed"

    def __post_init__(self) -> None:
        require(self.version == "baseline_research_quality_v1", "unsupported_quality_policy")
        require(self.unknown_behavior == "fail_closed", "unknown_quality_must_fail_closed")
        require(type(self.classifications) is tuple, "immutable_quality_classifications_required")
        expected = {r[0]: r[2] for r in INVENTORY}
        require(
            all(type(r) is tuple and len(r) == 2 for r in self.classifications)
            and tuple(sorted(self.classifications)) == self.classifications
            and len(dict(self.classifications)) == len(self.classifications)
            and set(dict(self.classifications)) == set(expected),
            "incomplete_quality_classification",
        )
        for name, action in self.classifications:
            require(action in {"ALLOWED", "BLOCKED"}, "invalid_quality_classification")
            require(
                expected[name] == "ALLOWED" or action == "BLOCKED",
                "quality_failure_cannot_be_waived",
            )

    @property
    def allowed_flags(self) -> tuple[str, ...]:
        return tuple(f for f in APPROVED_FLAGS if dict(self.classifications)[f] == "ALLOWED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "classifications": dict(self.classifications),
            "allowed_flags": list(self.allowed_flags),
            "unknown_behavior": self.unknown_behavior,
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def classify(self, state: str) -> str:
        require(
            type(state) is str and state in dict(self.classifications),
            "unknown_quality_state:" + str(state),
        )
        return dict(self.classifications)[state]

    def rejected_flags(self, flags: tuple[str, ...]) -> tuple[str, ...]:
        require(
            type(flags) is tuple and all(type(f) is str for f in flags), "invalid_quality_flags"
        )
        for flag in flags:
            self.classify(flag)
        return tuple(sorted(set(flags) - set(self.allowed_flags)))

    def signal_reason(self, signal: SignalCandidate) -> str | None:
        rejected = self.rejected_flags(signal.context.quality_flags)
        # Only Up is executable. Normal Down context_mismatch must remain null.
        status = self.classify(signal.up.status)
        strength = self.classify(signal.up.strength.value)
        if rejected or signal.rejected_quality_flags or signal.up.status == "quality_blocked":
            return "quality_blocked"
        if signal.up.status != "scored" or signal.up.raw_score is None:
            return "up_context_unavailable"
        if status != "ALLOWED" or strength != "ALLOWED":
            return "quality_blocked"
        return None

    def signal_policy(self) -> SignalPolicy:
        """Explicit opt-in for existing inference; never replaces its validators."""
        return SignalPolicy(
            self.version, ResearchPolicy(self.fingerprint, frozenset(self.allowed_flags))
        )
