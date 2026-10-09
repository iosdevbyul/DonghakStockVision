# Corporate-action evidence acquisition pipeline

The final corporate-action gate remains closed. This pipeline is additive evidence
work after the final coverage contract was locked.

## What this slice does

The pipeline reuses the existing DART corporate-action review queue and produces:

- a deterministic unique DART document acquisition plan
- secret-safe batched acquisition through the existing `DartEvidenceClient`
- offline verification of cached document bytes and receipts
- one fail-closed review row for every locked-universe security
- an acquisition manifest linked to the current DART and security-lifecycle
  fingerprints

It does not parse a filing into an approved economic event, establish an effective
date, promote boundary identity to full-period continuity, adjust prices, generate
signals, or execute a backtest.

## Batched document acquisition

The candidate queue can contain many disclosures, so the collector defaults to
25 documents per invocation. Batching avoids an accidental large DART request run
and lets collection resume from the immutable cache.

Use a local ignored directory for acquisition bytes:

```bash
python -m donghak_stock_vision.backtest.collect_corporate_action_documents \
  --root . \
  --output .data/corporate-action-documents \
  --offset 0 \
  --limit 25
```

Repeat with increasing offsets after reviewing sanitized output. The DART key is
read from `.env`; it must never be printed, committed, or copied into evidence.

## Offline replay

After one or more acquisition batches:

```bash
python -m donghak_stock_vision.backtest.verify_corporate_action_acquisition \
  --root . \
  --sources .data/corporate-action-documents \
  --output /tmp/corporate-action-acquisition-replay
```

The replay requires a new output directory and validates request identity, HTTP
status, gzip payload integrity, raw SHA-256, and the documented ZIP response shape
before counting a document as preserved. An HTTP 200 XML application-error body
must never be accepted as a preserved document source.

The resulting security review queue remains `PENDING_REVIEW` for all securities.
Existing lifecycle boundary identities remain supporting evidence only; they do
not become whole-period historical identity.

## Next evidence step

When document acquisition is complete, reviewed effective dates, exact historical
security identities, event-class query completeness, and authoritative no-event
semantics must be captured as explicit review evidence. Only those reviewed
records may be converted into `CorporateActionSecurityCoverage` objects.

The corporate-action blocker must not be cleared until all 993 securities satisfy
the locked final coverage contract.

## Maintenance and application-level errors

Open DART binary endpoints can return an XML application-error body with HTTP 200.
The transport therefore validates the documented ZIP payload shape before creating
a cache receipt or body. Cached non-ZIP responses also fail closed during replay.

A failed or maintenance response is not evidence and must not be promoted into the
acquisition manifest. Existing diagnostic copies may be quarantined outside the
active acquisition directory before a later clean recollection.
