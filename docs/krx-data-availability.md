# KRX availability observations and research execution

`krx-scheduler availability` is a read-only consumer of existing original capture,
raw-source custody, authorization, stored-batch and optional shadow-request owners.
It makes no HTTP request, loads no credentials, creates no DB and changes no
scheduler state. Existing request/response metadata and completion records are
the audit trail; no second clock, collector or availability polling loop is added.

## What the times establish

`krx_capture_availability_v1` reports the original request start, response body
completion, HTTP status, validated row count, raw SHA-256, original raw-source PIN,
optional verified SQLite batch identity and local completion-record timestamp.
Request end uses the original response-completion boundary; HTTP header arrival
was not separately retained. For incomplete/timeout responses, full-body receipt
time is null; capture-finished time records the transport failure boundary instead.
The scheduler's completion record is written after ingestion and integrity
validation. It is a local upper bound on successful
storage, **not SQLite's exact commit instant**; `sqlite_commit_at` remains null.
Missing original timestamps are never reconstructed from file modification times.

A positive response proves data was locally observable at that response's completion
time. It does not prove the first instant KRX published it, independent publication
chronology, a complete independently authenticated market universe, PIT qualification
or performance. `official_publication_time` and account-wide remaining quota stay
UNKNOWN. Mock responses retain `authorized_provider_mock_fixture` origin and never
set `provider_data_observed`. No original policy or historical receipt is changed.

The [official terms, article 5](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp)
describe generally continuous service access with maintenance/service exceptions.
Article 8 sets a per-key daily cap of 10,000 requests. These provisions do not
guarantee the publication hour of a particular market date. The
[official KOSPI daily-service description](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC),
usage instructions and retained `Spec.docx` do not establish an exact publication
hour in the material verified here. Same-day availability, per-API release-hour
differences, and a general weekend/holiday release timetable remain unverified.
The existing minimum one-calendar-day capture delay is a local policy/schema
restriction, not an authenticated KRX release guarantee. Do not replace it with
a third-party claimed hour or infer a universal timetable from one response.

## Read-only audit command

Use the original plan, independently retained capture/batch PINs and configured
authorization. Do not echo PIN values or place them in shared logs.

```sh
dsv krx-scheduler availability \
  --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --source-plan "$ORIGINAL_PLAN" --source-plan-pin "$PLAN_PIN" \
  --source-attempt "$ORIGINAL_ATTEMPT" --capture-pin "$CAPTURE_PIN" \
  --batch-pin "$BATCH_PIN" \
  --availability-request "$PINNED_SHADOW_REQUEST" \
  --availability-request-pin-file "$SHADOW_REQUEST_PIN_FILE"
```

The batch and research-request options are optional. Without a batch PIN the
report makes no SQLite-storage claim. Without a research request it makes no
deadline/alignment claim. A supplied request is verified against its retained PIN;
its original model and retained calendar establish the immediate next session and
unchanged midnight deadline. Sealed Final Test windows are rejected before capture
replay. The report still requires full input/feature/inference verification by the
existing Shadow Handoff/recorder and never creates a prediction itself.

The action rejects `--execute`, `--scheduled`, `--apply`, `--allow-network` and
operator-enablement flags. Source/body/receipt/batch substitutions fail closed.
Successful HTTP 200 with a valid empty `OutBlock_1` is `empty_response`, zero rows,
and no availability/storage success. It is not automatically a holiday. Malformed
or wrong-date rows are `invalid_response`; transport/status failures remain failed.
The raw response remains retained, and this command never retries or repairs it.

Separate original read transactions and retained PIN comparisons are consistency
checks, not an atomic snapshot across archive, scheduler and market DB. A concurrent
write can cause rejection; retry an offline assessment only after the writer exits.
This never authorizes another provider request or an unknown-outcome retry.

## Actual observation: November 14, 2026

