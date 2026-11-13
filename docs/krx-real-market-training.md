# Versioned real KRX research training

`real-market-dataset` composes existing research and operational sources into the
existing immutable AnalysisStore snapshot/model format. It does not collect data,
change operating policies, register a model, or publish prospective predictions.
The legacy `train --real-market-source` v1 contract remains unchanged.

## Dataset version instead of repinning Phase 2

The original Phase 2 protocol pins a whole SQLite file. Adding history changes
that file fingerprint even when earlier observations remain usable. Preserve the
old protocol and its source PIN. A new request pins the current source files and
the exact old protocol file, records a distinct dataset version, and retains both
old and current source identities. It never substitutes a new PIN into the old
protocol. A source-byte pin is local integrity, not independent issuer authority.

The new owner preserves the protocol's Training and Validation ranges exactly.
It never selects, unseals, replays, computes features for, or scores the sealed
outer Final Test. Whole-container hashing necessarily reads SQLite bytes; it is
not a query of protected observations or outcomes.

The optional new diagnostic test must start **after** the original Final Test.
It is an explicitly retrospective development diagnostic, not a new locked Final
Test, unbiased OOS evaluation, prospective evidence, or permission to change old
roles. TrainingService selects C using the unchanged Validation AP rule, fits
only Training, then scores frozen parameters on the diagnostic test. Reusing the
known Validation period limits interpretation; no performance approval follows.
No Minimums, model codec, estimator, feature, label, or qualification rule changes.

## Original sources and consistency

- `_VerifiedMarket` reuses legacy raw-byte/hash/parser equality checks or existing
  receipt custody verification. Its new bounded source-window option rejects
  protected windows; the v1 caller still uses its original range.
- Backfill original intent, response, receipt and independently retained source
  PIN checks reuse `_source`; `_reference` binds the verified original to the
  exact OperationalMarketStore batch. Forward originals use
  `verify_stored_capture`, including their original plan and authorization.
- Operational batch verification replays original response bytes against stored
  observations before selecting tickers. Mock/offline capture origins are rejected.
- Original SQLite files are opened read-only, in read transactions. No destructive
  research/operational merge occurs. The output is a new AnalysisStore, not a
  second normalized market database.
- Duplicate ticker/date content must agree exactly. Both owners remain in the
  manifest; the earliest retained collection time is used deterministically.
- Each source must be quiescent with a pinned whole-file hash. Research and
  operational hashes are checked again after their reads. Multiple files are not
  one atomic snapshot: a concurrent writer causes failure, rather than a mixed
  accepted dataset. Run in a quiet operational window; do not stop the scheduler.
- At most 25 tickers, 20,000 selected rows, 100 operational batches, and existing
  raw response/count/byte bounds apply. Bodies are replayed per source, never
  cached without a bound. Independently retained request/source/snapshot/manifest
  PINs must stay separate from their data artifacts.

Missing panel observations are reported as unknown causes; listing, delisting and
suspension are not inferred from absence. Existing no-trade/discontinuity guards
exclude affected features. Calendar completeness, corporate actions, survivorship,
and publication chronology remain unverified.

Known empty originals (including 2026-10-02) retain their bytes, date and verified
hash. They are not filled or reclassified as holidays. Existing `check_segment`
rejects feature/label windows crossing such missing sessions. Explicit barriers
also reset warmup across the deliberately unread protected interval. A gap is
never compressed into a consecutive feature/label sequence. Other dates use the
existing observed-bars/unverified-calendar semantics, not an authenticated
exchange-session continuity claim.

## Pinned request

The JSON request uses `real_market_dataset_request_v1` with:

- `dataset_version`, sorted unique `tickers`, an explicit aware `cutoff`;
- `research`: absolute `database`, current file `pin`, explicit
  `legacy_research_v1` or `receipt_archive_v1` `source_contract`;
- `protocol`: original locked protocol `path` and whole-file `sha256`;
- `diagnostic_test`: explicit inclusive `start`/`end` after the protected window;
- `known_missing_sessions`: sorted dates with retained, verified empty originals;
- `operational`: absolute storage `root`, whole database `pin`, and `evidence`.

Each backfill evidence item has `kind: backfill`, `policy`, `policy_pin_file`,
`plan`, `plan_pin_file`. Each forward item has `kind: forward`, `capture_root`,
`plan`, `plan_pin_file`, `attempt`, `capture_pin_file`, `batch_pin_file`, the exact
original `authorization` reference, and `authorization_policy`. File paths and
private PIN values belong only in private operator artifacts. No credential is
needed, read, or emitted by this workflow.

The independently retained request PIN is the existing canonical JSON digest of
the full request. Output source identity contains original raw IDs/hashes, receipts
where they actually exist, original capture references, database versions, and
original/new split roles. No receipt is synthesized for legacy research sources.

```bash
dsv real-market-dataset prepare \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --output "$NEW_PREPARED_DIRECTORY" --pins-root "$NEW_PREPARED_PINS"

dsv real-market-dataset train \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --output "$NEW_ANALYSIS_DB" --pins-root "$NEW_TRAINED_PINS"

dsv real-market-dataset verify \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --output "$NEW_ANALYSIS_DB" --pins-root "$NEW_TRAINED_PINS"
```

`verify` also accepts the prepared directory and its pins. It reconstructs the
source-to-snapshot chain, compares retained artifacts, and checks all three
independent pins. It performs no fitting or writes. A changed file, receipt,
source owner, protocol, missing original, conflicting duplicate, unsupported
version, fixture capture origin, or protected range fails closed.

Prepare retains `snapshot.json` and `manifest.json`. Train retains the same
content-addressed snapshot/manifest, the existing model/dataset, an independently
reproduced EvaluationService record, and a final training result. New outputs are
exclusive; an existing output is never overwritten. An interrupted fit can leave
a partial **new** analysis DB without a final result/pin set; preserve it for
inspection and use a separate new output. There is no success claim until the
final result and independent pins exist. Original sources stay intact on failure.
Insufficient data is reported by the unchanged trainer with no fabricated model.

The existing daily model gate recognizes this source version, checks source/split
bindings and rejects days inside the protected test or overlapping the new
training/diagnostic interval. Compatibility does not activate daily analysis,
scheduling, PIT, prediction recording, trading, or execution.

## Local verification

```bash
pytest -q tests/integration/test_real_market_dataset.py \
  tests/integration/test_real_market_training.py tests/integration/test_daily_research.py \
  tests/integration/test_krx_backfill.py tests/learning tests/architecture
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Tests use explicitly documented KRX-schema fixtures, never genuine exchange
observations. The operator's actual data run is reported separately with source
hash preservation, counts, model status and artifact locations. Research scores
are conditional barrier-event scores, not calibrated share-price probabilities.
All external authenticity, PIT, performance and execution approvals remain false.
