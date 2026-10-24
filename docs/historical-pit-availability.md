# Historical PIT availability evidence

The actual inspected Korean daily observations have **zero historically
PIT-qualified cases**. This workflow verifies original source lineage and retains
bounded timestamp claims, detects substitutions and chronology defects, and reports
why authentic decision-time availability remains unavailable. It grants no approval.

## Actual existing evidence and authorities

PR #137's read-only KRX archive replay and KIND session reconstruction remain
unchanged. The original Market DB has 993 symbols and 1,099,255 latest daily bars,
with local raw pages and bar-source links. The real file lacks the current
`raw_capture_receipts` table. Raw retrievals were recorded in 2026 for historical
trade dates, so they cannot establish identical content's availability in 2024.
There is no archived response publication header, exchange-issued publication time,
authenticated historical revision ledger, or independently verified request/response
custody chain for the inspected observations.

Existing systems have narrower semantics:

- `capture_evidence_sink._receipt_bytes` reconstructs canonical local receipt bytes
  only from an existing capture row whose metadata/raw body hash agree. It creates
  no legacy receipt, export file or independent attestation in this workflow.
- `inspect_custody_acknowledgement` can verify an independent byte pin, expected
  custodian/raw identity and receipt digest. Even a consistent acknowledgement
  returns `custodian_authenticated=False`, `independent_custody_verified=False`
  and no inference authority.
- Existing S3 version/readback/retention and deployment-readiness contracts preserve
  the same authentication boundary. Local saved S3 inventory concerns synthetic
  development archives, not authentic historical KRX publication. No remote S3 or
  provider operation is performed here.
- Existing PIT provenance/revision-ledger reviews likewise distinguish structural
  consistency from authenticated issuer evidence and explicitly withhold historical
  availability/inference authorization. A daily-bar claim is not a feature-input
  revision ledger; the new code does not fabricate compatible feature snapshots.
- Existing baseline, corporate-action, PIT/OOS and operational gates are unchanged.
  None of these local timestamp claims supplies their missing authority. Corporate
  Actions coverage, Runner Tail and Run Input Freeze blockers are not cleared.

A hash authenticates neither a publisher nor a historical clock. Local source
names, URLs, file paths, mtimes, a self-signed key, a retrospective page, or an
operator saying a receipt is independent are not a new trust root. An appropriate
approved issuer protocol/verifier is absent; no positive authenticator or approval
flag is invented for this PR.

## Timestamp meanings

The assessment keeps these separate for every scoped observation:

| Field | What it could establish | Current evidence |
|---|---|---|
| `session_date` | Original daily date and calendar membership | Original bar and pinned original KIND calendar |
| `exchange_session_at` | Exchange session instant | Unavailable: a date is not an instant |
| `source_publication_at` | Authenticated publication of exact content | Unavailable |
| `source_correction_at` | Authenticated correction/revision chronology | Unavailable |
| `actual_first_observed_at` | Proven first observation, not merely one retained retrieval | Unavailable |
| `provider_ingestion_at` | Provider's internal ingestion event | Unavailable |
| `locally_retained_retrievals` | Client-recorded retrieval values and exact source field | Preserved `raw_pages.collected_at` values |
| `original_artifact_capture_at` | Independently established artifact capture event | Unavailable |
| `independently_authenticated_at` | Timestamp validated against an appropriate external authority | Unavailable |
| `evidence_available_at` | Proven availability of the evidence itself | Unavailable |
| `strategy_safe_available_at` | Qualified availability applicable to a decision | Unavailable |

PR #137's projection calls its copied `DailyBar.collected_at` value `ingested_at`.
This additive assessment explicitly treats the originating field as a **locally
recorded retrieval**. It does not reinterpret it as provider ingestion or first
observation. A current local receipt copies that client timestamp; it is not a
separate trusted artifact-capture clock. Multiple retained retrievals are listed,
never minimized into a purported first-observed time.

No close-time, midnight, next-session, file-mtime, current-time or adjusted-price
assumption is used to fill a missing timestamp.

## Versioned claim and pin contracts

`HistoricalAvailabilityClaim` / `historical_availability_claim_v1` freezes:

- Full exchange-qualified instrument, session date, exact original bar hash.
- Raw archive ID and content hash, with exact local document identity
  `raw_pages/<id>/sha256/<hash>`; this is not an invented exchange document number.
- Declared publisher/custodian, timestamp kinds and origin references.
- Bounded claimed revision sequence, source/predecessor hashes and optional times.
- Capture and trust-root references, explicitly declarations rather than authority.

The immutable timestamp kinds distinguish publication, correction, first observation,
provider ingestion, local retrieval, artifact capture and an asserted authenticated
timestamp. Even the last kind is a **claim**, not a verified conclusion.

`PinnedAvailabilityClaim` retains the original claim owner and independent document
pin, expected publisher/custodian, and optional original acknowledgement/export
bytes with separate independent pins. These bytes are retained in the assessment
owner, not fetched from a caller-controlled path. A receipt must equal the canonical
original capture receipt from the pinned archive, and the existing acknowledgement
review must find its content consistent. It still proves no historical availability.

