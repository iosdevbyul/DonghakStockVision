# Operational capture and prospective evidence readiness

`prospective_operational_evidence_readiness_v1` (research capture) and
`prospective_operational_evidence_readiness_v2` (operational v2/v3 capture) are
read-only gap assessments.
It connects existing original-owner verifiers without creating a capture engine,
conversion pipeline, journal, database, trust registry, or operational approval.
It calculates no predictive performance. Every report remains `unresolved`.

## Capability matrix

| Boundary | Existing implementation and verification | Assessment and remaining requirements |
| --- | --- | --- |
| Original market capture | `forward_capture.verify_capture` reconstructs the sealed manifest, original raw receipts, source identity, parser results and applicable ingestion lineage. Capture plans enforce existing scope and protected-partition restrictions. | Locally verifiable. A declared `authorized_provider` mode alone does not authenticate origin, permission or historical publication. |
| Evidence persistence | Existing archive and receipt storage preserve original response bytes. `operational_capture.verify_stored_capture` separately verifies an operational SQLite batch against its original source and authorization owners. | Implemented; this bridge does not receive operational store/authorization owners and does not certify database completion. No ingestion or recovery import occurs. |
| Prediction-time provenance | `audit_custody` replays `EvaluationSources`, original journal entries, frozen policy/horizon, signal inputs and future outcome bindings. | Locally replayable **offline fixtures only**. Actual prospective prediction authorization and independent pre-existence evidence remain unavailable. |
| Original capture-to-prediction binding | Exact archive identity plus plan pin, capture pin and attempt are matched to the original frozen request; the selected response must be its unique parsed observation. | Structurally verified for compatible original offline owners. Matching a ticker or date is insufficient. |
| Custody export and local readback | `forward_custody.assess_forward_operations` verifies existing export/outbox linkage and locally supplied readback bytes using existing validators. | Locally verifiable; no submission, staging, retry or external availability check is performed here. |
| External custody acknowledgement | Original `ReturnedCustodyEvidence` bytes and retained pins are inspected by existing acknowledgement/readback validators. | Content consistency only. A missing/invalid acknowledgement remains explicit. Even matching acknowledgements do not authenticate custodian identity or clock. |
| Future observation provenance | Existing `bind_outcome` verifies the exact frozen target, gate, deadline, cohort and original source when available; pending/unavailable/invalid cases remain in the cohort. | Offline local verification only. Historical backfills cannot be relabelled genuine prospective evidence. |
| Issuer/key governance | Optional existing `AnchorSnapshot` is replayed through `review_anchor`, checking issuer/key identity, encoding, declared validity/status and pinned revoked-receipt declarations. | Configured consistency only. Independent issuer/key custody, historical authority, revocation completeness and clock assurance remain unverified. |
| Receipt signatures | Existing external receipt and cohort reconcilers verify supported Ed25519 assertions against exact original policy/prediction owners. | Implemented separately; this bridge does not accept receipt-summary JSON or convert capture acknowledgements to signed prospective receipts. Valid signatures alone cannot close the independent custody/time gaps. |
| Genuine prospective evaluation | Consumer/result contracts explicitly prohibit unsupported metrics and approvals. | Not established. Requires independently verifiable predeclaration, prediction existence, appropriate source rights, original source binding and authenticated external chronology. |

Existing service approvals are not denied by this report. The assessment has no
provider-authorization owner argument and cannot independently verify those
approvals. It never inspects credentials or the environment. An inherited
collection-readiness `missing_krx_api_key` reason is represented as
`credential_presence_not_assessed`, not a claim that an operator lacks a key.
Configured reference strings and operator claims are not substitute
proof. Supply original authorization owners to the existing operational verifier
in a separately authorized workflow; do not use this bridge as permission to collect.

## Supported interface and scope

Module: `donghak_stock_vision.storage.prospective_operational_readiness`.

- `CaptureSource` only groups references to the existing archive, original
  `ForwardCapturePlan`, plan pin, attempt, manifest pin and response sequence.
- `assess_operational_evidence` takes one such capture, an explicit aware `as_of`,
  original `ForwardOperationsConfig` plus its separately retained pin, and optional
  existing `ReturnedCustodyEvidence`, `EvaluationSources` plus its retained source
  references pin, and `AnchorSnapshot` plus expected issuer/key identities.
- No caller approval, trusted-status boolean, credential, policy-summary JSON or
  already-computed readiness report can authorize a successful finding.
- `verify_operational_evidence` additionally requires independently retained report
  and reference pins and reconstructs the report from those same original owners.
  A modified report with a new self-computed hash still fails semantic verification.
- No new CLI or public HTTP endpoint is introduced. This is a small programmatic
  consumer of the existing storage contracts, not another operator control plane.

Example using original objects already loaded by an offline operator application:

```python
from donghak_stock_vision.storage.prospective_operational_readiness import (
    CaptureSource, assess_operational_evidence, verify_operational_evidence,
)

capture = CaptureSource(
    archive_root, original_plan, retained_plan_pin,
    original_attempt, retained_capture_pin,
)
report = assess_operational_evidence(
    capture, as_of, original_operations_config, retained_config_pin,
    source=original_evaluation_sources,
    source_references_pin=retained_evaluation_references_pin,
)
# Retain pins separately from mutable report files; this is local integrity,
# not independently authenticated publication or timestamping.
report_pin = report.identifier
references_pin = report.to_dict()["references_pin"]
verify_operational_evidence(
    report, report_pin, references_pin,
    capture, as_of, original_operations_config, retained_config_pin,
    source=original_evaluation_sources,
    source_references_pin=retained_evaluation_references_pin,
)
print(report.payload_json)
```

