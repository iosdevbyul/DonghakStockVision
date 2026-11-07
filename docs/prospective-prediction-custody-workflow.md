# Offline prospective prediction recording and custody rehearsal

This workflow connects original retained observation bytes to an existing frozen
prediction, append-only journal entry and source custody evidence. It is a
**synthetic offline rehearsal**, not independently authenticated forward prediction
performance. It makes no provider requests, uploads, broker calls or orders.

## Baseline and reused owners

The integration starts from PR #221, merged at
`77890ea2d517f09ca01c94fc0f03eff92af1742c`.

| Existing owner | Already implemented and reused | Integration added here |
| --- | --- | --- |
| `forward_capture` and `forward_signal.request_contract` | Strict plan/partition validation, pinned original manifest replay, authoritative response/raw receipt lineage | Verify every observation used by this prediction; reject incompatible scopes before recording |
| `forward_signal.generate_signal` | Original parser/ingestion replay, database SHA-256, observation IDs, age and local chronology checks | Freeze request, original strategy reference, parameters and signal policy before any journal append |
| `strategy.forward_reference` | Stable strategy ID/version, implementation SHA-256, bounded SMA parameters | Retain the exact reference; reject a later implementation or parameter substitution |
| `prospective_prediction` | Canonical prediction horizon policy, target/partition and clock checks, deterministic prediction ID and lifecycle | Bind its original policy/pin to the frozen workflow and original journal record |
| `forward_signal_journal` | v1/v2 immutable entries, hash chain, retained head checks, transactional append, duplicates and rollback | Validate custody first; call its existing `append_signal`, then read back the exact original entry |
| `prediction_binding.inspect_prediction` | Original request, policy, entry and prediction ownership plus semantic replay | Verify the recorded prediction against the same frozen workflow owners |
| `forward_raw_source_custody` (#221) | Raw ID from original capture lineage; original contract/receipt identity; optional supplied S3 observation and readback consistency | Bind one original response custody assessment to each exact observation, in original order |
| Existing CLI/demo | Synthetic original HTTP capture through `MockTransport`, existing provider parser, original journal | Add freeze/record/verify actions and one complete demo; share the original prediction fixture preparation |

There is no second journal, capture engine, S3 adapter, prediction representation,
signature stack or clock authority. Original journal entries and all historical
reports retain their schemas and pins. Existing v1/v2/v3 capture custody support is
unchanged. **v2/v3 operational captures remain ineligible prediction inputs** under
the original forward-signal guard; they are never relabelled as research owners.
Provider-backed prediction recording remains rejected by the existing
`authorize_horizon` gate. Supported recording here requires `offline_fixture`.

## Data lineage and frozen references

```text
Original pilot.sqlite3 / raw_pages + raw_capture_receipts
  -> pinned ForwardCapturePlan / manifest / response lineage
  -> generate_signal: original observed bars + observation IDs
  -> frozen workflow references:
       request pin + strategy ID/version/code SHA + parameters + signal policy
       prediction horizon policy pin + per-observation custody assessment/pin
  -> append_signal(..., prediction_policy=original policy)
  -> existing forward_signal_entry_v2 / forward_prediction_evidence_v1
  -> inspect_prediction + original source/custody replay
  -> prospective_prediction_custody_verification_v1
```

`prospective_prediction_custody_workflow_v1` is a separately retained reference
bundle. It records the exact original request/policy pins, strategy reference,
parameters, signal policy and database pin. Each ordered observation ID is bound
to its complete original raw-source assessment and pin. The response sequence is
resolved from the **verified** observation receipt's capture/sequence run identity
and rechecked against the original journal/manifest by #221. Full receipt, raw ID,
capture ID, manifest pin and plan pin must match the observation. Nothing fills in
missing original evidence.

Recording reconstructs the frozen references before opening the journal for
writing. A strategy code change, parameter change, horizon change, missing source,
reordered observation or substituted custody owner cannot silently change a
previously frozen workflow. The existing request pin fixes parameter identity;
there is no invented parameter-version contract. The existing horizon policy
version is `forward_prediction_policy_v1`; it remains distinct from the later
`prospective_evaluation_policy_v1` evaluation policy.

After the original atomic append, `inspect_prediction` replays the journal and
captures. Its original `PredictionSource` binds the request, entry, prediction ID,
policy and retained head. The final report also replays the raw-source custody
references, including any separately supplied external readback evidence.
Verification reconstructs the full report from these owners; a rehashed summary
alone cannot establish integrity.

## Executable rehearsal

Install the existing optional verification dependencies and use fresh private
paths. The demo creates only new synthetic evidence; it refuses to reuse an
existing capture root or journal. Weekday choices are synthetic fixture dates,
not verified KRX calendar assertions.

```sh
python -m pip install -e '.[api,dev]'
dsv forward-signal offline-prediction-custody-demo \
  --capture-root /tmp/dsv-prediction-originals \
  --journal /tmp/dsv-prediction-journal.sqlite3 \
  --archive-namespace synthetic-prediction-rehearsal \
  > /tmp/dsv-prediction-result.json
```

The demo reuses the existing collector with **HTTP MockTransport**, parser and
prediction fixture. HTTP request log messages describe mock requests, not network
traffic. No provider/AWS credentials are needed. It retains `signal-request.json`,
`prediction-policy.json` and `prediction-custody-workflow.json` beside the new
original archive, flushes the workflow file/directory before append, records one
prediction, detects a duplicate and semantically verifies the resulting report.
The journal must be outside the original capture directory.

Expected results: four original observations with verified local raw bytes,
manifest and receipt identities; one `forward_signal_entry_v2`; a BUY/up prediction
for the fixed fixture closes `(100, 90, 80, 120)`; `duplicate_detected=true`; a
pending record-time lifecycle with no outcome. Every trust/performance/trading
approval remains false and `performance_metrics` remains null.

Fresh fixture creation uses current **local** timestamps, so independent fresh
runs have different original pins. Given the same retained originals, workflow,
journal head/entry/ID, policy and as-of instant, verification is deterministic.
Duplicate recording preserves the original recording time and returns the same
report when the journal head is unchanged, even after the original horizon.

Read the retained owners back in a separate process and verify the exact report:

```sh
python - <<'PY'
import json
import subprocess
from pathlib import Path

result = json.loads(Path('/tmp/dsv-prediction-result.json').read_text())
report = Path('/tmp/dsv-prediction-report.json')
report.write_text(json.dumps(result['report']))
subprocess.run([
    'dsv', 'forward-signal', 'prediction-custody-verify',
    '--capture-root', '/tmp/dsv-prediction-originals',
    '--request', result['request_path'], '--request-pin', result['request_pin'],
    '--prediction-policy', result['prediction_policy_path'],
    '--prediction-policy-pin', result['prediction_policy_pin'],
    '--workflow', result['workflow_path'], '--workflow-pin', result['workflow_pin'],
    '--journal', '/tmp/dsv-prediction-journal.sqlite3',
    '--journal-pin', result['journal_head_pin'], '--entry-pin', result['entry_pin'],
    '--prediction-id', result['prediction_id'],
    '--report', str(report), '--report-pin', result['report_pin'],
], check=True)
PY
```

Expected output includes `locally_verified=true` and
`independent_custody_verified=false`. Keep pins independently of mutable report
files. Pins emitted by this local command are references to retain, not proof that
an independent custodian ever possessed them.

### Using already retained original fixtures

The same CLI exposes the stages separately. Supply original request/policy files
and their separately retained pins. `prediction-custody-freeze` is read-only:

```sh
dsv forward-signal prediction-custody-freeze \
  --capture-root "$capture_root" \
  --request "$request_file" --request-pin "$request_pin" \
  --prediction-policy "$policy_file" --prediction-policy-pin "$policy_pin" \
  --archive-namespace "$archive_namespace" > frozen-result.json
```

Retain the `workflow` object in its own JSON file and its `workflow_pin` separately.
Use those exact files/pins to record, with the existing journal's trusted head
(`forward_signal_journal.GENESIS` for a new journal):

```sh
dsv forward-signal prediction-custody-record \
  --capture-root "$capture_root" \
  --request "$request_file" --request-pin "$request_pin" \
  --prediction-policy "$policy_file" --prediction-policy-pin "$policy_pin" \
  --workflow "$workflow_file" --workflow-pin "$workflow_pin" \
  --journal "$journal_file" --journal-pin "$retained_head" > recording-result.json
```

CLI recording uses the current local clock; the canonical original policy checks
its recording delay and future target. It has no backdating override. The Python
API takes an explicit fixture `recorded_at` for deterministic offline tests,
without claiming authentication. Read-only `prediction-custody-verify` always
uses the original journal's verified recording instant to reconstruct the
record-time report. The Python `verify_workflow` can separately project the
original lifecycle at a canonical later `as_of`; it never resolves an outcome or
computes performance.

## Failure, duplicate and recovery behavior

Invalid supplied evidence raises the existing validator error and the CLI exits
with status **2**, without emitting a completion report. A successful report's
`rejected_evidence=[]` means no rejected evidence was admitted; it is not a claim
that invalid evidence can be ignored. Missing external evidence remains explicitly
listed in source custody reason codes and top-level independent-trust gaps.

| Condition | Executable behavior |
| --- | --- |
| Identical request, policy and journal head | Existing duplicate detection returns the original entry/time; no new row |
| Same signal with different horizon, or a legacy signal being promoted | Existing journal rejects `same_signal_id_different_prediction_contract`; original rows remain intact |
| Frozen strategy, parameters, policy, custody row or ownership differs | `workflow_frozen_inputs_mismatch` or the stricter original validator failure before append |
| Modified/missing original BLOB, receipt, manifest or ingestion time | Original database pin, capture, receipt or chronology verification rejects; rehashing the database does not repair its evidence |
| Interrupted journal insert before commit | Existing transaction rolls back; empty committed journal can replay from GENESIS |
| Interrupted initialization leaves an empty/invalid file | Existing application-ID/schema guard rejects; never automatically rebuild or delete the file |
| Commit succeeds but response is lost | Original complete entry survives. A stale head is rejected. Retry only with an independently retained current head; identical replay then returns the original duplicate |
| Source changes or verification is interrupted after commit | No completed E2E report is published. The complete original journal entry remains append-only and cannot pass source replay until original evidence is available |
| Journal edit/truncation or forged entry/ID/time | Retained head/entry pins, chain checks, prediction reconstruction or semantic replay reject |
| Rehashed completion report | Reconstruction from separately retained owners rejects semantic substitution |

There is no automatic head discovery, evidence repair or overwriting recovery
path. Loss of the trusted current head is an unresolved evidence dependency,
not permission to trust the last SQLite row. The original journal is the atomic
recording boundary; the frozen reference file, capture archive and report are
separate artifacts. They do not form one distributed transaction. A report is
published only after complete readback verification. Journal verification also
rechecks the retained head after source reads; it does not promise an atomic
snapshot spanning all files or immunity from later mutation. Use quiescent
retained originals.

## Local integrity and external evidence

The report distinguishes original local byte/manifest/receipt checks, local
prediction journal integrity, source-to-prediction linkage, and supplied external
claims. It never treats local hash chains or system timestamps as historical
authority. The existing signal decision's `eligibility.eligible` continues to mean
input eligibility for that synthetic reference calculation, not genuine-forward,
PIT, performance or execution approval.

The Python `WorkflowInputs.supplied` tuple can contain the existing #221
`SuppliedRawSourceEvidence` for each observation, in the exact original order.
Use the same evidence when freezing and verifying; wrong object/source/version,
readback bytes, missing pins or changed retained references reject through the
original validators. Consistent bytes yield only
`content_consistent_unverified`. CLI rehearsal uses local evidence only and has no
external upload or receipt-minting option. The complete workflow bundle must be
retained; its namespace is the existing operator label, not authenticated archive
metadata. See [raw-source custody operations](forward-capture-custody-operations.md)
for the exact source contract and separately retained object reference format.

Original HTTP capture receipts concern original response bytes. They are **not
prediction custody receipts**. Existing independently supplied prediction receipt
verification and #217 governance require their original artifact owners, issuer,
keys and chronological evidence; this workflow neither fabricates these nor
converts a horizon policy into an evaluation-policy artifact. Its top-level
`external_prediction_custody_not_supplied` remains explicit.

Retain original `pilot.sqlite3`, request, horizon policy, frozen workflow bundle,
source/object/readback evidence where supplied, existing prediction journal,
head/entry/prediction IDs, report and all separately retained pins. Do not replace
missing evidence with reconstructed JSON or body bytes. Original source size,
response count, request/entry/journal limits remain enforced. New workflow and
report JSON are bounded to 2 MiB; raw response bodies are never embedded or cached
by this integration.

Unresolved independent requirements include policy predeclaration time,
prediction existence time, original publication time, external prediction custody,
independent issuer/custodian authority, PIT qualification and evaluation approval.
No genuine-forward eligibility, performance, trading or execution approval is
established. Original #217 governance and #218 cohort logic are unchanged.

## Validation commands

```sh
ruff check src tests
ruff format --check src tests
mypy
pytest -q tests/integration/test_prospective_prediction_workflow.py \
  tests/integration/test_prospective_prediction.py \
  tests/integration/test_prediction_binding.py \
  tests/integration/test_forward_signal.py \
  tests/integration/test_forward_custody_versions.py \
  tests/integration/test_forward_raw_source_custody.py
python -m build
```

Offline pytest's existing autouse network guard blocks TCP connections. E2E tests
also forbid provider send, AWS-session creation and broker submission while
running successful verification through real cryptographic and semantic checks.
Installed-package validation runs the same workflow tests from outside `src` and
checks the imported module comes from the installed wheel. Required GitHub Python
3.12/3.13/3.14 checks remain mandatory before protected Squash Auto-merge.
