# Offline baseline runner-tail contract

This layer verifies an execution-window **evidence contract**. It does not call
`BaselineBacktestRunner.run`, load an estimator, generate a signal, freeze inputs,
calculate performance, or publish API readiness. All success-path tests are synthetic.
The actual Corporate Action gate remains unverified and the actual publication's
`runnerTailIntegrated` remains false.

## Causal partition

The current independently reviewed calendar and locked contracts establish:

```text
11 research sessions of causal warmup: 2024-06-14 ... 2024-06-28
    -> signal anchors: 2024-07-01 ... 2025-06-30 inclusive
    -> no new signals
    -> execution-only tail:
       2025-07-01 (immediate entry candidate; holding session 1)
       2025-07-02
       2025-07-03
       2025-07-04
       2025-07-07 (holding session 5; final possible exit)
    -> settlement-only sessions: 2025-07-08, 2025-07-09
    -> runner-tail evidence verified, only if every upstream gate passes
    -> Run Input Freeze still required
    -> actual baseline backtest still not executed or authorized
```

The final June 30 signal could enter on July 1 and require all five observed holding
sessions. Settlement on July 9 is the second eligible settlement session after July 7,
not two calendar days. Settlement evidence does not add execution or signal sessions
and does not convert the existing ledger to broker settlement-cash accounting.

The implementation reuses `AnchorRange`, `derive_boundaries`, the existing feature
context requirement (11 preceding sessions), the locked five-session holding rule,
and existing bounded tax-date validation. It does not build a weekday-based exchange
calendar. Research exclusions are applied to research sessions only; they remain
actual exchange sessions for settlement. In particular, March 21, 2025 is not a holiday.

`RunnerTailWindow` is immutable and distinguishes warmup, signal, execution, and
settlement-only tuples. `permits_signal()` excludes every non-anchor date.
`entry_candidate(anchor)` returns one immediate next research session and rejects
warmup/tail/settlement dates as anchors. It does not approve an entry or retry after
an unusable bar. Security-level preflight and all existing quality checks remain
required. Verified lifecycle absence is not a synthetic bar or permission to trade.

## Explicit trust boundary

A self-consistent SHA is not an independent approval. `verify_runner_tail` requires
an independently reviewed `baseline_runner_tail_pins_v1` manifest and its separately
supplied expected fingerprint. It never derives replacement expected pins from an
unexpected artifact. The manifest has exactly:

```json
{
  "version": "baseline_runner_tail_pins_v1",
  "artifacts": {
    "approval": "<reviewed semantic SHA>",
    "universe": "<existing frozen universe SHA>",
    "calendar": "<reviewed semantic SHA>",
    "calendar_verification": "<reviewed semantic SHA>",
    "boundaries": "<reviewed semantic SHA>",
    "settlement": "<reviewed semantic SHA>",
    "lifecycle": "<reviewed semantic SHA>",
    "lifecycle_manifest": "<reviewed semantic SHA>",
    "lifecycle_reconciliation": "<reviewed semantic SHA>",
    "parent_gaps": "<reviewed semantic SHA>",
    "corporate_coverage": "<independently reviewed final coverage SHA>"
  },
  "parent_integrated_fingerprint": "<reviewed DART integration parent SHA>",
  "parent_mapping_fingerprint": "<reviewed DART mapping parent SHA>"
}
```

Placeholders are not valid hashes. Do not create real approval pins until their
underlying evidence has been reviewed. No real pin manifest is added by this PR.
Parent integration and mapping references are independently reviewed upstream pins,
not newly asserted historical identity. This verifier checks relevant parent links;
it does not rerun upstream evidence acquisition or authenticate source responses.

Inputs follow the existing formats:

| Input name | Existing contract / representation |
| --- | --- |
| `approval` | Unwrapped `baseline_run_approval_v1` |
| `universe` | Unwrapped frozen universe, `schema_version: 1`, exact 993 tickers |
| `calendar` | Wrapped `kind_cash_calendar_v1` |
| `calendar_verification` | Wrapped `official_calendar_comparison_v1` |
| `boundaries` | Wrapped existing unversioned `exact-boundaries` shape |
| `settlement` | Wrapped existing unversioned `settlement-calendar` shape |
| `lifecycle` | Wrapped `baseline_security_lifecycle_v1` integrated evidence |
| `lifecycle_manifest` | Wrapped `baseline_security_lifecycle_v1` manifest |
| `lifecycle_reconciliation` | Wrapped `security_lifecycle_reconciliation_v1` |
| `parent_gaps` | Wrapped `missing_session_review_v1` |
| `corporate_coverage` | Wrapped `corporate_action_final_coverage_v1` |

Every supplied wrapper's fingerprint is recomputed, then compared with its independent
pin. Unwrapped approval/universe contents are also hashed and compared. Unsupported
versions fail closed; the existing unversioned shapes are deliberate compatibility
adapters, not permission to accept arbitrary future formats. Unknown status/blocker
values, malformed inputs, tampering, or mismatched links raise `ContractError` before
an output artifact is created. Well-formed negative evidence yields explicit blockers.

