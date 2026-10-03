# Trusted historical HGB signal integration

This is an additive, in-memory application service. Existing linear SignalService,
model loaders, CLI and storage schemas are unchanged. No fitting, calibration,
model registration, trading threshold, execution or backtesting is implemented.

## Artifact resolver

`TrustedHGBResolver(root, up_manifest, down_manifest, up_pin=..., down_pin=...)`
requires the existing `HGBArtifact` inference envelopes and independently pinned
envelope fingerprints from trusted local configuration. The root contains exactly
the following named inputs used by this resolver:

- `lock.json`: exported experiment lock with `lock_id`
- `qualification.json`: exported qualification with `report_id`
- `plan.json`: original experiment plan, linked by lock.plan_id
- `up-research-only.pkl`, `down-research-only.pkl`

No arbitrary checkpoint path, automatic discovery or network download is accepted.
Symlinked artifact files and paths outside the configured root are rejected.
Missing/malformed files, mismatched hashes, direction, eight-feature order/version,
label contract, candidate, qualification, source training snapshot, calendar,
configuration and runtime provenance fail closed.

Content IDs are recomputed using the existing canonical digest (excluding exported
lock_id/report_id fields). Both selected candidates must match the approved HGB-7
Up / HGB-15 Down configuration and qualification must say validation_gate_passed.
The checkpoint bytes must match both manifest and persisted experiment lock SHA.
The complete parameter JSON is subsequently checked against the actual estimator
by the unchanged HGBInference loader, along with runtime and class/feature checks.

`verify()` returns verified immutable byte copies without unpickling or prediction.
`load()` delegates to the existing inference loader. Pins/root must NOT be obtained
from an untrusted request alongside its artifact. SHA detects tampering relative
to the pin; it does not authenticate arbitrary pickle or sandbox deserialization.
The original model bytes are never converted or saved again.

## Snapshot and clocks

`ResearchSignalRequest` explicitly supplies ticker, timezone-aware as_of,
history_start, source_cutoff, research limitation acknowledgement, synthetic mode,
research calendar and optional canonical quality JSON.

- **as_of** is the research signal observation time.
- **anchor_date** is the previous Asia/Seoul calendar date. Complete daily OHLCV
  becomes available at next Seoul midnight. Same-day OHLCV is not used.
- **source_cutoff** is the explicitly selected acquisition snapshot cutoff, passed
  to the existing `capture` API. It may be later than as_of in historical research:
  this acknowledges retrospective collection/revision, NOT verified PIT data.
- Source receipt timestamps remain unchanged. `capture` excludes receipts after
  source_cutoff and enforces the existing price basis and origin checks.

`resolve_snapshot` reads only the explicitly bounded history_start..anchor_date
through MarketDataStore.read. Out-of-range/ticker/order violations by a repository
are rejected. It retains at most the last 12 bars: 11 feature bars plus the previous
edge needed by the existing quality check. It calls the unchanged feature engine.
Older rows may be read to locate that window because the public store has no
last-N query; they are not used for features. The supplied read bound is recorded.

Missing anchor, insufficient history, excluded research dates, no-trade rows,
gaps, discontinuities, adjustment and quality failures are not repaired or filled.
Weekend/holiday anchors fail missing_anchor_bar; there is no implicit fallback to
a previous session or invented calendar. Choose as_of on the day after the desired
observed session. Research calendar must exactly match the locked experiment.
A quality manifest may be retrospective and the existing flags preserve that fact.

## Orchestration

```python
service = HGBResearchSignalService(market_store, trusted_resolver)
result = service.research(request, explicit_signal_policy)
```

Flow: bounded snapshot → existing feature engine → quality policy → artifact
provenance verification → existing HGBInference.research → generate_signal.
Denied quality flags yield the existing quality_blocked/null candidate; artifact
hash/provenance is still checked, but no checkpoint is deserialized and no score
is calculated. Structural/feature failures raise AnalysisError (underlying store
failures also propagate), with no partial candidate or database writes.

Raw scores, conditional context_mismatch/null and strength states are unchanged.
No score is converted to BUY/SELL or compared with a new signal/trade threshold.
Real inputs remain research_only, synthetic fixtures synthetic_test_only;
operational_eligible/executable remain false.

## Provenance and replay

`IntegratedResearchSignal` preserves the existing candidate and signal fingerprint,
Up/Down inference envelopes (including direction, checkpoint SHA, lock and
qualification IDs, training snapshot, configuration and versions), frozen input
snapshot JSON, anchor_date and feature_version. Its fingerprint covers all of these.
The context includes dataset ID derived from snapshot/feature version, snapshot
ID, raw input hash, feature-content hash, policy hashes and UTC cutoffs.
Training snapshot and inference snapshot are separate references, never substituted.
Equal inputs/pins/policies produce equal identities; model/policy/input changes
change identity. No wall clock/random/network is used by the service. Snapshot
bytes are returned for caller preservation; durable persistence is not implemented.

## Remaining boundary

This integration is ready to support **backtest contract design**, not a claim
that trading execution is ready. Remaining work includes reviewed application
configuration/pin distribution, persistence/query integration, and separately
approved temporal availability/strategy/backtest contracts. Latest-only market
storage cannot reconstruct historical revisions or establish PIT/OOS execution
or liquidity evidence. Feature and label definitions are unchanged. Tests use
explicit estimator doubles and temporary SQLite, never production OOS predictions.
