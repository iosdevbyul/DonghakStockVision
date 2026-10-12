# A-2 Raw Source Custody Contract

This addition prepares **the actual original `raw_pages.body` bytes** for a future
external storage adapter. It adds no AWS SDK call, upload, local export, table,
schema migration, worker, or deployment change. It does not replace capture
receipts and does not establish external or independently authenticated custody.

## Public local contract

`storage/capture_raw_source_contract.py` exposes:

- `prepare_raw_source_custody(connection, raw_id, *, archive_namespace)`:
  read-only preparation of an immutable `RawSourceCustodyContract`.
- `MAX_RAW_SOURCE_BYTES`: **16 MiB (16,777,216 bytes)**, separate from the existing
  receipt adapter's 64 KiB limit.
- `verify_raw_source_readback(expected, received, *, version_id)`:
  offline comparison of supplied exact bytes against an expected contract.
- `RawSourceRejected`: fixed, redacted failure messages.

The loader issues one parameterized SELECT joining the original page and existing
`raw_capture_receipts` by raw ID. Within that same SQLite statement snapshot it
checks the BLOB storage type, size, receipt schema version and exact correspondence
of run ID, provider, scope (`raw_pages.ticker`), collected-at text, source date
(including NULL), and raw SHA-256. Missing legacy receipts are rejected, never
backfilled. The actual BLOB hash must equal both recorded hashes.

The original body is a `bytes` value; there is no JSON parsing, Unicode decoding,
base64 encoding, whitespace normalization, decompression or reserialization of
that value. Empty BLOBs are valid and retain SHA-256 of empty bytes. Original
metadata text is preserved without timestamp/date normalization. Wrong BLOB or
Python body types, wrong metadata types, corrupted hashes, missing rows, mismatched
receipts and database read errors fail closed. Error strings do not include the
body or database exception details. The body is excluded from the value's repr.
Callers must also avoid logging bodies; a deliberately logged value remains their
responsibility.

The frozen value validates its in-memory bytes/digest and metadata types.
Constructing/replacing a value manually does not prove it was read from a trusted
archive; use the loader for archive-to-receipt verification. Local hashes and
metadata do not authenticate a collector or custodian.

## Namespace and deterministic linkage

The caller must supply a **stable archive namespace**, matching
`[a-z0-9][a-z0-9_-]{0,127}`. Namespaces are case-sensitive validated ASCII slugs;
empty names, whitespace, paths, punctuation, traversal and names over 128 bytes
are rejected. The contract never generates, trims, lowercases or changes one.
Maintain the same namespace for a given archive across restarts, and provision a
different namespace for a different archive. Reusing the same namespace for two
DBs can defeat isolation; the module does not invent persistent archive identity.

`identifier` is SHA-256 of compact, sorted-key JSON containing:

```text
schemaVersion = capture_raw_source_custody_v1
archiveNamespace = caller's exact namespace
byteLength = length of actual bytes
receipt = existing capture receipt fields, including rawId and rawSha256
```

This JSON serializes identity metadata only, never the raw body. Raw body SHA-256
is computed directly from actual bytes. The identity binds the archive namespace,
raw ID, content digest, length and receipt metadata. Re-preparing the unchanged
row after restart produces the same identity; changing namespace, bytes/digest or
receipt metadata produces a different identity. An altered body with the original
digest is rejected instead of producing a usable conflicting contract.

The value also provides `receipt_sha256`, `receipt_idempotency_key`, and
`receipt_payload()`, using the existing receipt serialization and identity rules.
The receipt digest matches the exact existing exported receipt bytes. Nothing is
written back to the receipt or its exported JSON file.

The proposed source key is:

```text
evidence/<archive-namespace>/raw-source-v1/raw-<raw-id>/<source-identifier>.bin
```

For linkage, `receipt_object_key` describes the existing receipt rule for the
same `evidence/<archive-namespace>/` prefix:

```text
evidence/<archive-namespace>/raw-<raw-id>/<existing-receipt-idempotency-key>.json
```

The two keys are always distinct. This is a proposed raw-source destination,
not a relocation of receipts. Existing receipt objects already under another
approved prefix keep their original keys and bytes; the future adapter/operator
must record the actual receipt reference rather than move them to this namespace.
The existing S3 adapter, dev-test key rules and all stored objects are unchanged.

