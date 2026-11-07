# Forward capture operational readiness and offline custody staging

## Integration and scope

Base main `0e188cef768462aba5392c837b4402f11985fe2c` contains merged PR #139.
The integration is deliberately restricted to the existing local custody staging
boundary. No collector, custody store, transport factory or approval authority is
added. No provider, STS, AWS/S3, external custodian or brokerage call was performed.

Reused components:

* PR #139's original plan, split authorization, pinned manifest verification,
  raw bytes, ingestion receipts, private pilot archive and exclusive writer lock.
* `export_capture_receipt` / `verify_exported_capture_receipt`: exact original
  `raw_capture_receipt_v1` JSON bytes, exclusive 0600 export, file/directory fsync.
* `prepare_custody_submission_identity`: original receipt digest and deterministic
  idempotency key.
* `enqueue_capture_receipt`: original idempotent outbox, without processing.
* `verify_custody_readback`: original content comparison, using only supplied local
  bytes; `inspect_custody_acknowledgement`: original explicit acknowledgement checks.

The established custody artifact is the **JSON receipt**, not a replacement archive
of the HTTP body. The existing S3 adapter also uploads this receipt only. The
response body remains in the original `pilot.sqlite3/raw_pages`; the manifest and
binding pin preserve source linkage. External custody of the complete HTTP bytes
and manifest is **not implemented or implied**. No existing custody contract is
weakened to upload another payload under the receipt contract.

## Exact readiness checks and trust levels

`forward_capture_operational_readiness_v1` (research scope) and
`forward_capture_operational_readiness_v2` (operational observation/full-market
scope) are canonical immutable reports of a particular inspection. It evaluates original provider authorization and terms
references, credential **presence only**, explicit date/symbol/market/currency,
original split pin for v1 or original operational scope for v2/v3, separately
supplied plan/capture pins, bounded request/retry/
byte/time/daily budgets, private storage permissions, archive byte ceiling,
original capture/receipt/parser/ingestion consistency, exported receipt, outbox,
local receipt readback and any explicitly supplied external claims.

`ForwardOperationsConfig` (`forward_capture_operations_config_v1`) optionally
records a reviewed minimum permitted interval and its reference, an approved
custody endpoint reference/custodian identity, retention reference and backup
reference. Each is an **operator configuration assertion**, not independently
verified evidence. In particular:

* `credential_present` does not prove a valid key or service entitlement.
* `frequency_locally_compatible` only compares the configured limit to the original
  capture interval; it does not authenticate the provider's frequency permission.
* An endpoint reference is never used as a URL or an SDK/bucket selection.
* Receipt byte consistency proves local source/export linkage. It does not
  authenticate a custodian, exchange publication, clock or historical availability.
* Retention/backup references remain configured-only. There is no restore drill,
  independently verified retention evidence or WORM claim in this workflow.

The overall status remains
`blocked_for_provider_collection_and_independent_custody`; explicit blockers
preserve missing/unverified provider, frequency, custody, receipt/readback,
retention and backup requirements. `offline_staging_ready` is narrower: it checks
whether verified capture evidence can safely be staged locally, without granting
any external permission. Successful CLI inspection exits 0 even when its report
lists blockers; configuration/integrity errors exit 2 through the original CLI.

Private staging requires the pilot root/export directory and archive/export files
to have no group/other permission bits and no symlinks at the checked paths.
The original archive has a 256 MiB ceiling, at most 32 captures and 3 responses per
capture. Receipt and supplied evidence files are bounded to 64 KiB. Existing
original plan/split checks reject OOS/final-test/final-holdout before source I/O.
Basic capture CLI behavior is unchanged. Custody operations use the existing
`[api]` extra for the original Pydantic acknowledgement contract; basic CLI parsing
still works without optional API/custody/learning packages.

## Runnable complete offline workflow

These commands use the actual deterministic MockTransport pilot, not KRX. Run in
an environment with the project's existing API extra installed:

```sh
pilot=$(mktemp -d /tmp/dsv-custody.XXXXXX)
dsv forward-capture offline-pilot --root "$pilot" > "$pilot/capture-result.json"
python - "$pilot" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
result = json.loads((root / 'capture-result.json').read_text())
(root / 'plan.json').write_text(json.dumps(result['manifest']['plan']))
(root / 'plan.pin').write_text(result['plan_pin'])
(root / 'capture.pin').write_text(result['capture_pin'])
PY
common=(--root "$pilot" --plan "$pilot/plan.json" --plan-pin "$(cat "$pilot/plan.pin")"
        --attempt pilot-1 --pin "$(cat "$pilot/capture.pin")" --sequence 0)
dsv forward-capture custody-readiness "${common[@]}" > "$pilot/before.json"
dsv forward-capture custody-stage "${common[@]}" > "$pilot/staged.json"
python - "$pilot" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
staged = json.loads((root / 'staged.json').read_text())
(root / 'binding.json').write_text(json.dumps(staged['binding']))
(root / 'binding.pin').write_text(staged['binding_pin'])
PY
dsv forward-capture custody-verify "${common[@]}" \
  --binding "$pilot/binding.json" --binding-pin "$(cat "$pilot/binding.pin")"
dsv forward-capture custody-status "${common[@]}" > "$pilot/status.json"
dsv forward-capture custody-readback "${common[@]}" > "$pilot/readback.json"
```

