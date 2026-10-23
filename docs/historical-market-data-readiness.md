# Historical market-data readiness and provenance

This boundary performs real read-only inventory, bounded original-price replay and
calendar verification. It does not turn historical records into PIT-qualified
strategy inputs. The actual local result is **blocked historical strategy replay**.

## Repository sources actually discovered

| Asset | Implemented capability | Evidence and limitations |
|---|---|---|
| `providers.krx.KRXProvider` | Official daily KOSPI/KOSDAQ/KONEX endpoint contracts, `basDd`, exact integer unadjusted OHLCV/value | A documented provider contract is not a locally available dataset or source authentication; no provider is contacted here |
| `.data/market.sqlite3` | 1,099,255 latest cleaned daily rows, 993 symbols; date range 2022-01-03 through 2026-10-01 | Existing pinned file, scope-specific raw replay required; not revision history or historical availability evidence |
| `raw_pages`, `bar_sources`, `collection_runs` | Original payloads, local SHA-256, local ingestion metadata and raw-to-cleaned links | 2,151 recorded KRX raw pages, source dates through 2026-10-02; local retrieval times 2026-09-29 through 2026-10-02 cannot prove 2022–2025 availability |
| `storage.sqlite` current contract | New archives also record `raw_capture_receipts` | That table is **absent in the actual existing Market DB**; this work does not migrate it |
| `docs/evidence/baseline-official` | Preserved KIND monthly calendar responses and reviewed KRX calendar/settlement metadata | Fourteen months June 2024–July 2025 exist; this workflow independently replays the June 2024 month, not every dataset/session |
| Independent custody contracts / local S3 test inventory | Signed-acknowledgement, readback and deployment-readiness code exists | Local S3 inventory references synthetic dev-test archives; these do not prove historical publication of the Market DB rows |
| Corporate-action/lifecycle evidence | Existing reviewed coverage and identity contracts, partial official/DART evidence | Complete corporate-action coverage remains 0/993; no approval is created by this report |
| Historical input/execution | Existing linear/HGB historical research pipeline and explicit daily execution assumptions | Uses `after_trading_date_seoul_midnight`; code states `not_pit_verified` and `not_oos_evidence`; this assumption is not reused as an observed timestamp |
| Native Trading Core/Paper Broker | KR/US synthetic instruments, currencies and simulation | Does not establish an actual US historical source or a historical-input approval bridge |
| Frozen OOS and improvement | Explicit synthetic authorization, custody, replay and isolation contracts | Unchanged; historical observations cannot be inserted into `SyntheticObservations` or passed as synthetic OOS evidence |

Only the scoped KOSPI instrument is verified here. KOSDAQ/KONEX provider coverage
is a code capability, not a claim that those datasets were acquired and verified.
No US historical provider/dataset was discovered. The new adapter supports the
Trading Core's KOSPI/KOSDAQ KRW identities; KONEX has no matching Trading Core
exchange identity, and US historical inspection fails closed. The preserved KIND
`ldMktTpCd=1` month is accepted only for KOSPI session comparison.

The existing official calendar-verification artifact reports 46 securities with
missing sessions in its earlier full baseline audit. That stored finding is not
revalidated over final-test prices here and must not be presented as a new audit.
Research exclusions are not exchange holidays. A raw date without cleaned bars,
or an empty response, is not proof that an exchange was closed.

## Immutable read scope and source pins

`HistoricalReadScope` reuses `Instrument`, `ResearchDateRange`, and
`ExplicitResearchSplit`. It binds the exact instrument, inclusive window,
training/tuning partition, independently retained split fingerprint, original DB
SHA-256, row budget and original-byte budget. Only training and tuning are accepted;
the window must be wholly contained in the appropriate original partition.
OOS/final-test/holdout or out-of-partition requests fail before opening the file.

A split pin bounds inspection, not model training or PIT approval. The local split
source hash records existing metadata; it is not an independently authenticated
training-custody attestation.
A caller must obtain original split/custody pins independently, rather than hashing
newly supplied data and declaring it trustworthy. The local example uses the
original `phase2-training-20261001/summary.json` split metadata: validation ends
2024-06-28 and final_test begins 2024-07-01.

During initial exploration, one already-exposed baseline row dated 2024-07-01 was
read before that split was identified. It was not used in the report, candidate
feedback or execution. The functional workflow and committed observations' hashes
use **only 2024-06-24 through 2024-06-28**, inside the original validation partition.
No final-test observations are included in the artifact. This distinction is
recorded rather than claiming that the initial exploration never read that row.

## Read-only original replay

`storage.historical_readiness.inspect_historical_market`:

1. Checks exact scope owners, role, split/window and independent scope pin.
2. Rejects active WAL/journal state; verifies the complete original file pin.
3. Opens `mode=ro&immutable=1`, sets `query_only=ON`, closes the connection explicitly.
4. Collects aggregate inventory metadata; it does not load all historical prices.
5. Reads at most 64 bars for one symbol and no more than 366 days.
6. Rejects raw-link dates/markets outside the authorized observation before reading
   a response body. Uses exact original raw links and hashes; reparses KRX payloads with the
   existing provider parser in an offline subclass whose network method raises.
   No credential or HTTP client is created. Full original market pages may be
   parsed to verify source shape, but only selected instrument/date rows enter the
   projection. At most 8 MiB of linked original response bytes is loaded.
7. Requires an exact original bar including its ingestion metadata; revisions are
   not silently selected by an invented historical cutoff. Wrong source scopes,
   raw hashes, markets, row identities, duplicates and mismatches are invalid.
