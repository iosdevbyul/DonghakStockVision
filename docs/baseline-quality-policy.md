# Baseline research-quality approval

This is a static contract review, not an analysis of real data or trading results.
No historical run, flag-frequency analysis, OOS prediction, fitting or collection
was performed to choose this policy. All tests use synthetic/in-memory inputs.

## Scope and inventory

Quality metadata is **open string input**, not one repository-wide enum. Snapshot
capture emits seven named flags. HGB and linear paths independently expose statuses;
entry/feature guards raise named errors. The inventory below explicitly classifies
the known snapshot flags, signal states, relevant legacy quality states and the
complete named guard diagnostics from snapshot, feature, HGB integration, signal
domain, inference and trusted-artifact resolution. Guards are listed for auditability;
listing an error here never replaces its originating validator.

The five approved snapshot flags are the **only** values accepted as flags. A
status such as `scored` or `verified` cannot be inserted into `quality_flags` to gain
approval. ALLOWED means eligible for further validation, never an order/fill guarantee.
Any future or externally supplied string absent from the inventory raises
`ContractError("unknown_quality_state:<value>")`. No silent acceptance or fallback.

| Exact flag/state/guard | Originating layer | Classification | Existing meaning and reason |
|---|---|---|---|
| `adjustment_mismatch` | `data/snapshot.py` | BLOCKED | Existing validation guard: adjustment mismatch. |
| `artifact_provenance_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: artifact provenance mismatch. |
| `calendar_unverified` | `snapshot/signal/entry/legacy quality` | BLOCKED | Missing verified coverage cannot establish complete session ordering. |
| `checkpoint_mismatch` | `models/artifact_resolver.py,models/inference.py` | BLOCKED | Existing validation guard: checkpoint mismatch. |
| `conditional_signal_conflict` | `signals/domain.py` | BLOCKED | Existing validation guard: conditional signal conflict. |
| `configuration_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: configuration mismatch. |
| `constant_volatility` | `features/engine.py` | BLOCKED | Existing validation guard: constant volatility. |
| `context_mismatch` | `snapshot/signal/entry/legacy quality` | BLOCKED | Requested direction is outside its conditional population; null is not a score. |
| `contract_conflict` | `snapshot/signal/entry/legacy quality` | BLOCKED | Stored analysis conflicts with its contract. |
| `corporate_actions_unverified` | `snapshot/signal/entry/legacy quality` | BLOCKED | Coverage does not establish corporate-action quality; unsupported uncertainty. |
| `empty_signal_prefix` | `baseline_inputs` | BLOCKED | No published prefix is available. |
| `excluded_corporate_action` | `features/engine.py` | BLOCKED | Existing validation guard: excluded corporate action. |
| `experiment_configuration_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: experiment configuration mismatch. |
| `experiment_link_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: experiment link mismatch. |
| `feature_contract_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: feature contract mismatch. |
| `feature_label_contract_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: feature label contract mismatch. |
| `future_signal_input` | `signals/domain.py` | BLOCKED | Existing validation guard: future signal input. |
| `future_signal_prefix` | `baseline_inputs` | BLOCKED | Prefix contains data beyond the decision cutoff. |
| `gap_through_barrier` | `baseline_runner` | BLOCKED | No approved execution-price rule for a gap through barriers. |
| `incompatible_artifact` | `models/inference.py` | BLOCKED | Existing validation guard: incompatible artifact. |
| `incompatible_parameters` | `models/inference.py` | BLOCKED | Existing validation guard: incompatible parameters. |
| `incompatible_research_score` | `signals/domain.py` | BLOCKED | Existing validation guard: incompatible research score. |
| `incomplete_equity` | `baseline_runner` | BLOCKED | Required causal position valuation is incomplete. |
| `inference_failed` | `models/inference.py` | BLOCKED | Existing validation guard: inference failed. |
| `insufficient_history` | `features/engine.py` | BLOCKED | Existing validation guard: insufficient history. |
| `insufficient_model_data` | `snapshot/signal/entry/legacy quality` | BLOCKED | No usable directional model. |
| `insufficient_point_in_time_data` | `features/engine.py` | BLOCKED | Existing validation guard: insufficient point in time data. |
| `insufficient_remaining_sessions` | `baseline_runner` | BLOCKED | No sufficient remaining observations for a resolved exit. |
| `invalid` | `snapshot/signal/entry/legacy quality` | BLOCKED | Quality evidence is invalid. |
| `invalid_artifact_path` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: invalid artifact path. |
| `invalid_artifact_pin` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: invalid artifact pin. |
| `invalid_checkpoint` | `models/inference.py` | BLOCKED | Existing validation guard: invalid checkpoint. |
| `invalid_features` | `models/inference.py` | BLOCKED | Existing validation guard: invalid features. |
| `invalid_market_order_or_duplicates` | `data/snapshot.py,features/engine.py` | BLOCKED | Existing validation guard: invalid market order or duplicates. |
| `invalid_market_range` | `data/snapshot.py` | BLOCKED | Existing validation guard: invalid market range. |
| `invalid_next_bar` | `snapshot/signal/entry/legacy quality` | BLOCKED | Schema validation failed. |
| `invalid_next_open` | `snapshot/signal/entry/legacy quality` | BLOCKED | Open is not a usable positive price. |
| `invalid_population` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid population. |
| `invalid_provenance` | `models/inference.py` | BLOCKED | Existing validation guard: invalid provenance. |
| `invalid_quality_flags` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid quality flags. |
| `invalid_quality_manifest` | `data/snapshot.py` | BLOCKED | Existing validation guard: invalid quality manifest. |
| `invalid_quality_result` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid quality result. |
| `invalid_research_policy` | `models/inference.py` | BLOCKED | Existing validation guard: invalid research policy. |
| `invalid_research_signal_request` | `signals/integration.py` | BLOCKED | Existing validation guard: invalid research signal request. |
| `invalid_runtime` | `models/inference.py` | BLOCKED | Existing validation guard: invalid runtime. |
| `invalid_signal_enum` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal enum. |
| `invalid_signal_fingerprint` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal fingerprint. |
| `invalid_signal_null` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal null. |
| `invalid_signal_policy` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal policy. |
| `invalid_signal_score` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal score. |
| `invalid_signal_status` | `signals/domain.py` | BLOCKED | Existing validation guard: invalid signal status. |
| `invalid_snapshot_range` | `data/snapshot.py` | BLOCKED | Existing validation guard: invalid snapshot range. |
| `known_incomplete_trading_date` | `features/engine.py,signals/integration.py` | BLOCKED | Existing validation guard: known incomplete trading date. |
| `locked_model_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: locked model mismatch. |
| `malformed_artifact` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: malformed artifact. |
| `malformed_provenance` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: malformed provenance. |
| `missing_anchor_bar` | `signals/integration.py` | BLOCKED | Existing validation guard: missing anchor bar. |
| `missing_artifact` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: missing artifact. |
| `missing_artifact_root` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: missing artifact root. |
| `missing_bar` | `snapshot/signal/entry/legacy quality` | BLOCKED | Required bar is absent. |
| `missing_portfolio_session` | `baseline_runner` | BLOCKED | A held ticker is absent from an observed market session. |
| `missing_research_score` | `signals/domain.py` | BLOCKED | Existing validation guard: missing research score. |
| `missing_signal_bars` | `baseline_inputs` | BLOCKED | No bars are available to construct a signal. |
| `missing_signal_identity` | `signals/domain.py` | BLOCKED | Existing validation guard: missing signal identity. |
| `missing_verified_session` | `features/engine.py` | BLOCKED | Existing validation guard: missing verified session. |
| `missing_warmup_session` | `baseline_inputs` | BLOCKED | Required causal warmup has a missing bar. |
| `mixed_data_origin_or_price_basis` | `data/snapshot.py` | BLOCKED | Existing validation guard: mixed data origin or price basis. |
| `mixed_series_metadata` | `data/snapshot.py` | BLOCKED | Existing validation guard: mixed series metadata. |
| `next_bar_unavailable` | `snapshot/signal/entry/legacy quality` | BLOCKED | Immediate next bar is absent. |
| `no_trade_bar` | `features/engine.py` | BLOCKED | Existing validation guard: no trade bar. |
| `nonfinite_features` | `features/engine.py` | BLOCKED | Existing validation guard: nonfinite features. |
| `not_applicable` | `snapshot/signal/entry/legacy quality` | BLOCKED | Direction is outside its applicable population. |
| `observed_bars_unverified` | `snapshot/signal/entry/legacy quality` | BLOCKED | Calendar completeness is not verified; synthetic legacy fixtures are not real evidence. |
| `ok` | `signal/snapshot/legacy quality` | ALLOWED | Legacy input validation succeeded; not a trade signal. |
| `price_basis_mismatch` | `snapshot/signal/entry/legacy quality` | BLOCKED | Model and market price bases differ. |
| `price_jump` | `snapshot/signal/entry/legacy quality` | BLOCKED | Phase 1 warning may indicate a corporate action; no automatic waiver. |
| `provenance_hash_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: provenance hash mismatch. |
| `quality_blocked` | `snapshot/signal/entry/legacy quality` | BLOCKED | Upstream approval failed; never override it. |
| `quality_coverage_missing` | `snapshot/signal/entry/legacy quality` | BLOCKED | Verified coverage does not span required bars. |
| `quality_not_allowed` | `models/inference.py` | BLOCKED | Existing validation guard: quality not allowed. |
| `quality_not_available_at_snapshot` | `data/snapshot.py` | BLOCKED | Existing validation guard: quality not available at snapshot. |
| `quality_review_may_be_retrospective` | `snapshot` | ALLOWED | Review timing disclaimer; verified coverage and segment checks still apply. |
| `quality_unverified` | `data/snapshot.py` | BLOCKED | Existing validation guard: quality unverified. |
| `research_artifact_required` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: research artifact required. |
| `research_calendar_mismatch` | `data/snapshot.py,signals/integration.py` | BLOCKED | Existing validation guard: research calendar mismatch. |
| `research_calendar_requires_historical_research` | `data/snapshot.py` | BLOCKED | Existing validation guard: research calendar requires historical research. |
| `research_limitations_acknowledgement_required` | `data/snapshot.py,signals/integration.py` | BLOCKED | Existing validation guard: research limitations acknowledgement required. |
| `research_only` | `models/inference.py` | BLOCKED | Existing validation guard: research only. |
| `research_sessions_excluded_not_holidays` | `snapshot` | ALLOWED | Explicit calendar exclusions are recorded, not converted into holidays. |
| `research_signal_only` | `signals/domain.py` | BLOCKED | Existing validation guard: research signal only. |
| `retrospective_revisions_possible` | `snapshot` | ALLOWED | Historical snapshot limitation; not evidence of a detected defective segment. |
| `revision_history_unavailable` | `snapshot` | ALLOWED | Latest-only provenance; no PIT claim is permitted. |
| `runtime_mismatch` | `models/inference.py` | BLOCKED | Existing validation guard: runtime mismatch. |
| `runtime_provenance_mismatch` | `models/artifact_resolver.py` | BLOCKED | Existing validation guard: runtime provenance mismatch. |
| `scored` | `signal/snapshot/legacy quality` | ALLOWED | Usable raw score; strategy eligibility still required. |
| `scores_on_blocked_quality` | `signals/domain.py` | BLOCKED | Existing validation guard: scores on blocked quality. |
| `signal_direction_mismatch` | `signals/domain.py` | BLOCKED | Existing validation guard: signal direction mismatch. |
| `signal_quality_blocked` | `snapshot/signal/entry/legacy quality` | BLOCKED | Upstream signal rejected quality. |
| `stale_data` | `snapshot/signal/entry/legacy quality` | BLOCKED | Observation is outside the accepted freshness window. |
| `suspected_discontinuity` | `features/engine.py` | BLOCKED | Existing validation guard: suspected discontinuity. |
| `suspected_gap` | `features/engine.py` | BLOCKED | Existing validation guard: suspected gap. |
| `synthetic_mode_required` | `data/snapshot.py` | BLOCKED | Existing validation guard: synthetic mode required. |
| `synthetic_mode_requires_fake_provider` | `data/snapshot.py` | BLOCKED | Existing validation guard: synthetic mode requires fake provider. |
| `unavailable` | `snapshot/signal/entry/legacy quality` | BLOCKED | Required usable directional output is absent. |
| `unclassified` | `signal/snapshot/legacy quality` | ALLOWED | No strength bin or trading implication. |
| `universe_incomplete` | `snapshot` | ALLOWED | Incomplete universe limits interpretation, not individual bar validity. |
| `unknown` | `snapshot/signal/entry/legacy quality` | BLOCKED | Unknown provenance/quality is not approval. |
| `unsupported_or_mixed_price_basis` | `snapshot/signal/entry/legacy quality` | BLOCKED | Price basis is unsupported or inconsistent. |
| `untrusted_artifact` | `models/artifact_resolver.py,models/inference.py` | BLOCKED | Existing validation guard: untrusted artifact. |
| `unverified` | `snapshot/signal/entry/legacy quality` | BLOCKED | Quality evidence is not verified. |
| `unverified_adjustment` | `data/snapshot.py` | BLOCKED | Existing validation guard: unverified adjustment. |
| `verified` | `signal/snapshot/legacy quality` | ALLOWED | Verified quality state; evidence still required. |
| `verified_sessions` | `signal/snapshot/legacy quality` | ALLOWED | Verified calendar basis; coverage and segment checks remain mandatory. |

