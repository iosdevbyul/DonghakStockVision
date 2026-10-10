# Source collection evidence review (Phase A-1, contract only)

The existing ingestion pipelines already generate a collection run ID and call
`MarketDataStore.archive(run_id, provider, scope, RawPage)`, followed by `save`
and `record_run`. `RawPage` contains the original response bytes, observed
collection timestamp, and optional source date. This change does **not** replace
those facilities, modify database schemas, or execute any network collection.

`SourceCollectionEvidence` declares the provider, scope (ticker or reserved
whole-market scope), source date, observed time, run ID, collector name and
version, original-byte SHA-256, observation kind (initial or revision),
predecessor content hash for revisions, and a snapshot ID.

`inspect_source_collection` compares the supplied evidence document to a
separately supplied archived `RawPage` and independently supplied evidence
fingerprint. It checks the declared identity, timestamp and source date,
original-byte digest, and versioned evidence schema. The out-of-band fingerprint
must come from a separate trusted channel: a hash computed from the same
untrusted document does not add trust.

## Security and integration boundaries

- This is an **offline, read-only contract/validator**. It does not yet
  automatically emit records when `archive` is called or backfill old records.
- There is no trusted writer, append-only log, timestamp attestation, signing
  authority, or cryptographic chain to PIT revision ledger in this PR.
- The observation kind and predecessor digest are **claims**, not proof that
  every revision was captured or that bytes existed at a historical cutoff.
- A source-provided timestamp or caller-supplied fingerprint is not independent
  historical evidence. Do not infer collection authenticity from hash equality.
- `provenance_authenticated`, `historical_availability_verified`, and
  `inference_authorized` always remain false, even on content match.
- Existing data quality flags, PIT cutoff checks, model approval and execution
  gates must remain in place.
- Tests are offline. No production database, API, pipeline state, source data,
  or deployed model is changed.

Future work: emit this contract at the actual collector/archive boundary using
durable independent storage; establish a trusted signer/key lifecycle; bind
collected raw versions to the revision ledger and PIT snapshot. Only then
independently assess historical availability and quality restrictions.