One existing authorized manual `run-due` execution selected missing November 13
with `--max-runs 1`, the existing policy's one-day delay, retry zero and single
runner lock. The local request allowance was one and used count zero; account-wide
remaining quota was UNKNOWN. Original approved policy/PINs and installed LaunchAgent
were preserved. No new collector code was deployed: live execution used the existing
operating checkout, while research used merged PR #234.

| Fact | Verified result (KST) |
| --- | --- |
| Trading date | 2026-11-13 |
| Request started | 2026-11-14 12:20:06.713152 |
| Complete response observed | 2026-11-14 12:20:08.711893 |
| HTTP / valid original rows | 200 / 936 |
| Post-storage completion record | 2026-11-14 12:20:16.018938 |
| Added SQLite rows / duplicate additions | 936 / 0 |
| Operational total | 26,302 observations / 28 observed trading dates |
| 005930 | Original-source/stored-value readback verified |
| New provider requests / retries | 1 / 0 |
| Official first publication | UNKNOWN |

Existing rows in every original archive/operational table were compared by retained
row-prefix hashes and remained unchanged. The authorized DB/runner files changed
only as part of collection; their whole-file hashes are not claimed unchanged.
The other 337 protected files retained their SHA-256 values, including Phase 2,
model/training/evaluation artifacts, original PINs and old prediction journal.
The original October 2 empty response was neither changed nor requested again.

The original November 16 request template and PIN were preserved. Existing Handoff
froze a separate post-commit request, verified all required histories and the same
model, and recorded **four actual research-forward candidates**: 000660, 005380,
005930 and 035420. Input date is November 13; event sessions remain November 16–20;
the generation deadline remains November 16 00:00 KST. 051910 was blocked by the
unchanged `shadow_no_directional_context` rule. No label, threshold or event was
adjusted to increase the count. There was no new Historical Replay or training.

All four are Pending, with zero evaluated samples and unavailable performance metrics
null. Duplicate recording produced zero new predictions; repeat Pending resolution
kept the acknowledged head. Initial Pending events legitimately advance the journal
head; the retained checkpoint from the latest outcome report must be used afterward.
Prediction/model/input/score verification succeeded without changing any record.
All PIT, performance, genuine-forward and execution approval flags remain false.

Private evidence resides in `Research/availability-20261114T031953Z`, with separate
`ResearchPins/availability-20261114T031953Z`. Collection, availability, Handoff,
duplicate, Pending and semantic-verification reports are retained. No key/PIN or
market-response body is published in repository documentation.

## Next operations and automation boundary

The existing weekday 19:00 LaunchAgent is unchanged. It must skip the now completed
November 13 date using the existing completion registry; do not request it again.
The original scheduler may continue accumulating authorized later dates, independently
of research. Availability can then be audited offline using the retained evidence
and the command above. Continue deferred evaluation with the existing `resolve`
command and a new pinned current-source request only after all five outcome sessions
are actually available; never reuse a stale whole-DB PIN or overwrite an old request.

This one successful Saturday observation establishes a real weekend window for this
particular Friday input. It does not establish when future Friday responses will
be ready. A reviewed one-shot weekend collection can use the existing `run-due
--execute --max-runs 1` path under its approved date, policy and request budget,
without `--scheduled`, followed by existing opt-in Handoff arguments. Do not add
a second scheduler, infer another observation hour, or enable this path unattended
from this single sample. Failed/empty/403/429/timeout responses stop without retry.

Ordinary weekday 19:00 collection remains too late for the existing next-session
midnight guard. For consecutive calendar-day sessions, the earliest permitted
capture (input date + one day at 00:00 KST) equals the exclusive prediction
deadline. That permitted window is empty regardless of the chosen scheduler hour.
Friday-to-Monday or a verified holiday gap can provide a nonempty window, as the
actual Saturday observation demonstrates; the event start must not be moved.
Even a next-morning pre-open schedule would require a separately
reviewed session-time contract; changing an hour alone cannot relax that guard.
No general release timetable or safe new recurring research schedule has been
verified, so none is activated. Deployment review, suitable collection timing and
actual repeated-cycle validation remain necessary for unattended research operation.