### Schema errors and non-quality fields

`DailyBar.__post_init__()` separately rejects invalid ticker/date/timezone/market,
noninteger or negative OHLCV/value, nonpositive close, inconsistent OHLC relationships,
and inconsistent zero volume/value. Its human-readable `ValueError` messages are
not quality states and cannot be allowlisted. Entry converts malformed bars to
`invalid_next_bar`; absent bars are `missing_bar` / `next_bar_unavailable`. No-trade
reference-close records can be stored by Phase 1 but remain `no_trade_bar` for research.
`price_jump` is a Phase 1 warning, not proof of an acceptable corporate action.

Population values `prior_decline`, `prior_rise`, `flat` are conditional-model
contexts, not quality permissions. Only scored Up in prior_decline can progress.
Down's normal `context_mismatch` remains null and does not veto valid Up. Up
`context_mismatch`, `not_applicable`, `unavailable` and upstream `quality_blocked`
never progress. `synthetic_test_only`, `research_only`, `pit_review_required`,
registration/evaluation status and adjustment enums are separate provenance or
usage contracts, not approvals. Existing checks remain authoritative.

## Static classification rationale

- `universe_incomplete`: acknowledges inability to reconstruct the complete historic
  investable universe. It does not identify a malformed individual bar. No
  survivorship-free population or market-wide performance claim is authorized.
