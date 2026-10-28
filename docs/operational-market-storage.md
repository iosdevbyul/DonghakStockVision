# Persistent operational KRX market storage

This is a private local storage foundation, not a collector or a new authorization
system. It is independent of the recovered-observation query in PR #150. It uses
only the merged main capture, raw-receipt, gzip and KRX parsing contracts.

## Architecture and research boundary

The historical `SQLiteStore` has a `(ticker, trading_date)` key and supports
reviewed revisions by replacing its cleaned projection. Reusing that table for
operational data would mix markets and make an operational DB look like an existing
research input. The new store therefore has distinct operational tables with a
`(market, trading_date, ticker)` primary key, its own SQLite application ID and an
explicit schema version. It contains no historical `bars`, `bar_sources` or research
analysis tables. The legacy writable store rejects its application ID before adding
research tables. Existing read-only research readers cannot read operational rows as
historical bars.

Shared code remains authoritative:

- `RAW_SCHEMA`, `archive_raw_page` and `raw_capture_receipt_v1` come from the existing
  SQLite implementation. Its existing `archive` method still owns its original
  transaction; the operational caller owns a single larger transaction.
- Existing `KRXProvider.parse_market`, `DailyBar` and bounded gzip decoding validate
  every supplied row. No provider transport, key or HTTP client is constructed.
- Existing canonical `FrozenJSON` serialization fingerprints local batch manifests.
- The original receipt export/verifier can read the raw tables unchanged. No outbox,
  custody upload, independent receipt, approval or scheduler is created here.

Every manifest and status explicitly keeps PIT, training, OOS, forward-signal, PAPER
and execution eligibility false. Local hashes and structural replay detect local
inconsistency; they do not independently authenticate provider origin, publication
time or custody. Changing an entire locally controlled DB and recomputing all hashes
cannot establish historical authenticity. Existing approval and independent custody
requirements remain necessary before any downstream qualification.

See [forward capture](forward-market-data-capture.md),
[existing authorization](authorized-krx-forward-paper.md) and
[historical descriptive analytics](real-market-data-analytics.md) for those separate
boundaries. No old pilot, failed manifest, recovery artifact or trusted pin is
imported, migrated or rewritten.

## Private SSD layout

The default is `$HOME/DonghakStockVision-Data`, normally on the Mac home volume.
Use `df -h "$HOME"` to confirm the intended local volume before initialization;
custom roots must be explicit absolute canonical paths without symlink aliases.
No data directory is created simply by importing a module or inspecting a missing root.

```text
~/DonghakStockVision-Data/             # 0700, owned by the current operator
  database/                          # 0700
    operational.sqlite3              # 0600
    operational.sqlite3-journal       # transient SQLite rollback journal, when needed
  logs/                              # 0700, reserved for operator-managed logs
  backups/                           # 0700, reserved for operator-managed backups
```

Original raw bytes and manifests live **inside SQLite**, not duplicated in separate
`raw/` and `manifests/` file trees. This allows receipts, normalized data, provenance
and manifests to commit together. Logs and backup directories are placeholders, not
an implemented retention policy or evidence of a successful backup. Nothing is
pruned or uploaded automatically. Use a separately reviewed SQLite online backup
procedure rather than copying a live DB without its transaction state.

Unknown, incomplete, permissively accessible, hard-linked or incompatible stores
are rejected. Existing files are never chmodded, deleted or silently upgraded.
An interrupted first initialization leaves files for operator review and fails
closed; do not point initialization at original evidence or a research directory.

## Offline CLI

All commands use the existing `dsv` entry point and emit versioned canonical JSON.
`DSV_DB_PATH` and the legacy global `--db` option do not select an operational DB;
use `--root`. Supplying global `--db` to this command is rejected.