Observed result: one synthetic 2025-07-01 KOSPI `005930` bar; capture manifest and
original raw receipt verify; `custody-exports/raw-1.json` is byte-for-byte the
original receipt; outbox is `pending`, attempts 0. Local export readback is
`content_consistent_unverified`; external submission ID, returned receipt and
external readback are absent; independent custody and publication authentication
remain false. The original PIT assessment remains blocked. No outbox processing,
S3 upload or provider request occurs, including on repeated staging.

The fixture stores pins beside the archive for convenience only. In real operation
retain the original plan/split/capture/binding pins and their reviewed source
owners separately from mutable archive/export storage. Rehashing changed local
reports does not create an independent trust root.

## Binding and independent verification

`forward_capture_custody_binding_v1` contains the capture ID, immutable manifest
and original plan pins, original source endpoint/parameters, exact source scope,
response sequence/raw ID/body SHA, response/ingestion fingerprints, original
export payload and SHA, existing idempotency key and outbox raw ID. It deliberately
excludes changing outbox attempts and does not invent a submission ID. Store the
binding and its canonical pin from the stage output; `custody-verify` reconstructs
it from the original verified capture and exported receipt.

A custody readiness report also has an independent report pin. The Python
`verify_operations_report` requires original plan/config/evidence owners and pins
and recomputes the assessment. A self-rehashed report with custody or authenticated
publication flags changed fails semantic verification. Status changes legitimately
produce a new readiness fingerprint; old reports are not silently rewritten.

Original raw byte readback remains the PR #139 `forward-capture readback` command;
the new `custody-readback` inspects **receipt** linkage and trust status, without
duplicating raw-byte readback or offering a network transport.

## Outbox lifecycle and recovery

`custody-stage` exports only the chosen response receipt, verifies it, then calls
original `enqueue_capture_receipt`. It never calls `process_outbox`. Duplicate
staging reuses exact exports/idempotency keys and never resets attempt counts,
retry/manual-review state or prior failures. There is no new `accepted` state.
Existing response retries can have different raw IDs; select each response
explicitly using `--sequence`, preserving both failed/incomplete and successful
response identities without claiming a partial response was complete.

Export and enqueue are separate original commits, not a new distributed
transaction. If interrupted after a complete export, rerun staging to verify and
enqueue the same receipt. If interrupted after enqueue, rerun without duplicating
the entry. A partial, missing, wrong-ID, changed or symlinked export is never
silently overwritten: invalid existing exports require operator triage. Missing
exports are reported; explicitly staging may recreate the exact verified receipt.
The archive/body/manifest remain authoritative and must not be repaired by
inventing evidence. Verification/readiness/readback are read-only; staging changes
only the original pilot outbox and adds original export files.

## Inspecting independently supplied external claims offline

No external receipt is created locally. If an operator later has original response
bytes and separately trusted pins, use an explicitly pinned operations config and:

```sh
dsv forward-capture custody-readiness "${common[@]}" \
  --operations-config "$reviewed_ops_config" --operations-config-pin "$ops_pin" \
  --acknowledgement "$original_ack_file" --acknowledgement-pin "$independent_ack_sha" \
  --returned-readback "$original_readback_file" --returned-readback-pin "$independent_readback_sha"
```

Each optional evidence file is bounded and read locally only. Original checks bind
acknowledgement custodian/raw ID/receipt SHA and readback bytes to the exported
receipt. Invalid/missing pins, incorrect IDs, malformed acknowledgements and failed
readback produce explicit blockers. A consistent original issuer receipt ID is
shown only after the original content checks; it is still an unauthenticated
claim. Local export readback and supplied external readback occupy separate fields.
Even mutually consistent claim bytes never authenticate an issuer or publication.
`--allow-network` is rejected for every custody operation.

For an example operations config, use the exact `ForwardOperationsConfig.document`
with its canonical fingerprint; omit unknown references rather than guessing:

```python
from donghak_stock_vision.storage.forward_custody import ForwardOperationsConfig
config = ForwardOperationsConfig()  # all external/retention/backup evidence unavailable
print(config.document.payload_json)
print(config.document.identifier)
```

## Future separately approved operation

Before any real capture, independently review current KRX daily-service access,
terms, private-use/redistribution constraints and permitted collection frequency;
retain original service/terms evidence, explicitly reviewed scope/split/plan pin,
credential presence, budgets and private storage. PR #139's capture command still
requires explicit network permission. This task does not execute that step or
satisfy permission by inserting local reference strings.

