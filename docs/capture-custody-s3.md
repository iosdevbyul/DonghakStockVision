# A-2: explicit AWS S3 custody adapter

The optional `S3CustodySession` stores the **exact exported capture receipt bytes**
in `donghak-custody-dev` (ap-southeast-2), while FastAPI and SQLite stay on the local
Mac. It uses the existing receipt verification, stable idempotency identity,
explicit queued submission, immutable submission/lookup audits and readback
comparison. It does not upload the archived source response body: the existing
A-2 artifact is the exported JSON capture receipt.

The FastAPI research application remains read-only. Construct the session in an
explicit operator workflow and pass it the existing SQLite connection, export
directory and queued raw ID. There is no automatic upload on import, API startup,
outbox enqueue, or ordinary CI test execution.

## Reviewed AWS configuration and permissions

Read-only inspection on 2026-10-12 confirmed:

- `default` resolves to the existing `AccountFullAccessRole`.
- `DonghakCustodyWriterRole` already trusts
  `arn:aws:iam::101180464666:role/managed/AccountFullAccessRole`.
- The bucket has versioning and Object Lock enabled, default GOVERNANCE retention
  of one day, all four Public Access Block settings enabled, and AES256 default
  encryption. No bucket policy existed at inspection time.
- Initially the writer had no inline or attached managed policies. After the
  approved change, it has exactly `DonghakCustodyEvidenceAccess` inline and zero
  attached managed policies. The final document matches
  `infrastructure/aws/donghak-custody-writer-policy.json` read back from IAM.

The policy contains **only two Allow statements**, with no Deny/NotAction statement:

| Resource | Allowed operations |
| --- | --- |
| `donghak-custody-dev/evidence/*`, `donghak-custody-dev/dev-tests/*` | PutObject, GetObject, GetObjectVersion, GetObjectRetention |
| `donghak-custody-dev`, restricted by `s3:prefix` to `evidence/*`, `dev-tests/*` | ListBucketVersions |

Deletion, Governance bypass, retention modification, legal hold/ACL modification,
bucket configuration and IAM administration receive no grant. Other buckets and
other prefixes receive no grant. Keep the dedicated role free of additional
policies; adding grants or resource policies requires another permissions review.
No permissions boundary was created. Administrator policies/trust and bucket
configuration were not modified.

The local `~/.aws/config` dedicated profile is configured with:

```ini
[profile donghak-custody]
role_arn = arn:aws:iam::101180464666:role/DonghakCustodyWriterRole
source_profile = default
region = ap-southeast-2
role_session_name = donghak-custody-dev
```

No access keys or tokens are placed in the project. `default` currently uses AWS
CLI `login_session`; install `.[custody]` to obtain boto3 >=1.41 and the required
CRT support. The adapter explicitly selects `donghak-custody`, checks its role and
source profile, and calls STS before constructing the S3 client. Wrong role,
missing profile, expired credentials and SDK failures fail closed without an
administrator or `comatoki` fallback. SDK exception details are redacted.

## Storage, replay and audit behavior

Choose an explicit namespace such as `evidence/<local-archive-namespace>/` or
`dev-tests/<unique-approved-run>/`. The key is
`<namespace>/raw-<rawId>/<idempotency-key>.json`. Raw IDs are local to the source
archive; use a stable namespace for that archive across process restarts.

Uploads are bounded to 64 KiB and use SHA-256 checksums and `IfNoneMatch="*"`.
The adapter never sets retention headers or performs a bucket/retention mutation;
S3 applies the existing one-day bucket default. A duplicate upload receives 412
and verifies the existing version and exact bytes instead of creating a new
version. 409, permission failures and transient failures are surfaced to the
existing explicit recovery workflow. A lost upload response can be replayed with
the same key and reconciled without another version.

Every successful observation verifies a unique latest version without delete
markers, performs a VersionId-pinned GetObject with checksum verification, checks
metadata and identity against the exact receipt bytes, and queries retention for
that same VersionId. Missing/nonunique/null versions, truncated history, changed
bytes or metadata, missing SHA-256 and expired/missing GOVERNANCE retention block
verification. Readback always fetches remote bytes; it never trusts a stored hash
alone. Read streams are bounded and closed.