Omit both `source` arguments when original prediction owners are unavailable.
The report then has zero declared predictions and explicit missing provenance;
this does not describe an empty approved evaluation cohort. When owners are
supplied, the consumer requires the full predeclared cohort, at most 32 cases,
with its original policy, cohort and frozen horizon pins. Each case retains its
prediction ID, original entry/request pins, target, gate, deadline and future
state. The capture must be an exact original prediction input for at least one
case; other unlinked cases remain visible. No case is silently dropped.

For an executable synthetic demonstration and tamper checks, run:

```sh
PYTHONPATH=src:. python -m pytest -q tests/integration/test_prospective_operational_readiness.py
```

These fixtures use existing capture, staging, journal and future observation
interfaces. Mock provider transport and synthetic authorization documents test
scope compatibility without proving real-provider origin or permission. No test
calls a provider or substitutes fixture data into a real evidence archive.

## Version-aware custody and retained reports

Capture v1 remains a research partition/split owner; v2 is a prospective read-only
observation scope and v3 is an explicitly allowed full-market collection scope.
The bridge now invokes the existing version-aware custody validator for all three.
It never fabricates research fields. See the before/after matrix in
[Forward capture custody operations](forward-capture-custody-operations.md#version-aware-operational-scope-compatibility-pr-220).

For v2/v3, new bridge reports explicitly use
`prospective_operational_evidence_readiness_v2`. Contract compatibility means the
original capture and scope passed the applicable custody path; it does **not**
imply that an export or returned acknowledgement/readback exists or verifies.
Those facts remain separately reported. Missing original exports cannot become
successful custody findings merely because the schema is supported. Original
capture, policy, prediction, horizon and trusted-reference identities are retained.

Research v1 reports and canonical pins are unchanged. Independently pinned original
#219 v1 reports for v2/v3 can still be reconstructed with their historical
unsupported-scope finding and null custody results. This compatibility branch is
private to semantic verification; the public assessor cannot request the old path.
Changing only a new report's version to v1 fails semantic reconstruction. No past
report or retained pin is rewritten or reinterpreted as a successful custody check.

Prospective prediction owners remain restricted to `offline_fixture`, their
original research windows and predeclared horizons. Supplying a v2/v3 operational
capture as a prediction input still fails
`operational_scope_cannot_map_to_offline_prediction_contract` before that archive
is read. Valid operational custody is not research authorization or genuine future
observation provenance. Separately, operational SQLite completion verification
still requires its original authorization owners; persistence remains unassessed
by this bridge.

## Time, authenticity and source distinctions

- Trading date is the source's session date, not publication time.
- Request start, response completion and receipt collection times are original
  local-clock fields. They are emitted in UTC and never authenticated by a hash.
- Capture manifest v1/v2 does not bind ingestion completion time. The standalone
  capture field is null with an explicit reason. Supplied prospective owners
  separately replay the pinned database ingestion audit; future timestamps, when
  available, retain their existing local-only assurance in per-case findings.
- A content-consistent original acknowledgement may supply an issuer-asserted
  `acceptedAt`. It remains unauthenticated, even with a retained content pin.
  An asserted acceptance before collection or after `as_of` adds a chronology
  inconsistency reason; it does not become an independent clock observation.
- Publication and independently authenticated time remain null. Later retrieval,
  Git dates, local files and today's key configuration cannot prove historical
  publication, original prediction creation time or historical signing authority.
- `origin` is the original manifest's declared capture mode, accompanied by
  `origin_assurance=declared_capture_mode_not_independently_authenticated`.
  It is never a certification of genuine provider or prospective evidence.

A retrospective source with a matching date is not a future observation owner.
Only the original outcome-binding verifier may inspect a supplied future owner,
under its frozen gate, deadline and research restrictions. Missing, pending or
invalid future provenance remains explicit. No historical database fallback is used.

## Bounds and consistency

One capture and one response are assessed. Existing archive/plan/receipt bounds
apply (including the 256 MiB archive limit). Returned receipt/readback bodies are
each limited to 64 KiB. Config and optional anchor owners use existing strict
schemas and pins. Evaluation replay preserves its 32-case, 256 KiB per-request
and 2 MiB aggregate request bounds. Final canonical output is capped at 256 KiB;
oversized reports fail closed instead of truncating cases or gaps.

Capture/custody and optional prospective replay are checked again at the end of
an invocation. This detects persistent observable changes across verification
boundaries. It is not an atomic snapshot across files and does not detect every
transient change subsequently restored. Operators must retain immutable source
snapshots for reproducible review. There is no global/mutable trust cache. The
optimized cohort reconciler and existing v1 schemas are unchanged.

Malformed owners, invalid original pins, changed authoritative artifacts,
unsupported versions, protected partitions and incompatible source identities
raise existing errors or bridge-specific deterministic errors. Missing optional
owners and unverified independent evidence become explicit report reason codes.
No broad exception suppression converts an integrity failure into missing evidence.

## Next action based on observed gaps

A local replay or even 100% signature verification is insufficient. Before any
future genuine evaluation adapter is considered, obtain externally verifiable
policy predeclaration and prediction existence receipts, authenticated issuer/key
custody, historical key/revocation evidence and clock assurance. Bind each to
exact original artifacts, not summaries. Preserve original market-source rights,
source bytes, custody acknowledgements and independently retained readback pins.

The structural custody-scope compatibility gap is resolved, while original
external custody and time-authentication requirements remain unresolved.
A separate prospective owner contract and independent evidence review would be
needed for genuine provider predictions; loosening `offline_fixture` is not a fix.
No external evidence requirement is completed merely by this adapter.

All genuine forward, PIT, performance approval and execution findings remain
false; metrics remain null. No network, provider, broker, paid service, source
write, strategy evaluation or operational activation is performed.