```sh
ROOT="$HOME/DonghakStockVision-Data"
dsv market-storage init --root "$ROOT"
dsv market-storage status --root "$ROOT"
dsv market-storage validate --root "$ROOT"
dsv market-storage duplicates --root "$ROOT"
dsv market-storage usage --root "$ROOT"
dsv market-storage latest --root "$ROOT"
```

`init` creates an empty store. Repeating it on a recognized store preserves data and
configuration. It may explicitly permit SQLite's normal hot-journal recovery of an
interrupted operational write. Normal status/validation commands open read-only and
never initialize, repair or migrate a missing/incompatible DB. If a hot journal
requires writable recovery, inspect the interrupted operation and run `init` on the
same recognized operational root, then `validate`.

`status` reports the schema version, table names, unique key, market counts, latest
normalized date, latest response date, batch count, disk usage and false eligibility
flags. `duplicates` reports normalized duplicates (normally zero because of the
primary key) separately from additional source links. Repeated captures are valid
provenance, not duplicate normalized observations. An empty response has no latest
normalized trading date; its response date does not prove a holiday or valid session.
`validate` exits 1 for detected data-integrity failures; configuration, filesystem or
SQLite errors exit 2. Query commands do not issue any market-data request.

## Schema and provenance

| Table | Contents |
|---|---|
| `operational_settings` | Immutable v1 configuration and resource limits |
| `raw_pages` | Original supplied bytes, source date, collection UTC, provider/scope and SHA-256 |
| `raw_capture_receipts` | Existing immutable v1 receipt linked to the original body |
| `ingestion_batches` | Unique batch/idempotency key, request fingerprint, raw ID and canonical manifest/fingerprint |
| `market_observations` | Unique market/date/code, canonical `DailyBar`, nullable provider-reported name, validation status and primary raw ID |
| `observation_sources` | Every observation-to-receipt link and original row values, including unknown provider fields |

Prices, volume and trading value retain the existing exact signed-64-bit integer
KRW/share semantics. Fractional, missing or unsupported values are rejected rather
than rounded or invented. Unquoted JSON fractional numbers, including in unknown
extra fields, require an explicit future precision contract and are rejected;
original numeric strings remain strings. Codes stay strings, including alphabetic suffixes. The
whole original response preserves its original encoding and formatting; canonical
row JSON preserves its values and extra fields. `ISU_NM` is a nullable
`reported_name`, not a DART company name or newly verified Security Metadata name.
No English translation, type classification or historical name validity is inferred.

The supplied source date must match every `BAS_DD`, and the selected market must
match every `MKT_NM`. Duplicate codes in a response invalidate the batch. All rows
in a bounded response can be stored, including a full KOSPI response, but row count
alone does not prove universe completeness or trading-calendar coverage.

## Explicit ingestion API for a future approved collector

There is no live collection, arbitrary artifact import or recovery import CLI.
The verified capture bridge below composes this synchronous API.
A future separately authorized collector can compose the synchronous
API with the existing capture pipeline. It must retain every attempted original
response through that pipeline, including responses rejected by normalization.
This ingestion API accepts structurally validated batches atomically; rejection
leaves the store unchanged. It is not a replacement for the original capture archive.

```python
from contextlib import closing
from pathlib import Path

from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.operational_market import OperationalMarketStore

# `page` must be the explicitly supplied original response bytes/date/UTC collection
# metadata, retained by the authorized capture owner. No fetching occurs here.
page: RawPage
with closing(OperationalMarketStore(Path("/absolute/private/root"), writable=True)) as store:
    manifest = store.ingest(
        "explicit-source-event-id",
        "KOSPI",
        page,
        source_kind="local_response_unqualified",
        content_encoding="gzip",     # actual retained response header
        representation="wire",       # actual retained byte representation
    )
```

Tests use `source_kind="synthetic_fixture"`; neither allowed label establishes
verified HTTP origin. No API key is accepted by this storage method. The caller must
supply accurate `wire` versus `httpx_decoded` representation; gzip is never guessed.
Both retained and decoded bodies are limited to 2 MiB using the existing decoder.
The API rejects recovery JSON because it is not an original `OutBlock_1` response.
Do not feed the existing private recovery or pilot into this store without a separate
explicitly authorized and reviewed import procedure.

