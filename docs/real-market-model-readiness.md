# Original KRX research model readiness and daily analysis

This is an opt-in bridge into the existing Phase 2 trainer, not a new estimator,
collector, database schema or prediction journal. It distinguishes local original
source integrity, model fit availability and independent real-world authority.
It never fetches data, reads credentials, changes operational policies/pins,
activates a scheduler or grants PIT/performance/execution approval.

## Contracts reused and the exact archive difference

The research source uses `bars`, `bar_sources` and `raw_pages`. The operational
source uses `market_observations`, `observation_sources` and pinned ingestion
batches. They are not interchangeable `SQLiteStore` databases. Operational data
continues through PR #227's `verify_stored_capture` and `daily_research`; this work
does not migrate or import it into the historical research database.

The read-only training adapter implements the existing `MarketDataStore` read API
through `ReadOnlyMarketStore`. It selects only explicitly permitted research rows,
checks row/source ownership, replays original bytes using `_OfflineKRXParser`, and
requires exact `DailyBar` reproduction. Database pins are checked before and after
in addition to a read transaction. The original `data.snapshot.capture` constructs
the Phase 2 frozen input, preserving source timestamps, price basis, research
calendar exclusions and real/synthetic distinctions.

Two source contracts must be selected explicitly:

| Contract | Local original verification | Original receipt/custody |
|---|---|---|
| `receipt_archive_v1` (default) | Exact normalized/raw replay | Existing `prepare_raw_source_custody` verifies the original receipt and BLOB |
| `legacy_research_v1` | Original BLOB SHA-256, original metadata, exact replay and retained DB pin | Unavailable: this archive has no `raw_capture_receipts` table |

The legacy mode requires that the receipt table is absent. It never fills receipt
fields, upgrades a schema, generates a receipt, or relabels it as full raw-source
custody. A receipt-capable archive with a missing/corrupt receipt is rejected.
Operational captures and their existing receipt/PIN semantics are unchanged.

`data_origin="real"` retains Phase 2's meaning of locally archived KRX-provider
records. The new source classification explicitly says this is an archived
provider claim, not independent issuer authentication or historical availability.
Tests exercising that contract use synthetic KRX-schema files in temporary
folders; their prices are never claimed as actual KRX acquisitions.

## Protected Final Test and preparation

The original, separately retained `ExplicitResearchSplit` is the access boundary.
Only its training and tuning ranges may be read by this bridge. The protected
outer Final Test remains unread. Its date/key inventory may be inspected as
metadata; it is not warm-up, model-selection or training data.

There is a material existing service constraint: `TrainingService.train` also
computes test diagnostics after parameter selection. It cannot consume an outer
Final Test through this new preparation path merely to make training succeed.
A **separately supplied/pinned inner development split** is therefore required:
its training range stays inside the original training range, and its validation
and development-test ranges stay inside the original tuning range. No new split
is generated automatically and no existing holdout is reclassified. Without an
inner split, preparation inventories original training/tuning and reports BLOCKED;
reported outer test counts are zero because that data was deliberately not read.
This does not assert that the outer test has no records.

The existing `split_dataset` and `requirements(Minimums())` determine readiness.
Their purges and real-data minimums are unchanged:

- At least 5 tickers and 504 feature-anchor dates globally.
- Per direction: training 1,000 labeled rows, 252 dates, 50 positive and 50 negative.
- Each validation/development-test fold: 200 labeled rows, 63 dates, 20 positive
  and 20 negative. Future label windows crossing the next fold boundary are purged.

These are necessary minima, not guaranteed sufficient raw-day counts. Warm-up,
five-observation label maturity, missing/no-trade/discontinuous bars, ambiguous
labels and class/context availability reduce usable counts. Readiness reports
per-ticker ranges and missing dates relative to the selected observed panel,
feature/label exclusions, required/actual directional counts and exact failures.
Exchange-session completeness remains unknown without trusted calendar evidence.
Existing explicitly supplied research exclusions are applied before payload replay;
no new exclusions, holidays, prices or timestamps are invented.

Limits: up to 25 explicit numeric KOSPI tickers, 20,000 selected bars, 4,096 original
responses, 2 MiB per response and 512 MiB total original bytes, a 2 GiB source DB,
and 3,660 calendar days. Only bounded normalized rows and source references survive;
there is no raw-body cache. Source/model files must remain quiescent: the read
transaction and before/after pins are not an independent custodian or atomic
snapshot of every filesystem artifact. WAL/hot-journal sources are refused.

## Existing CLI: prepare, train, evaluate

Use existing reviewed files/pins, not the example's variable names as evidence.
The source is explicit; no default DB, credentials, provider client or fixture
fallback is used. `--analysis-db` must name a **new separate** artifact output;
prepare-only never creates it. Existing databases/private files are not overwritten.

