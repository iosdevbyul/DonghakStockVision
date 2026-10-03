# Research signal domain

`signals.domain` is an additive, immutable boundary between model inference and
future trading decisions. Existing linear `SignalService`, persistence and CLI
remain unchanged. This module does not replace their stored signal schema.

A `SignalCandidate` contains two `DirectionalSignal` objects, an explicit
`SignalContext`, model fingerprints, policy fingerprints and rejected quality
flags. Each direction preserves its raw conditional score: Up applies only in
prior_decline (return_5 < 0), Down only in prior_rise (return_5 > 0). Flat produces
two context_mismatch/null values. Two scored directions are invalid. Null never
means zero score, low risk or permission to trade.

`SignalDirection` is up/down, not BUY/SELL. `SignalStrength` deliberately has only
unclassified, not_applicable and unavailable. There are no approved strength bins,
so the score is unclassified even at 0.5 or above. Scores are not calibrated
probabilities, complements, or a shared ranking. No threshold or trading action
is introduced.

## Policy and generation

`SignalPolicy` versions the raw-score policy and carries the existing
`ResearchPolicy`. Quality flags must be canonical, explicitly supplied, and all
allowed by that policy. `research_sessions_excluded_not_holidays` is not implicitly
allowed. Denied flags produce quality_blocked/null for both directions, with the
rejected flags preserved; `generate_signal` does not invoke inference in this case.
Allowing a flag acknowledges a research limitation; it does not verify PIT or
repair market data. Model-provided policy fingerprint and flags must exactly match.

`signals.research.generate_signal` accepts the unchanged public HGBInference.research
interface through a structural protocol. It checks feature order, finite values,
feature-content hash and population consistency before invoking inference. The
resulting ResearchScore objects must match independently supplied Up/Down model
fingerprints, quality policy and conditional population. Missing, malformed,
operational or incompatible results raise AnalysisError, never a fabricated score.
Inference exceptions propagate; no partial signal is returned.

## Provenance and time boundary

Context explicitly identifies ticker, input dataset and snapshot, source input
hash, feature-content hash, data cutoff, analysis as_of, mode and real/synthetic
origin. UTC-normalized cutoff must not exceed as_of. Model fingerprint refers to
the inference envelope, which binds estimator checksum, experiment lock,
qualification and training snapshot provenance. Training snapshot and inference
snapshot are distinct references and are not silently substituted.

The caller must resolve and verify those artifacts, ensure features were computed
causally from the named snapshot, and check collection/revision availability,
research calendar and freshness before constructing context. A supplied identity
or cutoff is not proof of the underlying data. This layer has no DB access and
cannot verify external provenance. Historical research only is supported; it does
not claim strict PIT evidence. Synthetic origin propagates as synthetic_test_only.

`to_dict()` is JSON-compatible; fingerprint covers context, both direction outputs,
quality results and policies. Equal UTC instants have equal representations.
No wall clock or random source is used. operational_eligible and executable are
always serialized as false; neither is caller-configurable.

## Scope and next step

The in-memory signal boundary is ready for further integration before backtesting.
It is not an end-to-end Phase 3 execution path. A reviewed provenance/snapshot
resolver and signal persistence/query integration are still needed. Entry/exit,
position sizing, fees, execution, backtesting, registration and model training are
outside this change. Existing classification threshold 0.5 remains outside this
new signal policy. Tests use explicit inference doubles, not OOS predictions.
