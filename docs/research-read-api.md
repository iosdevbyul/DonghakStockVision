# Pinned autonomous research read API (local, opt-in)

This adds a **read-only projection**, not a new research engine. It exposes the
original study plan's content-addressed ID and original experiment IDs directly
from an operator-selected immutable SQLite **copy** of an autonomous study.

## Activation

The study reader is off by default. An operator must create a **quiescent** copy
of an existing `experiments.sqlite3` file outside the actively written research
root. Approve and retain the copy's SHA-256 independently; do not use a live
database or expose the original model checkpoint directory.

```sh
dsv-api --study-snapshot "/private/approved/study.sqlite3" \
  --study-snapshot-sha256 "<approved-64-char-sha256>" \
  --study-id "<existing-autonomous-plan-artifact-id>" \
  --host 127.0.0.1 --port 8000
```

This option may be combined with an already-approved `--snapshot`. It is only
available on loopback and requires a private file (mode 0600), an exact
approved SHA-256 and a plan artifact present in that copy. No arbitrary paths,
plan IDs or PINs are accepted through HTTP. The client must access the loopback
server directly; a reverse proxy or remote deployment is not authorized.

## GET endpoints

- `/api/v1/research/studies`: the single approved study with its policy,
  experiment budget, recorded completion counts and unqualified evidence flags.
- `/api/v1/research/studies/{study_id}/experiments?offset=0&limit=25`:
  paginated experiment catalog using the actual immutable plan IDs, known
  terminal states and bounded, safe aggregate metrics.
- `/api/v1/research/studies/{study_id}/experiments/{experiment_id}`:
  one experiment from that catalog.

`studyId` is the artifact digest of the original autonomous plan.
`experimentId` is the stored configuration identity, never a list index.
Parent links are exposed only if the verified attempt refers to another ID
within that same plan. The endpoint **does not expose** raw experiment results,
feature samples, model checkpoint locations, dataset contents, credentials,
PIN contents, or filesystem paths.

## Explicit limitations

This API does not prove PIT, independent future performance, trading
authorization, or operational model promotion. Final reports, report IDs and
cross-policy comparisons are not available from this contract. They are
`UNAVAILABLE` and must not be invented by clients. The selection policy is
metadata describing a study, not a ranking across incomparable studies.

For the existing 72-selection / six-report research, a future **separately
approved publication** must first validate per-study snapshot identities,
cross-study common populations and report ownership. No old study is rerun,
and no historical report is overwritten as part of this API.

This is deliberately one pinned study per reader process, not discovery over
arbitrary private directories. A multi-study publication index requires an
explicit reviewed contract and independent verification.