A same-ID/same-input retry returns the original verified manifest. A same-ID/different
input retry fails. A new capture of unchanged values adds source evidence while
preserving the first normalized payload. Conflicting prices, names or adjustment
semantics require a separately reviewed revision procedure; no silent upsert occurs.
No current CLI collection command is redirected into this DB. Full collector and
scheduler integration remains future work under existing authorization requirements.

## Atomicity, concurrency and recovery

Each worker owns its own connection. `BEGIN IMMEDIATE` serializes writers with a
bounded five-second SQLite busy timeout. Source bytes, receipt, all normalized rows,
source links and manifest share one transaction. Any exception or process interruption
before commit preserves the prior committed state. After commit but before caller
acknowledgement, retrying the same source event does not duplicate the batch.
Read-only inspection and validation use a consistent read transaction.

The reference uses SQLite DELETE rollback journaling with `synchronous=FULL`.
SQLite's normal recovery handles a hot journal on an explicitly writable operational
open. File-header/application/version checks occur before that writable open, so an
unrelated historical DB is not recovered or migrated by this adapter. Validation
checks SQLite/FK integrity, expected schema/triggers, raw/receipt hashes, manifest
fingerprints and independent reconstruction of rows from the retained source.
Rehashed but inconsistent manifests and changed timestamps/values fail validation.
This does not claim distributed exactly-once collection, immunity to privileged
same-user file replacement, hard process isolation or a complete backup system.

## Storage budgets and operational limits

Defaults at explicit initialization are:

- Database limit: 10 GiB, additionally enforced with SQLite `max_page_count`.
- Required free space: 256 MiB, plus conservative per-batch rollback/write headroom.
- Maximum rows per batch: 2,000 (configurable up to 10,000).
- Response and decoded limits: 2 MiB each.
- Disk inventory: at most 10,000 filesystem entries; no symlink traversal.

For a new store only:

```sh
dsv market-storage init --root "$HOME/DonghakStockVision-Data" \
  --max-database-bytes 10737418240 --min-free-bytes 268435456 \
  --max-rows-per-batch 2000
```

Configuration is immutable for this v1 foundation. Reinitializing with different
limits is rejected; a future explicit migration must preserve prior evidence.
Free-space checks are cooperative: other processes can consume space after a check.
The DB page limit is not a hard cap on temporary journals, backups, logs or process
memory. Status exposes actual storage/free-space measurements; capacity errors stop
new writes, and SQLite I/O/full errors roll back the transaction. There are no hidden
retries, retention deletions, network requests, approvals or automatic backup claims.

## Import an already verified capture (offline)

`dsv market-storage ingest-capture` connects the existing immutable capture archive
and its original trusted pins to the transaction above. It constructs no HTTP client,
reads no key and accepts no network opt-in. Initialize a separate operational root
first. There is no arbitrary JSON/recovery import command.

The bridge checks the plan's independent canonical pin and protected partition
before source I/O, then invokes `verify_capture`, original authorization verification
and hash-checked `readback`. It requires a complete HTTP 200 response without a
transport error. The entire `OutBlock_1` must pass the existing bounded decoder,
KRX market parser, exact numeric and duplicate-row checks before the destination
is opened for writing. Invalid rows invalidate the whole batch; nothing is filtered
out to hide errors. An empty response is not imported as a complete market.
Response row count does not independently prove the exchange universe is complete.

**Current authorization constraint:** capture plans v1 bind a single ticker. The
prospective v2 `operator_read_only_pilot_v1` scope is explicitly limited to 005930.
Neither approves importing other instruments from a whole-market response. The
bridge rejects such real-provider inputs with
`full_market_ingestion_not_authorized_by_single_ticker_plan`, after checking the
original authorization. No authorization rule or plan schema is relaxed here.
The real 2026-10-26 pilot's retained 940 rows are **not imported**. A separately reviewed `operator_full_market_recurring_v1` policy and
`forward_capture_plan_v3` can now authorize prospective whole-market ingestion; see
[the recurring operator guide](krx-full-market-recurring.md). They never broaden
prior pilot captures. No existing private pilot is imported.

