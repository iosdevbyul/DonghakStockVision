# HGB historical trading integration

This connects existing trusted HGB research inference to the existing Phase 3
Decision Engine and Phase 4 single-ticker Runner. It adds no thresholds, trading
rules, execution pricing, ledger calculations, or qualification approvals.

## Reviewed contract differences

| Boundary | Linear | HGB |
| --- | --- | --- |
| Model identity | AnalysisStore model content ID | independently pinned Up and Down HGBArtifact fingerprints |
| Checkpoint | linear parameter JSON | trusted local pickle, SHA verified before loading |
| Training/input snapshot | same snapshot required in research | separate training snapshot reference and bounded inference snapshot |
| Dataset | persisted training dataset and split | public input dataset ID derived from inference snapshot + feature version |
| Provenance | model/dataset/snapshot checksums | experiment lock, qualification, plan, checkpoint, feature/label/runtime/calendar identities |
| Time | stored model creation timestamp | no model creation timestamp in public evidence; remains null |
| Explanation | linear feature/logit contributions | factual features and raw conditional scores; attribution unavailable |

The public `IntegratedResearchSignal` now additionally preserves the original
verified experiment records returned by `TrustedHGBResolver`. These records are
captured before deserialization and are part of the public evidence fingerprint.
Runtime compatibility and checkpoint verification still run in the unchanged
trusted loading path; replay of frozen evidence does not unpickle or retrain.
Hashes verify integrity relative to trusted configuration, not authenticity of an
arbitrary caller's pickle or policy. Do not obtain pins from untrusted requests.

## Adapter and immutable evidence

`signals.hgb_trading.adapt(IntegratedResearchSignal)` produces the explicit
`hgb_historical_analysis_v1` projection. `projection(public_evidence)` verifies
candidate identities/statuses, original direction manifests, experiment hashes
and links, input snapshot checksum, exact eight-feature values/hash, input window,
conditional context, quality, origin, cutoff and restrictions before reproducing
it. Neither scores nor model artifacts are converted into linear parameters.

`model_version` is explicitly a **model-pair reference**: canonical digest of
`{"up": original_up_fingerprint, "down": original_down_fingerprint}`. The original
identities remain in `hgb_provenance.model_pair` and `models`. No training dataset
is invented. Training snapshot references remain separate from the inference
snapshot and the public input dataset ID.

FrozenAnalysis accepts this additional contract with the original linear branch
unchanged. It checks declared manifest references, release identity, provenance,
projection equality and hashes. Reviewed input session basis must also match
the experiment lock, preserving the locked label semantics. Runner passes the original HGB evidence into
DecisionInput. The existing engine revalidates the explicit projection before
using its unchanged context, quality, risk, threshold and sizing rules. The
existing DecisionBundle, admission, reservation revision checks, later-event
execution, Ledger, verified Position History and Performance remain authoritative.

## Programmatic historical provider

```python
provider = HGBHistoricalProvider(trusted_resolver, explicit_signal_policy, observed_at)
request = HistoricalBacktestRequest(
    start, end, initial_cash, (ticker,), provider.model_id, explicit_config
)
result = run_historical_backtest(
    request, read_only_market, None,
    run_output=separate_analysis_store, hgb_provider=provider,
)
```

The explicit config is the existing historical-service config, including a
**bounded list of decision_dates**, DecisionPolicy allowed_models containing the
pair ID, reviewed quality, calendar and all existing execution/cost policies.
No HGB CLI discovery, default pins or automatic model selection is introduced.
The provider calls genuine HGBResearchSignalService.research at requested dates
only. Missing anchor, insufficient features, checkpoint/runtime/calendar errors
remain blocked decision points. Reviewed quality is additionally required for
trading: public research without a quality manifest becomes `quality_unverified`;
policy-denied flags remain `quality_blocked` with null scores.