The full original frozen universe document hash must match the approved
`universe_sha256`. No subset is selected from observed bars, current DART mappings,
lifecycle status, or event availability. Local paths and retrieval timestamps belong
outside `artifact` (for example in an outer `audit` object); they do not affect semantic
fingerprints. Dictionary insertion order and command-line artifact order do not matter.
Session arrays remain strictly ordered semantic inputs; arbitrary reordering is not
silently repaired.

## Evidence checks

- Re-derive boundaries using the reviewed calendar pin and compare with independently
  pinned exact boundaries. Missing dates cannot shift the five-session tail or move
  the warmup boundary. Missing/incomplete calendar coverage stays blocked.
- Require the existing calendar comparison's aggregate verification, zero structural
  row/order errors, source DB hash identity, complete required research date count,
  and matching start/end/exclusions. The verifier does not open SQLite or reread bars.
- Resolve security gaps only through the pinned lifecycle chain. Check calendar-gap,
  parent-gap, parent-integration, mapping, manifest, and reconciliation fingerprint
  links; exact gap inventories; reconciliation accounting; known reasons; and official
  source references. Only the existing unexplained-security-session condition may be
  resolved by lifecycle evidence. Other calendar failures cannot be waived.
- Compare the entire supplied settlement mapping to the existing calendar-derived
  mapping, including every possible entry/exit session and the final exit. Require
  VERIFIED status, matching rule source, matching calendar pins, and bounded existing
  cost-schedule support. No missing mapping becomes zero cost or calendar-day arithmetic.
- Require independently reviewed final Corporate Action coverage with the exact
  universe, 993 verified security statuses and coverage records, full required period,
  full historical identity count, no unresolved blockers, and a true final gate.
  [Document-level semantic review](corporate-action-semantic-review.md) is not an
  acceptable substitute. The [final coverage contract](corporate-action-final-coverage.md)
  is unchanged; no coverage or historical identity is created by this layer.
- Compare current baseline/quality/cost/runner identities to approved pins. Unexpected
  locked fingerprint changes stop validation; pins are not automatically updated.

Output version is `baseline_runner_tail_v1`, containing exact partitions, input pins,
universe and policy identities, and blockers. `runner_tail_integrated` may be true only
when all these checks pass. Every result, including synthetic success, contains:

```json
{
  "real_backtest_invoked": false,
  "model_fit_invoked": false,
  "prediction_invoked": false,
  "performance_calculated": false,
  "prices_adjusted": false,
  "run_input_frozen": false,
  "backtest_authorized": false
}
```

## Offline verifier

Supply every artifact explicitly; there is no mutable workspace discovery:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.verify_runner_tail \
  --pins /reviewed/runner-tail-pins.json \
  --expected-pins-fingerprint APPROVED_PIN_MANIFEST_SHA256 \
  --artifact approval=/reviewed/approval.json \
  --artifact universe=/reviewed/universe.json \
  --artifact calendar=/reviewed/official-calendar.json \
  --artifact calendar_verification=/reviewed/calendar-verification.json \
  --artifact boundaries=/reviewed/exact-boundaries.json \
  --artifact settlement=/reviewed/settlement-calendar.json \
  --artifact lifecycle=/reviewed/integrated-security-evidence.json \
  --artifact lifecycle_manifest=/reviewed/security-lifecycle-manifest.json \
  --artifact lifecycle_reconciliation=/reviewed/missing-session-reconciliation.json \
  --artifact parent_gaps=/reviewed/missing-session-explanations.json \
  --artifact corporate_coverage=/reviewed/corporate-action-final-coverage.json \
  --output /tmp/runner-tail.json
```

These paths are illustrative, not a claim that final real coverage/pins exist. The
output parent must exist and the output file must be new. Exclusive creation prevents
overwrite. The command writes canonical `{artifact, fingerprint}` JSON and prints only
aggregate status, counts, bounds, and fingerprint. A successful command means a report
was written; always inspect `runner_tail_integrated` and `blockers` for evidence status.
No `.env`, credentials, network, model loading, or database initialization is used.

## Integration limits and unchanged readiness

The real repository's final Corporate Action gate has not passed. Whole-period
historical identity and authoritative no-event semantics remain independent upstream
requirements. No checked-in evidence or publication file is updated here.

This is an evidence/window integration contract, not runtime dispatch wiring.
The existing runner still uses one shared contract end and allocates on each processed
in-range session. **Do not extend that end to July 7 and call the existing runner**:
that would also permit unwanted tail allocations. A later explicitly authorized run
adapter must consume the verified partitions and enforce the signal cutoff before
runtime execution. This PR changes neither the runner nor its economic behavior.
Run Input Freeze still needs separate model/input/quality validation and publication
approval. A fixture's true `runner_tail_integrated` never promotes the real API's
`runnerTailIntegrated` field.

Unchanged locked identities:

- Baseline: `89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8`
- Quality: `c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c`
- Cost: `c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef`
- Runner: `baseline_runner_v1`