- `revision_history_unavailable` and `retrospective_revisions_possible`: latest-only
  historical provenance, never proof of point-in-time availability. Permitted only
  within the existing historical research boundary, not as a PIT waiver.
- `research_sessions_excluded_not_holidays`: explicit excluded-session provenance;
  does not reclassify gaps or incomplete sessions as real holidays.
- `quality_review_may_be_retrospective`: review-time disclaimer, not a detected defect.
  Coverage, adjustment, exclusions and segment checks must still pass.
- `corporate_actions_unverified`: capture emits this without complete quality
  coverage. Unadjusted-price action uncertainty is **BLOCKED_ON_QUALITY_POLICY**;
  no inferred split or correction is permitted.
- `calendar_unverified` / `observed_bars_unverified`: no reliable complete calendar
  evidence. Real runner inputs remain blocked rather than interpreting absent
  sessions as holidays. Existing synthetic legacy fixtures can exercise this basis;
  this exception provides no approval for real inputs or flagged defective bars.

## Contract and integration

`BaselineQualityPolicy` is immutable, versioned, complete and canonical. Its identity
includes every classification, the exact flag allowlist and unknown-state behavior.
Only stricter reclassification is constructible; known failures cannot be waived.
A stricter variant has a different fingerprint and needs a corresponding trusted pin.