The existing historical builder freezes the selected series once; HGB inference
retains at most 12 bars per opportunity. Historical snapshot validation compares
that actual inference window to the frozen market records, rather than requiring
HGB training and inference snapshots to be identical. Full model checkpoints and
training datasets are never copied into timeline rows.

Generated evidence goes only to the explicit run output, as immutable
`hgb_analysis` artifacts. Market/source AnalysisStore same-file destinations are
rejected before signal generation. Borrowed stores remain caller-owned. Use
read-only source readers; no source schema or audit writes are required.

## Clocks and reporting

`as_of`/decision cutoff is next Seoul midnight after the selected complete bar.
`source_cutoff` preserves the explicit retrospective acquisition cutoff. Original
bar collected_at and quality verified_at remain intact. Provider `observed_at`
is a required, caller-recorded **actual evidence observation** timestamp, at or
after source_cutoff. It is stored separately in created_at.signal. It is not a
claim that the model or data existed at the historical decision time. Model
created_at stays null. Explicit retrospective release assumptions permit research
only; the adapter refuses PIT manifests.

`historical_trading_report_v1` gains additive HGB timeline fields:
`hgb_provenance`, `feature_values`, and actual `evidence_observed_at`. Original context, scores, configured
thresholds, selected rule, stage outcomes, fills, positions, and existing P&L stay
in their existing fields. `feature_contributions` and `linear_intercept` are null;
`hgb_attribution_not_provided` explains missing contributions. Text output includes
both checkpoint/lock/qualification/training snapshot references and feature values.
Linear report fields and contribution rendering remain unchanged.

Canonical JSON hashes include frozen evidence and source references, not rendered
text or a new wall clock. Replay the **same** frozen request and exact start/end
values for identical report/run/performance hashes. Fresh capture observation
receipts may legitimately differ. Replay verifies frozen calculations/lineage;
it does not independently certify original model scores or historical revisions.

## Validation and limits

Positive tests fit actual HistGradientBoostingClassifier estimators with fixed
seed/configuration on a separate synthetic matrix, verify them through the real
resolver and public inference loader, and run actual OHLCV feature preparation.
No score, estimator, or public service is monkeypatched. The fixed mechanics
policy uses zero thresholds, as in the existing linear mechanics fixture, without
looking at subsequent returns. Observed context-driven sequence is
BUY → HOLD → SELL → BUY → HOLD, with three later fills, one completed episode and
one terminal open episode. A separate threshold-one fixture exercises WAIT.
This demonstrates plumbing, not investment quality or a recommended policy.
The historical-service fixture retains its caller-fixed reservation ceilings:
BUY → HOLD → SELL → BUY → BUY, two fills and two explicit
`admission_budget_exceeded` reservation rejections. Its second entry is not forced
into a fill. The existing service does not dynamically resize caller reservations
for later entry estimates; callers must supply suitable reviewed policies.

A temporary `fixture_daily` provider exercises the real-provider historical
routing contract; those rows are still invented offline fixtures, not real market
performance evidence. Tests also cover missing/tampered artifacts, model/feature/
calendar/runtime incompatibility, source cutoff, quality denial/missing review,
missing/tampered frozen evidence, separate output, and deterministic report replay.
Existing linear, engine, Runner/Ledger, history, Performance and API tests remain
required regressions.

No existing local HGB checkpoint is requalified, retrained, or replayed against
real data by this change. Protected local files are fingerprinted read-only.
Synthetic mechanics, historical research, OOS evaluation, strict PIT qualification
and operational eligibility are distinct. Corporate Actions stays 0/993/blocked;
Runner Tail, Run Input Freeze and strict PIT remain blocked. Research-only and
unregistered restrictions remain in effect. Security Metadata, Research API
names, Operations, iOS and Web are unchanged. No authenticated KRX/DART call,
live acquisition, or brokerage route is used.

Next recommended PR: reviewed trusted-HGB run configuration and availability
receipt preflight for one bounded real historical research replay, with explicit
blocked outcomes and independent input evidence. No portfolio expansion yet.