`object_metadata()` describes bounded, body-free metadata for the future adapter:
`schema-version`, `archive-namespace`, `raw-id`, `raw-sha256`, `byte-length`,
`source-identifier`, `receipt-sha256`, `receipt-idempotency-key`. Provider, scope,
run ID, collection time and source date remain exactly in the local contract and
linked receipt. The adapter must persist an S3-supplied VersionId together with
bucket, actual key, identity, length, digest and observed retention deadline.
A VersionId cannot be created or proven locally.

## Size and memory rationale

16 MiB provides room for source responses substantially larger than tiny receipt
JSON while establishing a finite, reviewable per-object bound for a local Mac
workflow. It is an initial operational ceiling, not a measured guarantee that
all providers' responses fit. Oversized sources require a separately reviewed
streaming/size contract rather than silent truncation or a dynamic limit increase.

The SQL SELECT uses `typeof`, `length`, and CASE to suppress an oversized or
non-BLOB body **before transferring it into Python**. The size check, bounded
body and receipt metadata come from one statement, avoiding a gap between a
separate length query and body fetch. SQLite's existing storage/cache behavior
is outside this Python payload limit.

Preparation retains at most 16 MiB of Python body bytes. Hashing does not create
a JSON/base64 copy; the frozen contract retains the returned bytes reference.
SQLite/driver copies and cache can add memory. Offline comparison may hold both
the expected and received body (32 MiB of payload), in addition to SQLite or a
future SDK's buffers. This is not a strict process RSS ceiling. A future adapter
must bound read buffers and concurrency; multiple simultaneous preparations
multiply payload memory. This PR does not implement multipart or streaming I/O.

## Submission and recovery rules for the future adapter

The logical submission identity is `identifier`; remote key derivation must use
the same namespace and identity on retry. The original bytes, receipt linkage and
metadata must remain unchanged. A future upload must be conditional/no-overwrite:

1. Prepare and validate locally; never enqueue/submit a rejected source.
2. Submit the exact bytes at the deterministic source key, without altering the
   receipt key, bytes or existing receipt Outbox.
3. Record the remote VersionId and retention/checksum observation only after
   checking the returned version and remotely read exact bytes.
4. A duplicate or lost upload response triggers lookup/reconciliation of the
   same key and VersionId. Matching identity, length, metadata and bytes can
   resolve the storage observation without creating another version.
5. A conflicting digest/metadata/version, absent or ambiguous remote state,
   expired protection, or storage error blocks completion. Do not overwrite,
   delete, bypass Governance, fabricate a successful result, or automatically
   retry a manual-review item. Missing remote data is not authenticated absence.

S3 upload success and SQLite audit persistence **cannot be an atomic transaction**.
A crash after upload can leave an object without a local audit; a crash before
upload can leave a pending local intent. Recovery must compare independently
queried remote state with the stable local contract, then persist explicit audit
observations. Storage errors cannot be reported as success. This PR implements
no raw upload state machine or raw Outbox table; it leaves all existing receipt
retry budgets, manual-review rules and immutable audits intact.

`verify_raw_source_readback` checks a supplied nonempty/non-null bounded VersionId,
byte type, size, SHA-256 and length. A match returns
`content_consistent_unverified`, with `independent_custody_verified=False` and
`inference_authorized=False`. This is an offline comparison: it cannot establish
that bytes actually came from that VersionId, validate Object Lock or perform a
remote lookup. Those checks belong to the next adapter.

## Verification and next adapter scope

Synthetic tests cover binary byte preservation, metadata/receipt linkage,
SHA-256 agreement and corruption, missing source/receipt, empty bodies, >64 KiB
sources, the exact 16 MiB boundary and over-limit BLOB suppression, invalid types
and namespaces, restart identity stability, archive separation, metadata changes,
receipt key/byte compatibility, read-only one-statement operation, unverified
readback, conflicting bytes, VersionId validation and redacted errors.

The existing network-blocking fixture remains active. Tests use no real AWS
credentials, and this PR does not run a live test or access AWS. The new module
uses only standard-library SQLite/hash/JSON support. Existing A-2 receipts,
Outbox, idempotency, submission/lookup audits, S3CustodySession, VersionId
readback, Object Lock and trust decisions are protected by the full regression
suite.

The next separately approved adapter should implement bounded raw-byte S3
PutObject/conditional replay, exact VersionId GetObject, metadata and SHA-256
verification, Object Lock retention observation, explicit raw audit/recovery
integration and operator-controlled rollout with synthetic dev-prefix tests.
Review credentials, IAM scope, upload costs and retention requirements before
that rollout. It must preserve receipt APIs and must not infer independently
authenticated custody from S3 storage success.
