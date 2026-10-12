# A-2 raw-source S3 custody adapter

`storage/capture_raw_source_s3.py` adds explicit S3 storage for the **actual bytes**
of a verified `RawSourceCustodyContract`. Mac SQLite/FastAPI and collection/model
code remain local and unchanged. The receipt S3 adapter is unchanged; raw storage
and receipt storage are separate operations with separate observations.

## Keys, bytes and dedicated runtime identity

Production uses PR #93's `source_object_key` verbatim:

```text
evidence/<archive-namespace>/raw-source-v1/raw-<raw-id>/<source-identifier>.bin
```

The adapter takes no production bucket or arbitrary destination override. Bucket
and region remain `donghak-custody-dev` and `ap-southeast-2`. Its factory reuses the
existing `create_s3_custody_session` dedicated-profile gate, selects
`donghak-custody`, checks the configured role/source profile and calls STS to
verify `DonghakCustodyWriterRole` before obtaining an S3 client. Failure has no
administrator/other-profile fallback. Credentials and SDK exception details do
not enter return values, repr, logs or SQLite audit rows.

Tests can supply an explicit `test_prefix=dev-tests/<unique-lowercase-run>/`.
This prepends a test isolation boundary to the **complete unchanged contract key**:
`dev-tests/<run>/evidence/<archive>/raw-source-v1/...`. It changes neither the
contract identity nor any receipt key. The approved live test uses only synthetic
binary data in this boundary; it never uploads actual KRX bodies or receipt
objects. A production namespace must remain stable across archive restarts.

The source loader checks the original BLOB, recorded SHA-256 and matching receipt
in one bounded SQLite snapshot. The existing 16 MiB contract applies, including
empty BLOBs. Uploads pass the original `bytes` directly as application/octet-stream
with exact ContentLength, `ChecksumAlgorithm=SHA256`, the full-body base64 SHA-256,
contract metadata and `IfNoneMatch="*"`. No JSON/base64 transformation is applied
to the source body, and no multipart upload is used.

