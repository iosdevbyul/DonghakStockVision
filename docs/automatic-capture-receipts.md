# Local capture receipts (Phase A-2 incremental step)

`SQLiteStore.archive` now saves an original `raw_pages` row and a
`raw_capture_receipts` row in **the same SQLite transaction**. A receipt
records the original page ID, schema version, run ID, provider, collection
scope, original timestamp/source date, and response SHA-256.

Database triggers block ordinary SQL updates and deletes of receipts, and
`check_integrity` compares a receipt against the matching raw response.
Receipt insertion failures roll back the raw-page insert. This covers both
ticker and whole-market collection because both use `archive`.

This is **local capture bookkeeping**, not independent trusted storage.
An administrator with database write privileges can remove triggers, alter
the database, or roll back files. No independently timestamped or signed
copy exists. Existing raw pages are **not** retrospectively stamped with
receipts, because doing so would invent historic collection evidence.

The earlier `SourceCollectionEvidence` schema still requires a separately
established PIT snapshot ID. These local receipts do not invent one, and
are not proof of a complete revision chain or of source authentication.
They do not clear quality flags or authorize model inference.

Next steps for A-2 completion require an independently controlled,
durable append-only evidence sink with a verified delivery/failure
policy and recovery procedure. A-3 will handle independent signatures
and key validation.
