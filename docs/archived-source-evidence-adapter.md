# Archived source evidence adapter

`build_archived_source_evidence` is a **read-only, explicit** bridge from the
existing SQLite `raw_pages` archive to `SourceCollectionEvidence` (Phase A-1).

The caller identifies a persisted `raw_id` and a separately established PIT
`snapshot_id`. Collection occurs before snapshot creation, so no snapshot ID
is invented by the collector. Existing archive bytes, run ID, provider, scope,
source date and collection timestamp are reused; the original-byte checksum is
recomputed before exporting. The collector name/version, observation kind and
prior revision digest are explicit caller assertions, not authenticated facts.

The return value contains deterministic JSON bytes and a SHA-256 for transport.
The SHA-256 is calculated **from the same document** and must never be
represented as an independent trusted pin. It does not prove historical
availability, collector identity, complete revision history, or a trusted
timestamp. Do not mark source provenance or inference as authorized.

The adapter does not mutate the database, call the network, create snapshots,
sign evidence, emit automatic collection events, modify quality flags, or
grant model execution. It is not a replacement for independent secure capture,
durable append-only logging, and collector signing, all of which remain future
work. Offline tests use an in-memory SQLite database.
