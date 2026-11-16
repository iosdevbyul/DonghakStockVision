# Historical research expansion and robust discovery

This extends the existing autonomous runner, feature builder, three estimators,
AnalysisStore, Experiment Memory and provider interfaces. It adds historical
research capability, not a new collector, trainer, registry or scheduler. Old
v1–v5 plans, source PINs, models, Forward journals and operational settings retain
their contracts. Liblinear remains quarantined for new multi-stock studies.

## Source and time roles

`autonomous_historical_source_v1` creates dataset v4 / plan v6 in separate private
roots. Its `historical` object declares two source windows: exactly the original
training interval and one interval strictly after the original sealed Final Test.
Original validation and Final Test payloads are never read. They are not reassigned
as training when the source end date advances.

The read-only `_VerifiedMarket(source_window=...)` owner performs original BLOB,
source identity and receipt verification where the existing archive has receipts.
Legacy archives remain legacy archives: no missing receipt or source-publication
metadata is synthesized. Two sequential windows retain the existing aggregate
20,000-row / 25-symbol / 512MiB original-byte / 4,096-response bounds. Database
pins are checked before and after the read transactions. This is not an atomic
snapshot across separate transactions; unchanged quiescent hashes and frozen row
pins establish the local consistency boundary, not external historical authority.

Features reset at each source window. Explicit unavailable boundaries and existing
segment/discontinuity checks prevent labels or rolling history bridging the
unread intervals. Missing prices, sessions and labels are never filled. Metadata
inventory surveys complete dates/counts and per-period symbol coverage, while
payload eligibility and liquidity sampling remain in the original training role.
Historical constituents, suspension/delisting causes, sectors, capitalization,
corporate actions and independently authenticated publication times stay UNKNOWN
unless separately evidenced. Retrospective source retention is not historical PIT.

An optional `historical.source_parent={root,pin}` reuses a previously verified v6
original snapshot. Its original request, source windows, database pin, row digest,
source proof and separately retained parent snapshot must match. Reuse is only one
level deep. The original snapshot/source identities remain retained and explicitly
referenced by a new reused-source version. It does not rewrite the parent or invent
an original response. Only untrained preparation parents with identical search/
diagnostic roles and versioned feature contracts can supply frozen samples. Their
dataset PIN is retained, preventing repeated raw replay or feature calculation
for a pre-training readiness revision.

The additional request scope is explicit, for example:

```json
{
  "windows": [
    {"start": "2022-01-03", "end": "2023-12-28"},
    {"start": "2025-07-01", "end": "2026-10-01"}
  ],
  "search_end": "2026-06-30",
  "evaluations": [
    {"start": "2025-09-01", "end": "2026-03-31"},
    {"start": "2026-04-01", "end": "2026-06-30"}
  ],
  "diagnostic": {"start": "2026-07-01", "end": "2026-09-30"},
  "policy_version": "historical_robust_lexicographic_v1",
  "include_original_internal_folds": true,
  "references": []
}
```

These dates are observed archive bounds and research roles, not a claim of
independently authenticated exchange-session completeness or publication time.

## Chronological and stock generalization

Each goal keeps the existing delayed-direction event, 46 features, seven masks and
quarantined 462-configuration catalog. Optional original training-internal folds
are combined with explicitly declared later historical evaluation periods.
Training label ends must precede the evaluation window and remain inside the
training cutoff. Later declared folds add a horizon-sized observed-session
embargo; original internal folds retain their existing purging semantics.
The nominal continuous training span contains no original validation/Final Test
samples: only the two permitted source windows can contribute rows.

Heldout models exclude assigned symbols from both scaler and estimator fits.
Pooled controls remain explicit. Search scores and robust summaries use only the
same seen-symbol population. Cross-stock heldout scores never enter selector or
agent history. Features and stock-local trend/volatility/volume regimes use inputs
available at that sample's retained observation boundary, not its future returns.

A later diagnostic interval is excluded from every search/final checkpoint fit.
Its sidecars are not exposed by the standard report until the arm's budget locks,
and neither selector nor agent view receives them. It is historical diagnostic
evidence, not a pristine independent/prospective Final Test. No pickle loading API
is introduced: the existing worker scores its final in-memory fitted estimator.