8. Replays provided calendar owners with original `normalize_month`, independent
   normalized pins and original-byte hashes. Missing calendar days, unexpected or
   duplicate sessions fail closed. Without a calendar, expected gaps are unknown,
   not inferred from weekdays.
9. Verifies the DB pin and journal state again before returning immutable output.

A stable offline file is required. This is not concurrent transactional capture or
process-crash durability. The size caps are cooperative input bounds, not hard RSS
or CPU isolation. A file up to 2 GiB is hashed twice per inspection; independent
verification repeats the work. No large-scale throughput improvement is claimed.

## Timestamp and adjustment semantics

The canonical dated projection is `historical_strategy_observations_v1`.
It preserves the instrument/currency, native exact close, daily trading date,
raw-versus-adjusted classification, original ingestion timestamp, raw IDs, row
hashes and DB pin. The date is not converted to midnight, session close, receipt
time or a fabricated publication instant. `observation_at` and `available_at`
remain null with a stable explanation, and `canonical_market_quotes` is null.
It is deliberately not a `MarketQuote`, `PaperFrame` or `SyntheticObservations`.

KRX raw parser semantics are unadjusted. Changed/adjusted/unknown cleaned prices
cannot match an unadjusted original merely by rehashing the cleaned data. Missing
adjustment, corporate-action timing and revision evidence remain explicit blockers.
No adjusted-close substitution, future-known action, forward fill, successor
stitching, FX conversion or terminal liquidation is synthesized.

A local raw hash proves consistency with preserved bytes, not provider issuer
authentication or historical custody. The projection's origin explicitly says
`archived_provider_claim_not_independently_authenticated`. Tests use synthetic
archived-response fixtures; parser success never relabels those as authentic data.

## Versioned readiness states

`historical_market_readiness_v1` deterministically reports:

- `structurally_invalid`: invalid rows, identity/raw mismatches, duplicates,
  missing expected sessions or incompatible calendar context.
- `structurally_valid_missing_original_provenance`: valid rows without original
  raw links/evidence. Missing links are not repaired.
- `provenance_replayed_missing_independent_qualification`: scoped original bytes
  and cleaned records match; independent historical custody/PIT/action/approval
  requirements still remain missing.

The contemplated eligible narrow-replay state is **not emitted**: no authentic
approval owner bridging these daily records to native strategy decisions exists.
There is no new approval boolean, callback, authority, or summary import through
which a caller can obtain that label. Existing Corporate Actions, Runner Tail,
Run Input Freeze, strict PIT and OOS governance remains unchanged.

All labels are separate from profitability, frozen historical OOS, forward-paper
trading and live brokerage. A verified historical calendar alone cannot approve
prices, corporate actions, model custody or strategy execution.

## Actual local result

The committed [metadata artifact](evidence/historical-market-readiness.json) binds:

- Original Market DB SHA-256:
  `2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`.
- `KR / KOSPI / KRW / 005930`, inclusive 2024-06-24–2024-06-28, original validation
  partition represented as `tuning` for inspection only.
- Five selected unadjusted rows, exact original KRX replay and pinned original
  June KIND calendar; zero missing/duplicate/unexpected scoped sessions.
- Readiness: `provenance_replayed_missing_independent_qualification`.
- Controlled **strategy replay blocked**; zero orders/fills, no strategy/model run.

The artifact includes scope/split metadata, raw source hashes and dates, row
fingerprints, calendar pin, report fingerprint and blocked replay response. It
contains no newly published raw response or price projection. Aggregate dataset
coverage is not a claim that all 1,099,255 rows or all original pages were verified.

## Verification and reproduction

`verify_historical_readiness` checks the externally retained report pin and reruns
original DB reads, raw parsing, calendar normalization and observation projection.
It compares complete original results. Rehashed but changed readiness labels,
forged availability times, altered prices or substituted files fail closed.
`controlled_historical_replay` performs this verification before returning a
versioned blocked response; it does not import a broker, model or strategy runner.

```python
from donghak_stock_vision.storage.historical_readiness import (
    inspect_historical_market, verify_historical_readiness, controlled_historical_replay,
)

# scope, scope_pin, calendar owners and their pins come from independently
# reviewed original metadata, not a newly self-rehashed report. database is local.
result = inspect_historical_market(database, scope, scope_pin, calendar_owners)
report_pin = result.report.to_dict()["content_fingerprint"]
verify_historical_readiness(result, report_pin, database, scope, scope_pin, calendar_owners)
blocked = controlled_historical_replay(
    result, report_pin, database, scope, scope_pin, calendar_owners,
)
assert blocked.to_dict()["status"] == "blocked"
```

The committed metadata artifact supplies the exact local scope/split, calendar raw
file reference and pin used for this observation. Tests independently construct
small synthetic SQLite archives and calendar owners; CI does not require the
private production DB or a financial-provider connection.

## Next stage and unsupported claims

Next implement independently reviewed historical availability/revision custody and
complete instrument/corporate-action coverage, bind verified session timings and
original strategy policy approvals, then design an explicit historical execution
bridge. Do not weaken synthetic-only contracts or reuse the latest-known daily
availability assumption as measured PIT evidence. Frozen historical OOS requires
its own independent eligibility and untouched-holdout review afterwards.

No historical strategy return, profitable run, OOS approval, forward-paper stage,
operational activation or live brokerage readiness has been demonstrated here.
