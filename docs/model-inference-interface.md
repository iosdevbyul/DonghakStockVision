# Research model inference interface

`models.inference.ScoreModel` accepts an explicit feature-name tuple and values,
and returns only a scalar score. `LinearInference` delegates to the existing JSON
linear implementation. Existing model loading, signal storage, CLI and Phase 3
contracts are unchanged. No threshold, trading action, sizing or calibration is
part of this interface.

`HGBArtifact` records checkpoint SHA-256, experiment lock and qualification IDs,
source snapshot identity, complete canonical estimator parameters, exact sklearn /
NumPy / SciPy versions, direction and feature/label versions. Its fingerprint
covers every field. Feature ordering must exactly equal `ohlcv_value_v1`:
return_1, return_3, return_5, close_to_sma10, volatility_10, range_to_close,
volume_change_10, trading_value_change_10. Merely supplying eight numbers is
insufficient. Reordered, nonfinite or incompatible inputs fail closed.

## Trust boundary

`HGBInference(artifact, checkpoint_bytes,
trusted_artifact_fingerprint=independently_verified_fingerprint)` is a **trusted
local checkpoint** interface. Pickle can execute code; neither SHA nor post-load
type checks make arbitrary pickle safe. Never expose this loader to uploaded or
untrusted artifacts. Before constructing the manifest, the caller must verify
checkpoint bytes against the existing experiment lock, qualification against
that lock, source snapshot and the training feature order. The trusted fingerprint
must be pinned separately; copying a supplied artifact's own fingerprint is not
verification. The loader checks integrity and runtime compatibility before
unpickling, then exact estimator type, full parameters, class ordering, feature
count, fitted iteration count and disabled early stopping. No fitting occurs.
The manifest is an inference envelope, **not model registration** or a claim that
research evidence is PIT evidence. Original estimators are not converted or saved
again. Python/runtime environment should be archived along with the checkpoint.

Only the approved HGB-7 Up and HGB-15 Down configurations are accepted. Full
parameters are compared, including parameters not explicitly listed in the
experiment summary. scikit-learn dependencies remain in the existing learning
extra; linear inference does not import them.

## Research policy and conditional null

The higher-level `research` method requires historical_research and an explicit
`ResearchPolicy` quality allowlist. Empty allowlists allow only inputs without
quality flags. `research_sessions_excluded_not_holidays` is allowed **only** when
named in that allowlist, following verification of the research calendar policy.
All other flags also require explicit approval; allowing a flag acknowledges a
research limitation and does not repair the data. Unknown/unapproved flags block.
This policy is separate from the existing Phase 3 strategy quality policy.

Up is eligible only for return_5 < 0, Down only for return_5 > 0. Otherwise the
result is null/context_mismatch. Scores are independent and uncalibrated, never
complemented or combined. Result records artifact/policy fingerprints and flags,
with operational_eligible=false and executable=false. No trade threshold exists.
The low-level score method is a numerical primitive; signal-layer callers must
use the research gate and separately verify snapshot, cutoff and feature provenance.

## Next boundary

The signal layer can now be designed against this interface. It still needs a
reviewed artifact/provenance resolver and public signal service integration,
snapshot/cutoff validation and explicit research-quality policy. This PR does
not route HGB through the old linear loader, register a model or enable Phase 3
execution. No real estimator prediction, OOS reevaluation or refitting is needed
to test the interface: tests use explicit estimator doubles and synthetic vectors.