All metrics, per-symbol/period/regime distributions, calibration, baselines and
common-calendar nonoverlapping cohorts reuse existing evaluators. Correlated stock
rows are not independent time anchors. Sparse buckets and unknown chronology stay
visible; no horizon is selected by comparing its AP with another horizon's AP.

Universe sampling uses the complete original training role. It precedes all new
post-Final-Test evaluation dates, but is not an as-of-screened historical universe
for the earlier reused internal folds. Those legacy folds remain conditional,
retrospective tuning diagnostics, not clean historical PIT evidence. Reports must
separate old and newly added calendar anchors and retain this selection/survivorship
limitation. No future-period performance is used to resample the universe.

## Fixed selection and transfer policy

The policy `historical_robust_lexicographic_v1` is frozen in the immutable plan.
It compares, in order: sufficient internal folds, folds meeting the predeclared
baseline/calibration/BA/overfit screen, worst BA, worst baseline AP gain, negative
BA dispersion, worst adequately populated regime BA and negative overfit gap.
These are ordered criteria, not an arbitrary weighted sum or approval score.
Candidate-family exploration, prior hypotheses and lower complexity break ties.
Observed CPU/memory costs are reported separately and constrained by existing
worker guards; the selector does not claim to optimize measured execution time.

Adaptive and predeclared controls use the existing v5 selection semantics. Every
arm shares data, candidate/fold identities and the finite budget. Each completed,
failed or cache-adopted selection consumes budget. Exact matching peer adoption
may strip only policy assignment/parent from comparison; different datasets,
periods, stock assignments or runtime/code identities cannot share fit evidence.

Retained, locked v5 studies can supply bounded feature/model hypotheses for
revalidation. Their scores are never current-population evidence. References and
transfer counterfactuals are recorded; within-study history is restricted to the
same horizon and stock policy. Selection logs retain actual alternatives and
counterfactuals. Discovery curves use completion/adoption times and keep horizons
and stock policies separate. A cached trial is ordered at adoption, not its old
peer completion time. Policy success is not assumed from changed choices.

## Execution and retention

Create new request, study and independent PIN directories; do not overwrite an
existing source, policy or study. Keep the declared comparison protocol and any
readiness-only amendment before the first model fit. A failed readiness attempt
is retained. Do not relax minimum populations to obtain a training result.

```sh
dsv autonomous-research prepare --request "$NEW_REQUEST" --request-pin-file "$REQUEST_PIN" --root "$ADAPTIVE" --pins-root "$ADAPTIVE_PINS"
dsv autonomous-research fork-policy --source-root "$ADAPTIVE" --source-plan-pin-file "$ADAPTIVE_PINS/plan.pin" --root "$FIXED" --pins-root "$FIXED_PINS"
dsv autonomous-research fork-policy --source-root "$ADAPTIVE" --source-plan-pin-file "$ADAPTIVE_PINS/plan.pin" --selection-policy robust --root "$ROBUST" --pins-root "$ROBUST_PINS"
dsv autonomous-research run --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --steps 6
# A new process resumes the same immutable plan and finite budget:
dsv autonomous-research run --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --steps 6
dsv autonomous-research report --root "$ADAPTIVE" --plan-pin-file "$ADAPTIVE_PINS/plan.pin" --report "$NEW_REPORT"
```

The existing shared heavy-work lock, one-thread CPU workers, 1GiB RSS monitor,
300-second worker deadline, disk/power/thermal guards and resident-Ollama checks
apply. Do not stop someone else's inference to start a learner. Research is manual
and budget bounded; no 24/7 job, model promotion or scheduler is activated. Preserve
requests, source/snapshot/plan PINs, checkpoints, terminal memory, resource audits,
policy comparisons, exact-refit evidence and package/check artifacts separately.
All PIT, performance, Champion/operational registration and execution approvals
remain false. Existing scheduled runtime logs are audited separately from
immutable source/model/PIN and scheduler configuration files.