Before any external submission, separately approve the exact endpoint/account,
region/bucket/namespace, identity, roles and least-privilege credentials. Reuse the
original reviewed custody session only in that authorized workflow. Existing
`docs/capture-custody-s3.md` describes a prior development deployment, not current
permission for these captures. Its adapter requires explicit versioned checksum
readback and active GOVERNANCE retention for the exact object version; no current
Object Lock, retention, bucket, AWS account or profile availability was assumed or
queried here. Retention alone does not provide a recoverable backup or independent
authenticated custody. Original A-2/A-3 approval requirements remain applicable.

A later source-body/manifest custody protocol, independent acknowledgement
verifier, authenticated clock/publication evidence, retention audit and backup
restore drill require separately scoped work. Preserve Corporate Actions, PIT/OOS,
Runner Tail, Run Input Freeze and trading restrictions. No strategy, paper order,
public API route, scheduler, Docker service, operational approval or live trading
activation is added.


## Version-aware operational scope compatibility (PR #220)

The original `ForwardCapturePlan` remains authoritative. It is reconstructed with
its strict existing `from_dict` validator and canonical round-trip comparison
before archive reads. Custody never creates research membership or substitutes
an observation/market scope for a research split.

| Original plan | Scope authority | Before #220 | Current custody output |
| --- | --- | --- | --- |
| v1 | Existing training/tuning partition and original split/pin | Supported research-scope projection | Unchanged `forward_capture_custody_binding_v1` and `forward_capture_operational_readiness_v1`, identical canonical bytes and pins for identical owners/configuration |
| v2 | Original `prospective_read_only_observation_v1`, UTC collection window (maximum 24 hours), completed observation date with existing 1–7 day rule, KOSPI/005930 | Custody v1 indexed absent research fields; #219 reported incompatible | `forward_capture_custody_binding_v2` and `forward_capture_operational_readiness_v2`, explicit `kind=observation` |
| v3 | Original `kospi_full_market_scope_v1`, endpoint/method/market, bounded validity window and explicit ordered permitted days | Same research-scope mismatch | Same new v2 custody contracts, explicit `kind=full_market` |

For v2/v3 the nested scope has version `forward_capture_custody_scope_v2`, the
exact `capture_plan_version`, provider, market, currency, day and mode. Observation
scope includes the original ticker and exact `observation_scope`; full-market
scope instead includes exact `full_market_scope`. There are no fabricated
`partition`, `split` or `split_pin` fields. The full-market allowlist is an explicit
collection declaration, not independently verified exchange calendar evidence.

Original capture/plan pins, response sequence, endpoint/parameters, raw receipt ID,
raw digest, response/ingestion fingerprints, exported original receipt bytes and
outbox identity retain their existing meanings. Capture replay still reconstructs
the original raw-source and parser linkage; `capture_raw_source_custody_v1` is
unchanged. Full-market HTTP bodies stay whole in the original archive and no
research ingestion is introduced. Existing failed ingestion remains a failure;
receipt custody does not claim successful normalization.

Both local request-start and response-completion instants must be within the
original operational scope's **[valid_from, valid_until)** window. This is a
structural consistency check against original local timestamps, not a new grant
or proof of authentic historical timing. Existing provider-policy authorization,
credentials, network opt-in and collection-lag checks are not changed or replaced.

Use the existing `stage_forward_custody`, `verify_forward_custody`,
`assess_forward_operations` and `verify_operations_report` APIs or existing
`forward-capture custody-stage`, `custody-verify`, `custody-readiness`,
`custody-status` and `custody-readback` CLI actions with the **original** plan file,
plan pin, attempt, capture pin and response sequence. Command options and offline
restrictions are unchanged; no new transport or submission path is added.
Explicit local staging writes only the existing receipt export/outbox; all
verification/readiness operations remain read-only. This PR stages only synthetic
test archives, never private originals.

Unknown versions and missing/extra/cross-version scope fields fail through the
existing plan/scope validators (`plan_contract`, `plan_fields`,
`forward_scope_version`, `full_market_scope_contract`, or their stricter original
scope reasons). Noncanonical plans fail `custody_canonical_plan_required`.
Wrong capture/plan/attempt/raw identities retain original verifier failures;
unknown response sequence is `unknown_response`. Out-of-window operational
capture timestamps fail `custody_capture_outside_declared_scope_window`.
Rehashed binding/report substitutions fail `custody_binding_semantic_mismatch`
or `readiness_report_semantic_mismatch`. Missing exports, acknowledgements and
readbacks remain explicit blockers, never implied successes.

Existing limits are retained: bounded archive, plan, response, supplied receipt
and readback sizes. The projection adds only bounded scope metadata, no raw-body
copy, database scan or additional full-source replay. Repeated verification uses
original owners and retained pins, not a mutable trust cache.

Even content-consistent original acknowledgements/readbacks remain unauthenticated.
Existing signature semantics, issuer governance and optimized cohort reconciliation
are unchanged. No publication time, independent clock/custody authentication,
genuine prediction eligibility, performance approval or trading authorization is
established. External custody of the complete raw response is not achieved by
exporting its JSON receipt.