A real-provider capture containing only its authorized ticker may be imported with
its original independent-review or operator-policy package. All eligibility remains
false. Mock-provider authorization fixtures remain identified as
`authorized_provider_mock_fixture`; they are not genuine provider captures.

Full-market integration is verified offline using explicit synthetic capture
fixtures, including authorized-provider mocks of the separately reviewed recurring
policy. No genuine whole-market capture has been performed by these changes. The fixture acknowledgment cannot relabel an authorized-provider
capture as synthetic. It also does not grant permission to collect anything:

```sh
# CAPTURE_ROOT and PLAN refer to an existing OFFLINE FIXTURE capture, not private
# real pilot evidence. Pins must be retained separately by its original owner.
dsv market-storage ingest-capture --root "$ROOT" \
  --capture-root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \
  --attempt "$ATTEMPT" --capture-pin "$CAPTURE_PIN" --sequence 0 \
  --allow-synthetic-fixture
dsv market-storage validate --root "$ROOT"
```

For an eligible single-instrument provider capture, omit the synthetic flag and add
`--authorization-root "$AUTH_ROOT" --authorization-pin "$AUTH_PIN"`.
Independent review (the default) additionally needs the existing
`--reviewer-registry`, `--reviewer-registry-pin` and `--operator-id` arguments.
Operator review instead needs explicit
`--authorization-policy operator_read_only_pilot_v1`; it does not accept reviewer
trust. Original permission is checked at the recorded request/response times, so
read-only verification does not invent a new collection authorization after expiry.
Never publish these private arguments or packages. No API key is required for import.

### Retained source binding and verification levels

The original archive/receipt remains authoritative and unchanged, including any
failed original scoped ingestion. The destination retains another exact raw-byte
copy with its own receipt/run/raw ID. Those destination receipt identities are **not**
the original receipt identities.

A separately versioned `operational_krx_batch_v2` manifest retains a bounded
`operational_capture_reference_v1` in its request: capture ID/pin, plan pin, original
raw ID and receipt pin, authorization and trust reference pins where applicable,
response sequence/date/time, raw SHA and explicit origin. It contains no filesystem
path, secret or original permission document. Every normalized row links to the
operational raw ID, which links through this manifest to the exact original capture.
The canonical reference fingerprint is the idempotency/batch ID. There is no new
table, migration or custody system. Existing batch v1 bytes and validation remain
unchanged; readers encountering v2 must understand this added source reference.

`validate` checks the local reference/body/date/market/time binding and reconstructs
all normalized values from the retained bytes. It cannot independently authenticate
an original archive that is no longer supplied. Repeating **the original
`ingest-capture` command with all original trusted pins** performs original-source
and authorization semantic verification again, then verifies and returns the same
local batch. A tampered source is rejected even if a previous import succeeded.
This is the explicit source-to-destination verification path; a local rehash is not
an independent trust root. The command returns a stable
`operational_capture_ingestion_v1` summary with original ingestion state, imported
count, batch identity, provenance classification, zero network requests and six
false eligibility flags. It never rewrites a failed capture into original success.

Tests construct 940-row synthetic plain/gzip captures with existing `run_capture`
and `MockTransport`, then use this same bridge and CLI. They cover original pin and
receipt verification, exact values/names, idempotency, conflicts/atomic rollback,
interrupted captures, tampering, authorization scope and unchanged source files.
No genuine authorization or market-data outcome is asserted from those fixtures.
A future scheduler can call `storage.operational_capture.ingest_capture` after its
separately approved capture; no scheduler or collection call is implemented here.