[AWS conditional-write rules](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
determine the server response: 412 is reconciled against exact stored content;
409 and other errors are blocked rather than treated as storage success. No
retention headers, deletion, Governance bypass or bucket-setting calls are made
by the adapter.

## Exact-version bounded readback and retention

The adapter lists at most two versions under the exact key, rejects truncation,
delete markers, more than one exact-key version, non-latest and missing/null
VersionIds. It compares the listing with any previously audited VersionId.
Missing previously observed bytes/version is blocked rather than resurrected.

GetObject explicitly specifies that VersionId and `ChecksumMode=ENABLED`. Its
returned VersionId, length, full-body SHA-256 and **all** contract metadata must
match. The actual downloaded body is read in requests of at most 64 KiB, compared
chunk-by-chunk with the local bytes and hashed incrementally. Short reads are
supported; early EOF, extra bytes, invalid stream output and read failures block
verification. Streams are closed on both success and failure.

The adapter does not accumulate a second 16 MiB downloaded body. Python retains
the expected local body (up to 16 MiB), with bounded downloaded/comparison chunks
(up to about 128 KiB) and hash state. SQLite, botocore, transport buffers and
concurrency can add memory; this is not a total process RSS limit. Response
length/header mismatches are rejected before streaming, and total reads cannot
exceed the expected size plus one byte.

GetObjectRetention targets the **same VersionId** and requires GOVERNANCE plus a
timezone-aware future RetainUntilDate. Expired/missing retention, wrong mode and
invalid deadlines block the observation. A final version listing rejects a
history change during readback. These separate S3 calls are not an atomic remote
snapshot and do not promise immutability beyond the observed retention deadline.

## Replay, response loss and manual review

`submit` queries existing state before another PUT. Matching original bytes,
metadata, version and active retention return the existing observation without
creating another version. Conflicting/expired/ambiguous state is blocked before
an upload can create a replacement version. A race where another writer creates
the same key produces 412 and is verified through exact-version readback.

Lost response or transient/permission failure produces a blocked audit result.
An explicit subsequent lookup, including after restart with the same archive
namespace and test boundary, reconciles the same deterministic key. A subsequent
submit uses the recovered object instead of blindly creating another version.
SDK errors are redacted and never converted automatically to success.

The raw workflow does not enqueue, reset retry budgets or change receipt Outbox
state. If an existing receipt Outbox row is `manual_review`, raw `submit` is
blocked with no S3 request; explicit lookup/readback remain available for
investigation. There is no automatic replay authorization, scheduler or inference
permission.

## Separate append-only SQLite audit

Raw operations need their own table because receipt audit tables represent
receipt identity/status, not original-body storage. No existing schema/table is
modified, and SQLiteStore startup does not create a new raw table. Explicit raw
session execution creates `capture_raw_source_s3_events` and two immutable
update/delete triggers if needed.

Each event records raw ID/receipt foreign key, archive namespace, logical source
identifier, bucket/key, raw SHA-256/length, operation (submit/lookup/readback),
status, VersionId, GOVERNANCE deadline and observed time or a fixed blocker.
Statuses are `content_consistent_unverified`, `not_found_unverified` and `blocked`.
Content consistency never means independently authenticated custody.

Previously consistent audit versions are pinned on subsequent operations; a
changed VersionId is rejected, and conflicting prior local version observations
are refused. The original receipt audits and raw events can be inspected
independently. Existing synthetic regression tests show both can store/read back
without changing receipt exports, keys or Outbox state.

The session requires a committed local archive and refuses an already active
caller transaction, avoiding an implicit commit of unrelated work. It initializes
the audit before S3 requests; initialization failure prevents upload. An INSERT
failure after remote storage raises a fixed audit-persistence error, never a
successful result. Lookup can later reconstruct the missing local observation.

**S3 upload and SQLite audit persistence are not atomic.** A crash or lost local
commit can leave a retained remote object without a local success event. Do not
infer remote absence from missing audit rows or local success from an attempted
write. Explicitly reconcile the same key and exact bytes/version; do not delete
or overwrite to repair history. SQLite triggers protect ordinary local mutation,
not an independently controlled authority against the machine's administrator.

## Explicit operator API

```python
from donghak_stock_vision.storage.capture_raw_source_s3 import create_raw_source_s3_session

session = create_raw_source_s3_session()
result = session.execute(
    connection, raw_id, archive_namespace="my-stable-archive", operation="submit"
)
lookup = session.execute(
    connection, raw_id, archive_namespace="my-stable-archive", operation="lookup"
)
readback = session.execute(
    connection, raw_id, archive_namespace="my-stable-archive", operation="readback"
)
```

Check status explicitly; blocked/not-found are not successful custody. Return
flags `independent_custody_verified` and `inference_authorized` remain false. The
factory does not itself upload, and import/startup performs no S3 work. Existing
FastAPI endpoints and deployment are unchanged.

## Validation and AWS inspection

Normal pytest/CI prohibits network, excludes `live` tests and uses synthetic
MemoryS3/Stubber cases with no real AWS credentials. New offline cases cover
normal and duplicate storage, response loss/restart, 412 races, null/wrong/changed
versions, ambiguous history/deletion markers, body/length/checksum/metadata
mismatch, bounded short reads/EOF/extra bytes/closed streams, empty and 16 MiB
bodies, namespace separation, retention failures, permission/transient errors,
manual review, active transactions, read-only audit initialization, failed audit
INSERT recovery and independent receipt regression.

Read-only AWS inspection confirmed the dedicated STS identity, exactly one
`DonghakCustodyEvidenceAccess` inline policy and no attached managed policies. Its
unchanged two Allow statements cover necessary object operations and restricted
version listing under `evidence/*` and `dev-tests/*`. IAM inspection used `default`;
**every S3 request used donghak-custody**. No IAM policy/trust/role was changed.

Bucket-level Object Lock/Versioning configuration reads returned AccessDenied
because the dedicated role has no such grants. No administrator S3 fallback or
permission expansion was used. Therefore this inspection does **not** independently
verify the current bucket configuration. The real object's VersionId and retention
must be verified during an approved live test instead; those checks require only
existing object permissions.

The opt-in live test creates at most two non-sensitive synthetic bodies of 70 KiB,
under one approved unique dev-tests boundary. It simulates loss of one successful
application response and uses fresh sessions/SQLite connections to recover,
replay and verify both objects. No receipt S3 object is uploaded. The test first requires an empty prefix, preventing a rerun from creating extra
objects beyond the approved scope. Recover an interrupted run explicitly rather
than regenerate its local identities. The test requires:

```sh
DSV_RAW_S3_LIVE_APPROVED=yes \
DSV_RAW_S3_LIVE_PREFIX=dev-tests/<approved-unique-run>/ \
DSV_RAW_S3_REPORT_PATH=.data/raw-source-s3-live-report.json \
python -m pytest -q -m live tests/integration/test_live_raw_source_s3.py
```

Obtain explicit approval before this command: S3 requests and persistent storage
cost money. Objects are never deleted and remain after retention expires.
Actual KRX source external-storage/use rights require separate review and
approval; this test does not exercise them. Normal CI never executes this test.

Actual test execution and result, if approved, are reported separately from the
offline implementation. No live storage result is inferred from synthetic tests.
Independent custodian administration, authenticated acknowledgements/signatures,
longer-term retention/recovery policy and A-3 trust management remain separate
A-2 requirements even after a successful real S3 storage observation.

## This PR's validation record

- New offline adapter/audit/regression tests: 46 passed.
- Full offline suite: 1,579 passed, 3 live tests deselected.
- Ruff check/format, Mypy and sdist/wheel build passed locally.
- The human explicitly approved at most two synthetic objects, each <=80 KiB,
  only under `dev-tests/20261012-raw-source-6d901a/` using `donghak-custody`.
- PR #94's feature branch was clean and matched the reviewed commit before the
  live run. The prefix was empty (no objects/delete markers, no truncation).
- STS confirmed
  `arn:aws:sts::101180464666:assumed-role/DonghakCustodyWriterRole/donghak-custody-dev`.
- The existing explicit live test passed: **1 passed**. It was executed once;
  no additional upload run or actual KRX/receipt object upload was performed.

### Actual approved S3 result (2026-10-12)

The sanitized, reviewable report is
[`evidence/raw-source-s3/live-report.json`](evidence/raw-source-s3/live-report.json).
The original runtime report remains Git-ignored at
`.data/raw-source-s3-live-report.json`, and the post-test read-only inventory is
`.data/raw-source-s3-inventory.json`. Reports contain public verification metadata,
not source response bodies, access keys or session tokens.

Two binary source objects were created, each **71,680 bytes (70 KiB)**. A separate
actual `ListObjectVersions` request with `donghak-custody` confirmed exactly two
keys, one latest version per key, no duplicate versions/delete markers and no
truncation. Both keys preserve the contract path inside the approved boundary.

| Raw ID | VersionId | GOVERNANCE retention deadline (KST) |
| --- | --- | --- |
| 1 | `nuPgqqj9BQH7WeFHDCd5ZQrIUlcNbMUF` | 2026-10-13 23:24:15.216 +09:00 |
| 2 | `tNHxgBS.P2GgfXwMOc_JboPzh9oeKaCe` | 2026-10-13 23:24:18.187 +09:00 |

The actual-byte SHA-256 values matched the S3 checksum, original byte comparison
and VersionId-pinned Readback:

- Raw 1: `2920ecaf33981119c0059ab10092ba73292ca91e560ae117a23456b4ee8279ba`.
- Raw 2: `7fced1dd603bd6879fc8e99e4ea51fd3075adbcfc05a8a7b1265b995ee7c935c`.

The first object survived local connection/session restart with the same VersionId.
For the second, an application wrapper deliberately discarded one successful
PutObject response, producing a blocked local result. This simulated application
response loss, not an actual AWS/network outage. A fresh session after restart
looked up the same key and recovered the retained remote object; replay and
Readback preserved the same VersionId without another object/version.

No IAM role/policy/trust, bucket/Object Lock settings, existing source data or
receipt exports/objects were modified. New synthetic objects were not deleted;
they continue consuming storage after the one-day retention expires. Bucket-wide
configuration remains unreadable to this least-privilege role; the verified
GOVERNANCE deadlines above are observations of the exact object versions.

Actual S3 storage/readback verification succeeded for these synthetic objects.
`independent_custody_verified` and `inference_authorized` remained **false**.
Independent administration, authenticated custody attestations/signatures,
long-term retention policy and A-3 trust management are still outstanding.