`execute(..., operation="submit" | "lookup")` delegates to the existing queued
submission/lookup audit functions. It also appends the exact S3 observation
(VersionId, digest, retention deadline) to immutable
`capture_custody_s3_versions`. No credentials enter these rows. As in the existing
workflow, remote writes and local audit commits cannot be atomic; a crash can
leave remote bytes without a local audit. Reconcile through lookup and replay;
never silently mark the outbox accepted. A local audit write failure is surfaced.

`verify_readback(...)` uses the existing `verify_custody_readback` contract.
Outbox state/retry allowance and manual-review replay gates are preserved.
Matching storage content still means `claim_received_unverified` or
`content_consistent_unverified`. Independent custody, signatures, PIT authority
and inference permission remain false. This integration does not complete all
A-2/A-3 independent custodian/authentication requirements.

## Operator usage and tests

```python
from donghak_stock_vision.storage.capture_custody_s3 import create_s3_custody_session

# Existing verified export and explicitly queued receipt are required.
session = create_s3_custody_session(prefix="evidence/my-local-archive/")
submission = session.execute(connection, export_directory, raw_id, operation="submit")
lookup = session.execute(connection, export_directory, raw_id, operation="lookup")
readback = session.verify_readback(connection, export_directory, raw_id)
```

Obtain explicit approval before any IAM change, cost-incurring operation or live
upload. `infrastructure/aws/verify_permissions.py` is a read-only check using
`default` for IAM inspection/simulation and `donghak-custody` for actual STS and
S3 reads. It asserts exactly the reviewed installed policy, no other policies,
48 policy decisions, permitted version listing and AccessDenied for unapproved
prefix and unrestricted listing. It never invokes deletion or bypass APIs.

The Principal Policy Simulator returned Organizations-denied decisions even for
operations that succeeded with the real role. Therefore the recorded 48 checks
use **Custom Policy Simulator on the exact policy read back from IAM**, not a
claim that simulation models every Organizations/resource-policy constraint.
Deletion/bypass and other-bucket denial are simulation evidence; no destructive
API calls were made. Actual approved upload and readback establish runtime access.

Run offline checks with `pip install -e '.[dev,learning,api,custody]'`, then `pytest`,
Ruff and Mypy. New tests cover conditional replay, lost response recovery, SDK
request validation via Stubber, bad versions/checksums/metadata/retention, bounded
content, role enforcement and SQLite/outbox/audit/readback integration. Ordinary
CI blocks network and excludes live tests; it needs no AWS credentials.

A live run requires explicit approval and an independently chosen unique prefix:

```sh
DSV_S3_LIVE_APPROVED=yes \
DSV_S3_LIVE_PREFIX=dev-tests/<approved-unique-run>/ \
DSV_S3_REPORT_PATH=.data/s3-live-report.json \
python -m pytest -q -m live tests/integration/test_live_s3_custody.py
```

One synthetic receipt is created per live test execution. A restarted local
SQLite connection and new AWS session replay it, compare the unchanged VersionId,
query remote content and assert approximately 24 hours of default retention.
Objects are never deleted and continue incurring storage charges after retention
expires. The explicit live test does not run in GitHub Actions.

## Approved live result (2026-10-12)

The actual STS identity was
`arn:aws:sts::101180464666:assumed-role/DonghakCustodyWriterRole/donghak-custody-dev`.
All uploads and content readbacks used the `donghak-custody` profile.

- Bucket: `donghak-custody-dev`.
- Object count: **1** (within the approved maximum of 3), retained without deletion.
- Key: `dev-tests/20261012-s3-custody-cfb387/raw-1/bd494967164a7ea4730f53e3e0744c3a3fc6ec89a22bbd4058cf6fbf4b2e6811.json`.
- VersionId: `COEHF0onGIf10Q43GMdfjnEv.pKsXEvf`.
- Retention: GOVERNANCE until **2026-10-13 22:04:00.175 KST**
  (`2026-10-13T13:04:00.175Z`).
- Submitted and remotely retrieved SHA-256:
  `0cd97b257f83b18e100a4f0b0afa7bed563a67cbd8af68cfdd323f13fd3e0d96`.
- Restart replay: same VersionId, exactly one remote version.
- Byte readback: matched; authority flags remained false.
- Unit/offline integration additions: 33 passed; full offline suite: 1479 passed,
  2 live tests deselected. Explicit S3 live test: 1 passed.

Local sanitized reports are `.data/s3-live-report.json` and
`.data/s3-permissions-report.json` (Git-ignored). These document storage checks,
not independently signed custody acknowledgement or perpetual retention.