`availability_manifest(owners)` builds a deterministic input manifest. Execution and
verification require its externally retained trusted hash as an independent argument;
they never obtain the expected hash from the report under review. Record the pin
before accepting feedback. Rehashing newly supplied data alone does not authenticate
it, and even matching claim/document/manifest pins never upgrade authenticity.

Bounds: 256 evidence owners, four per observation, seven timestamp kinds and eight
revision claims per owner, 64 KiB per acknowledgement/export, 512 KiB total claim and
receipt input, and 2 MiB output report. Existing PR #137's 64-row, window and raw-page
bounds remain authoritative. No unbounded archive search or parallel worker exists.

## Original-source and receipt verification

`assess_historical_pit` first checks the exact original read-scope owner, partition,
window and scope pin, before file or receipt access. Only original training/tuning
ranges are allowed. It then invokes PR #137's original DB/raw/parser/calendar path,
without changing its schema or conclusion.

Every supplied evidence owner must bind an original projected bar, matching date,
instrument, raw ID and source hash. Wrong same-date instruments, invented document
identities, different original contents, foreign observations and substituted issuer
IDs fail closed. Original receipt lookup is limited to already verified scoped raw
IDs; metadata is checked again before reading receipt source bytes. No missing
capture row is synthesized retrospectively. Stable offline files are required;
journals and changed original file hashes are rejected. This is not concurrent
transactional capture or hard process isolation.

Claims check predecessor links, ordered sequences, final original hash and declared
chronology. Conflicting claims for the same publisher/document/timestamp kind are
rejected, rather than choosing the most favorable or earliest value. A consistent
claimed chain is still not authenticated revision evidence. Unknown historical
versions/timestamps remain missing.

## Assessment and coverage

`historical_pit_availability_assessment_v1` binds the original scope/DB/readiness
hashes, calendar pins, independent evidence manifest, unchanged original approval
state, per-observation timestamp inventory, retained claim references, custody
review, exact missing categories and report fingerprint.

Coverage reports total scoped observations, verified original lineage, authentic
publication evidence, authenticated revision evidence, established decision-time
availability and PIT-unqualified observations. Malformed/unprojectable original rows
remain in the denominator and have an explicit count/reason; they do not disappear
because a valid observation projection could not be built. Source-lineage counts
are distinct from calendar validity and PIT qualification. No unavailable time is zero.

The implemented authority boundary currently makes every strong availability count
zero. Local provenance/receipt consistency can be positive. This accurately reports
the absent approved timestamp authenticator rather than generating synthetic success.

## Actual real-data scope and result

[Committed metadata](evidence/historical-pit-availability.json) references the original
PR #137 readiness artifact, exact existing split and source/calendar pins. It inspects
only KOSPI/KRW `005930`, 2024-06-24 through 2024-06-28, within the original validation
partition represented as tuning for read-only inspection. No final-test/holdout row
is loaded in this workflow.

The five observations have five locally verified original lineages, no authentic
publication evidence, no authenticated revisions, no established decision-time
availability, and **five PIT-unqualified observations**. The existing Market DB has
no original capture receipt rows/table for this scope. All strong timestamp fields
remain null; local client retrieval values are preserved separately. The metadata
artifact contains no raw response, OHLC price projection or fabricated timestamp.

## Independent replay and historical strategy gate

`verify_historical_pit` independently reruns the original source/parser/calendar and
receipt/evidence verification using the caller's original owners and externally
trusted input pins, then compares the complete report and retained owners. A
self-rehashed altered timestamp, report, source mapping, issuer, coverage value or
approval claim cannot pass. No cache or report-only validation substitutes for source
reconstruction.

`historical_pit_execution_gate` performs this verification and returns a separate
versioned **blocked** result with PIT and original readiness blockers. It starts no
strategy, model, Paper Broker, order or fill. It does not alter PR #137's blocked gate,
synthetic Strategy Execution, Frozen OOS, valuation, improvement or public APIs/CLI.
Even future authentic availability would not by itself satisfy corporate-action,
instrument identity, calendar, strategy, OOS or operational approvals.

```python
# scope/scope_pin and calendar owners use original independently reviewed metadata.
# owners contain original explicitly unverified claims and separately retained pins.
from donghak_stock_vision.storage.historical_pit import (
    assess_historical_pit, verify_historical_pit, historical_pit_execution_gate,
)

result = assess_historical_pit(db, scope, scope_pin, owners, evidence_manifest_pin, calendars)
report_pin = result.report.to_dict()["content_fingerprint"]
verify_historical_pit(result, report_pin, db, scope, scope_pin,
                      owners, evidence_manifest_pin, calendars)
gate = historical_pit_execution_gate(result, report_pin, db, scope, scope_pin,
                                     owners, evidence_manifest_pin, calendars)
assert gate.to_dict()["status"] == "blocked"
```

## Follow-up prerequisites

Obtain original exchange/provider publication and revision records or independently
authenticated capture/receipt evidence covering exact historical content. Specify
an independently approved issuer/custodian trust protocol and timestamp scope;
validate revision completeness and evidence availability relative to decisions.
Retrospective retrieval alone cannot recover that missing history. Then separately
review corporate actions, historical identity, sessions, strategy eligibility and
untouched OOS/holdout requirements before designing historical execution approval.
No profitability, historical simulation success, forward-paper stage or live
brokerage readiness is demonstrated by this foundation.
