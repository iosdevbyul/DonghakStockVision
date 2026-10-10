# Corporate-action semantic review contract

This offline layer validates explicit reviewer-authored decisions against the immutable
[document review index](corporate-action-document-review.md). It does not acquire DART
documents or run inference, signals, models, backtests, or price adjustments.

The evidence flow is:

```text
Acquired document bytes -> document review index
                        -> explicit semantic decisions
                        -> CorporateActionEventEvidence
                        -> future final per-security coverage review
```

Candidate terms, snippets, filing titles, issuer names, and date candidates are review
aids only. They never approve an event or its effective date. Price movement, current
DART mapping, and lifecycle boundaries are not semantic approval either.

## Reviewer decision input

The input is an unwrapped JSON object with exactly these top-level fields:

```json
{
  "version": "corporate_action_semantic_review_decisions_v1",
  "document_review_index_fingerprint": "<exact index fingerprint>",
  "records": [
    {
      "rcept_no": "20240701000001",
      "ticker": "005930",
      "raw_sha256": "<exact indexed document SHA-256>",
      "disposition": "CONFIRMED_EVENT",
      "review_reference": "reviewer-note-1",
      "events": [
        {
          "event_class": "split",
          "effective_date": "2024-08-09",
          "evidence_entry_name": "filing.xml",
          "evidence_entry_sha256": "<exact indexed archive-entry SHA-256>"
        }
      ]
    }
  ]
}
```

Angle-bracket values above are placeholders, not valid hashes. Each decision must
identify an existing `(rcept_no, ticker)` pair, using the exact KRX security code
(including valid alphanumeric codes such as `00088K`). The ticker must already be in
that document's `repository_tickers`; unlinked documents cannot acquire inferred
security links. Each pair may appear once. Unknown fields and duplicate JSON keys
are rejected. `review_reference` must contain non-whitespace text identifying the
reviewer's explicit conclusion. This contract checks evidence binding, not reviewer
authentication or the factual correctness of a human interpretation.

| Disposition | Events | Meaning |
| --- | --- | --- |
| `CONFIRMED_EVENT` | One or more | Explicit event decisions bound to indexed document entries |
| `NO_RELEVANT_EVENT_IN_DOCUMENT` | None | No relevant event found for this security in this specific document |
| `UNRESOLVED` | None | No approved conclusion; the pair remains pending |

Only a `REVIEW_INDEXED` document can support either concluded disposition. Unreadable
or non-text documents cannot be treated as reviewed no-event evidence. Existing
unreadable index records without ticker links remain unreviewed.

Each confirmed event requires an existing `EVENT_CLASSES` value, a real calendar date
in `YYYY-MM-DD` form, and the exact archive entry name and SHA-256. An explicit date
need not appear among heuristic `date_candidates`. Omitting it never invokes a
fallback. Multiple classes/dates per document are supported. Duplicate events with
the same `(rcept_no, ticker, event_class, effective_date)` are rejected, including
attempts to repeat the event under another attachment. Distinct document references
are preserved as separate evidence; this layer does not reconcile filings/corrections
or count unique economic actions across documents.

## Approved event conversion

`approved_event_evidence(wrapped_index, decisions)` returns immutable existing
`CorporateActionEventEvidence` instances for confirmed events only.
`build_semantic_review(wrapped_index, decisions)` emits a `FrozenJSON` artifact with
version `corporate_action_semantic_review_v1`. Both validate the same inputs.

The official source is constructed as
`https://opendart.fss.or.kr/api/document.xml`, with the indexed raw document SHA,
`document_reference = rcept_no`, and the reviewed entry name/SHA. The existing
`source_valid` contract applies. Reviewer-supplied URLs are not accepted. No network
request or document deserialization is performed. The review index's fingerprint is
validated; actual archive acquisition and byte verification belong to the upstream
index builder. A self-consistent hash is not independently an authenticity proof.

## Deterministic partial review

Missing records are allowed. Records sort by receipt and ticker; event decisions
sort by canonical semantic fields. Approved evidence sorts by ticker, effective date,
class, document reference, and event fingerprint. Input record/event order does not
change the output, normalized decisions fingerprint, or artifact fingerprint.

The exact upstream index fingerprint participates in the output identity. Changing
that index requires a new decision artifact pin even if only a heuristic changes.
There are no timestamps or local paths injected into semantic identity.

Aggregate definitions:

- `decision_record_count`: document/security decisions, including unresolved records.
- `reviewed_document_count`: distinct receipts with any decision, including unresolved.
- `confirmed_event_record_count`: confirmed document/security records.
- `approved_event_count`: converted explicit event evidence records.
- `no_relevant_event_document_count`: distinct receipts with at least one document-only
  no-event decision; this does **not** imply every linked security was reviewed.
- `unresolved_record_count`: explicitly unresolved pairs.
- `unreviewed_document_count`: receipts without any decision.
- `pending_document_security_pair_count`: indexed linked pairs missing a resolved
  decision, including explicit unresolved pairs. Unlinked unreadable documents are
  counted as unreviewed documents, not invented pairs.

The output always states:

```json
{
  "authoritative_no_event_semantics_verified": false,
  "coverage_records_approved": 0,
  "corporate_action_gate_verified": false,
  "historical_identity_verified": false,
  "prices_adjusted": false,
  "prediction_or_backtest_invoked": false
}
```

## Offline command

```bash
.venv/bin/python -m donghak_stock_vision.backtest.verify_corporate_action_semantic_review \
  --index /tmp/corporate-action-document-review.json \
  --decisions /path/to/reviewer-decisions.json \
  --output /tmp/corporate-action-semantic-review.json
```

`--index` accepts the `{ "artifact": ..., "fingerprint": ... }` wrapper emitted by
`review_corporate_action_documents`. `--decisions` uses the unwrapped schema above.
The output parent directory must exist; the output file must not exist. Exclusive
creation prevents overwriting an existing file, including a competing writer's file.
The command writes canonical JSON with a trailing newline and an artifact/fingerprint
wrapper. Standard output contains counts, gate state, and fingerprint only, never
full document text or review records. No credentials, `.env`, SQLite, DART API calls,
quarantined downloads, or real-data execution are needed.

## Independent final-coverage requirements

**A: No relevant event found in this reviewed document** is a document-scoped human
conclusion. **B: Authoritative verification that this security had no relevant
corporate action during the entire required period** requires complete official
query semantics, all required classes, and independently verified historical identity.
Only B can support final `VERIFIED_NO_RELEVANT_EVENT` coverage. This layer creates A,
never B, even if every candidate document is reviewed.

No `CorporateActionSecurityCoverage` records are created and
`authoritative_complete_semantics` is never promoted. The
[final coverage contract](corporate-action-final-coverage.md) retains its independent
whole-period historical identity, all-class query, effective-date, authoritative
no-event, and full 993-security requirements. Current mappings, issuer names,
security-code similarity, or lifecycle endpoints cannot establish whole-period
identity here. Runner-tail integration and run-input freeze readiness remain separate
later steps. The corporate-action gate remains closed in this task.