`BaselineBacktestPolicy` version 3 embeds the quality contract. Its default allowlist
is now the explicit five-flag approval, not an empty implicit list. Existing callers
can still pass a stricter tuple (including an explicitly empty tuple); unknown or
blocked flags cannot be added. The compiled contract quality-version field binds
the full quality-policy fingerprint. Economic rules are byte-equivalent after
removing only strategy_version, checked against a frozen digest from main c4045d5.

Use `quality.signal_policy()` to explicitly construct the existing `SignalPolicy`
and `ResearchPolicy` for trusted HGB integration. This does not mutate an existing
source or automatically rescore upstream rejected signals. The underlying snapshot,
feature, artifact, inference, entry and corporate-action validators are unchanged.

The runner validates input flag vocabulary before processing. It rechecks session
quality for entries/exits/allocation and checks signal approval before chronology
and strategy eligibility. Known blocks produce no intent with explicit quality
rejection evidence. Unknown flags raise a contract error (not swallowed by the
AnalysisError handler). The initial immutable ledger remains unchanged on failure.

## Locked fingerprints

For the approved five-flag policy with the existing approved cost assumptions:

- Quality version: `baseline_research_quality_v1`
- Quality fingerprint: `c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c`
- Baseline version: `long_only_up_reversal_baseline_v3`
- Baseline fingerprint: `89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8`
- Cost fingerprint (unchanged): `c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef`

The runner already requires a caller-supplied expected fingerprint; it has no
embedded global pin to silently overwrite. Supply the above v3 pin for this exact
configuration. Tests validate that pin and reject the old v2 pin. Stricter allowlists
produce different identities. Never derive a trusted pin from an unexpected input
policy during execution. Existing model artifacts and inference identities are
unchanged; new signal-policy and baseline/result identities intentionally differ.

## Remaining historical-run blockers

**BLOCKED_ON_QUALITY_POLICY** until required corporate-action/calendar coverage and
session evidence are available through the existing quality-manifest contract.
This work did not inspect real inputs and does not claim that evidence exists.
Do not fabricate or retroactively approve it based on trade counts or performance.

A subsequent input freeze also needs independently verified model/input identities,
approved universe/dates, supplied settlement evidence within cost-schedule coverage,
and compatible synchronous publication times. Missing bars, suspected gaps,
discontinuities, unresolved exits and all previously documented runner limitations
remain fail-closed. No actual historical run or input freeze occurred here.