```sh
dsv --db "$RESEARCH_MARKET_DB" train \
  --mode historical-research --analysis-db "$NEW_MODEL_ARTIFACT_DB" \
  --tickers 000660 005380 005930 035420 051910 \
  --start "$SOURCE_START" --end "$SOURCE_END" \
  --snapshot-as-of "$SNAPSHOT_CUTOFF" --acknowledge-research-limitations \
  --real-market-source --market-sha256 "$RETAINED_MARKET_SHA256" \
  --access-split "$ORIGINAL_SPLIT" --access-split-pin "$ORIGINAL_SPLIT_PIN" \
  --research-source-contract legacy_research_v1 \
  --research-calendar "$ORIGINAL_RESEARCH_CALENDAR" --prepare-only
```

With no inner split, start/end are the original training.start/tuning.end.
For a separately reviewed inner development split, supply `--fit-split FILE` and
`--fit-split-pin PIN`, and start/end must exactly equal its training.start/test.end.
After READY preparation, omit `--prepare-only` to invoke the unchanged trainer.
Snapshot/source identities, dataset split/purge audit, model version and training
result are stored using the existing `AnalysisStore` schema. The source pin and
original receipt IDs/raw IDs are preserved. Insufficient data returns BLOCKED
without creating an output DB; invalid/mismatched evidence fails closed.

`TrainingService`, `feature_vector`, `fit_linear`, `LinearInference` and `load_model`
retain their mathematics and identifiers. Training and evaluation artifacts stay
separate. The existing trainer's internal fold diagnostics remain unchanged;
`evaluation_recorded=false` means no separate evaluation artifact was written.
To independently rebuild/check those development-fold diagnostics, use:

```sh
dsv evaluate --mode historical-research --analysis-db "$NEW_MODEL_ARTIFACT_DB" \
  --model-version "$TRAINED_MODEL_VERSION"
```

This invokes the existing `EvaluationService`; it is not an outer Final Test
unsealing command or a grant of performance approval. Models remain unregistered,
research-only, with no operational publication or forward-prediction eligibility.

## PR #227 connection

The existing `daily-research run` and optional scheduler post-step continue to
require explicit model DB/version. Real models now additionally require the
frozen `real_market_source` binding in their original training snapshot. The
consumer verifies request/snapshot/split identities, real KRX unadjusted profiles,
and that the requested analysis day neither overlaps the fitted data nor enters
the protected outer Final Test. An old generic model missing that binding stays
loadable under the original Phase 2 contract but is BLOCKED for new real daily
analysis. It is not silently retrained, annotated or approved.

Synthetic-model behavior stays unchanged. Per-ticker source insufficiency, daily
idempotence, immutable outputs, failure recovery and operational diagnostics from
PR #227 remain in effect. Adequate model preparation does not supply the minimum
11 recent, verified operational observations required by daily feature scoring.
No new prediction or outcome journal writes occur. Provider-backed prospective
recording remains unsupported. Local timestamps/custody remain unauthenticated;
PIT, performance, signal, PAPER and execution approvals remain false and unavailable
performance metrics remain null.

## Actual read-only local findings

The existing research database has 993 ticker identities and records extending
from 2022 into 2026. This is metadata inventory, not whole-database qualification.
An original retained split places training at 2022-01-03–2023-12-28, tuning at
2024-01-02–2024-06-28 and protected Final Test at 2024-07-01–2025-06-30.

The task's read-only preparation used the retained database pin and existing
research-calendar exclusions, replaying the original sources for 000660, 005380,
005930, 035420 and 051910. Each has 611 selected observations from
2022-01-03 through 2024-06-28, with no missing selected-panel dates. This legacy
archive has **no receipt table**. Its locally replayed original bytes do not prove
independent publication time or custody.

| Direction | Training labeled rows / dates | Tuning labeled rows / dates |
|---|---|---|
| Up-context | 1,235 / 428 | 293 / 106 |
| Down-context | 1,096 / 404 | 274 / 105 |

Preparation is **BLOCKED**: no separately reviewed inner development split was
supplied. The existing tuning pool also cannot supply two disjoint folds with at
least 63 direction-specific dates each: it has only 105–106 such dates, before
additional boundary purges. Additional approved development data/protocol review
is needed; this is not permission to take dates from the protected Final Test.
Existing research models are recorded in prior artifacts, but were not rewritten,
retrained, re-evaluated or automatically selected for production in this task.
No actual-data training success or daily real-data scoring is claimed.

## Offline validation

```sh
pytest -q tests/integration/test_real_market_training.py
pytest -q tests/integration/test_daily_research.py tests/learning \
  tests/integration/test_krx_005930_pilot.py \
  tests/integration/test_operational_market_storage.py \
  tests/integration/test_operational_capture.py \
  tests/integration/test_prospective_prediction_workflow.py
ruff check src tests
ruff format --check src tests
mypy
python -m build --no-isolation
```

The independent new integration fixture uses the actual real-data minimums,
original raw parser/receipt validators, Phase 2 fitting, immutable artifact loading,
separate evaluation and score-only inference. It poisons the protected outer
holdout to detect accidental access. Negative tests exercise rehashed source
corruption, receipt/ownership substitution, insufficient/future inputs, unknown
contracts, missing/forged model provenance, explicit research exclusions and a
credentials-free read-only CLI. Existing research/operational databases and
private policies/pins are checked by before/after SHA-256 comparisons.
