# macOS daily KRX collection scheduler

This is a per-user launchd job plus a short-lived one-shot runner, not a daemon,
Docker service, new collector, market DB or authorization authority. Development
uses temporary synthetic documents and MockTransport. **No real LaunchAgent was
installed/enabled and no real KRX request was executed.** The real private recurring
policy has not been issued by this work; do not substitute fixture permissions.

## Components reused

The runner uses `ForwardCapturePlan` v3, `operator_full_market_recurring_v1`, existing
readiness/key binding, `run_capture`, original raw receipts/manifests and semantic
verification, and `market-storage`'s verified `ingest_capture` and SQLite validation.
The existing forward-capture CLI and runner share the same capture-client factory;
there is no alternate HTTP implementation, quota ledger or SQLite schema.

PIT/training/OOS/signal/PAPER/execution eligibility remains false. No protected
research payload, old one-ticker pilot or recovery artifact is imported. See the
[recurring policy guide](krx-full-market-recurring.md) for separate service approval,
owner review, provider/mock identity and collection-scope requirements.

## Private configuration

Prepare an operator-owned, mode-0600 config outside Git, with canonical absolute
paths. Keep its canonical `FrozenJSON.identifier` separately trusted. The CLI checks
that independent config pin on every invocation, including scheduled executions.
This is a **template**, not permission or a working private package:

```json
{
  "version": "krx_scheduler_config_v1",
  "policy_root": "/absolute/private/recurring-policy",
  "policy_pin": "REPLACE_WITH_THE_REAL_SEPARATELY_TRUSTED_POLICY_PIN",
  "capture_root": "/absolute/private/new-full-market-archive",
  "operational_root": "/absolute/private/operational-store",
  "runner_root": "/absolute/private/operational-store/logs/krx-scheduler",
  "credentials_file": "/absolute/private/krx.env",
  "python_executable": "/absolute/project/.venv/bin/python",
  "working_directory": "/absolute/project",
  "hour": 19,
  "minute": 0,
  "timezone": "Asia/Seoul"
}
```

`capture_root` must match the reviewed policy. It must be a new full-market archive,
not the original Samsung-only pilot. Runner/operational directories cannot overlap
capture/permission directories. The runner directory is private (0700), and its
metadata files are 0600. Initialize operational SQLite explicitly with the existing
`dsv market-storage init --root "$OPERATIONAL_ROOT"`; the runner never creates or
migrates a missing research/operational DB implicitly.

Use the project's installed virtual environment or another reviewed installed CLI
interpreter. Venv interpreter symlinks are supported while preserving their absolute
venv path. Verify `"$PYTHON" -m donghak_stock_vision --help` before installation.
Relocating the project requires a new reviewed config/pin, not relative-path guessing.

`credentials_file` may be null when the process already receives `KRX_API_KEY`.
Otherwise it is an explicit private 0600 dotenv file, read locally. launchd does not
inherit an interactive shell's environment. The key is never placed in the plist,
arguments, run summaries or scheduler state. The existing capture code sends it as
`AUTH_KEY` only after authorization and actual-transport verification.

No real policy, key, original private document or private configuration was created,
printed or committed during development. Prepare the genuine recurring package using
the prior reviewed procedure before expecting a ready collection status.

## Offline preview, installation and explicit enablement

All examples below are operator instructions, not actions executed by this PR.
`$CONFIG`, `$CONFIG_PIN` and other variables refer to private actual files/pins, not
fixtures. Do not paste their contents into GitHub.

```sh
dsv krx-scheduler status --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler run-once --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler install --config "$CONFIG" --config-pin "$CONFIG_PIN"
```

`run-once` without `--execute` is read-only status/preview. All management commands
without `--apply` only generate a versioned plan; they neither write a plist nor call
launchctl. Inspect the generated plist's absolute arguments, working directory,
19:00 schedule and disabled defaults. No network request occurs in these commands.

After separately configuring genuine approval evidence and reviewing readiness:

```sh
# Install a disabled per-user file; no bootstrap/collection occurs here.
dsv krx-scheduler install --config "$CONFIG" --config-pin "$CONFIG_PIN" --apply

# Future explicit enablement: OS job activation plus persistent application consent.
dsv krx-scheduler resume --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --apply --allow-network --confirm-operator-authorization

# Same runner as launchd; still refuses work unless persistently enabled.
dsv krx-scheduler run-due --config "$CONFIG" --config-pin "$CONFIG_PIN" --execute

dsv krx-scheduler pause --config "$CONFIG" --config-pin "$CONFIG_PIN" --apply
dsv krx-scheduler uninstall --config "$CONFIG" --config-pin "$CONFIG_PIN" --apply
```

No install/resume/uninstall command was applied to the user's Mac in development or
tests. Installation does not enable collection. Initial application state is disabled,
plist `Disabled=true`, `RunAtLoad=false`, `KeepAlive=false`. Explicit resume requires
both network/operator flags, enables/bootstraps the owned per-user job, then records
application consent. It does not grant service permission: every request still verifies
the real policy, key, scope, expiration, quota, global capture-policy pause and storage.
A configured but missing policy stays blocked; no synthetic document can authorize
production transport.

Pause disables future application work before disabling the OS job. A running bounded
request may observe cancellation; a request already sent is never presumed absent or
refunded. Resume does not clear original capture-policy pauses, usage or duplicates.
Uninstall removes only this exact owned plist and unloads its job; config, run state,
original captures, trusted pins and operational data remain. Conflicting/unrelated
plists are rejected rather than overwritten. Control/config version mismatches or
corrupt metadata fail closed instead of silently resetting state.

## launchd and Asia/Seoul time

The fixed per-user label is `com.donghakstockvision.krx-daily`. The plist lives in the
operator's `~/Library/LaunchAgents`. ProgramArguments use the configured absolute Python
path and `-m donghak_stock_vision krx-scheduler run-due --execute --scheduled` with the
private config and independently supplied config pin. Space-containing paths are plist
array elements, not shell strings. WorkingDirectory is explicit.

`StartCalendarInterval` lists Monday–Friday at the configured hour/minute (19:00 by
recommendation). **launchd interprets calendar triggers in the Mac's system timezone.**
The child's `TZ=Asia/Seoul` alone does not fix that. Install/resume require an actual
Asia/Seoul host timezone; scheduled execution also rechecks it. A timezone change
blocks future scheduled collection. Pause/uninstall remain available on macOS after
a timezone change. The code does not modify the system timezone. Manual one-shot
execution uses an explicit Seoul clock conversion and all normal policy guards.

Local macOS `launchd.plist` documentation was inspected for calendar arrays, working
directory and externally stored `launchctl enable`/`disable` state. Native activation
remains an operator-only validation step; tests verify generation and dry-run without
changing any actual LaunchAgent or unrelated service.

When the Mac sleeps, launchd may coalesce a missed calendar event at wake. The runner
rechecks current Seoul date, allowlist and scheduled time. It never loops over missed
requests, runs a persistent worker or retries uncertain calls.

## Trading date selection and calendar limits

The configured source is the recurring policy's explicit `permitted_days` allowlist.
No independently current runtime KRX calendar is configured here. Existing historical
KIND calendar artifacts are not silently relabelled as a current operational calendar.
An operator may use verified calendar evidence to prepare the existing allowlist;
weekday detection by itself never proves a KRX session.

The default LaunchAgent now invokes the bounded `run-due` path. It processes missing
**observation dates** in ascending order, only when they are in `permitted_days` and
1–7 days before the actual Seoul execution date (or the policy's smaller configured
lag). The execution date need not itself be an allowed observation date: catch-up
on a holiday/weekend does not turn that date into a market session or request its bar.
The installed calendar trigger remains Monday–Friday at the configured time. A wake
or manual invocation rechecks timezone, scheduled time, policy and each requested day.

`run-once` remains a compatibility interface selecting the latest eligible date with
its previous execution-day guard. Use `backlog`/`run-due` for oldest-first collection;
no legacy plan, policy, capture, state v1 or published response is reinterpreted.

**Current policy limitation:** even at 19:00, same-day daily bars are not eligible under
PR #153's minimum one-day lag. This PR preserves that boundary, so Monday typically
collects the prior allowed Friday. No incomplete/current bar or unauthorized historical
warm-up is substituted. Same-day post-close collection would require a separately
reviewed policy/evidence change. Long holidays or downtime can exceed the lag. These missing dates are reported as
expired and **never fetched automatically** under this policy. No lossless historical
backfill or unconditional gap-free coverage is claimed.

## Single-run sequence and interruption behavior

A private process lock prevents scheduled/manual overlap. Each new capture follows:

1. Load pinned settings and explicit persistent enablement.
2. Select the latest eligible allowlisted completed date.
3. Revalidate original policy/terms/specification, key binding, duplicate/pause/quota
   state and the already initialized operational store's free-space readiness.
4. Atomically retain `capture_pending` with the plan and UTC start time.
5. Invoke the existing authorized bounded capture exactly once.
6. Persist its returned capture pin outside the original archive as `ingest_pending`.
7. Verify original capture receipts/manifest, ingest via the existing bridge and
   validate operational SQLite. Record a bounded completion and clear pending state.

Scheduler control/summary JSON uses canonical serialization, private permissions,
exclusive temp files, fsync, atomic replace and file locks. It is mutable local operator
state, not independent custody or a second economic/evidence ledger. Up to 366 source
references/completions and 512 KiB per state file are retained. Original raw/receipt and
policy authority remain in existing components. Local hashes are not provider signatures.

| Situation | Outcome |
|---|---|
| Disabled or no policy/key/storage | No request; explicit blocked/failed reason |
| No eligible/listed date or not yet scheduled time | Skip; no request |
| Already completed by this runner | Revalidate/reingest pinned source offline; no HTTP |
| Source already captured elsewhere without a runner-trusted pin | Existing duplicate guard blocks; no automatic adoption |
| HTTP/transport failure, invalid/incomplete market response | Preserve original failed capture; no same-date retry |
| Interrupt before a trustworthy capture pin is saved | `capture_pending` remains unknown; automatic request retry blocked |
| Capture pin saved, SQLite write/validation interrupted | Resume original pinned ingestion offline, idempotently |
| DB commit succeeded but scheduler completion write interrupted | Same offline replay; no duplicate rows/fills/requests |
| Pause during capture | Existing cancellation/receipt rules apply; no quota refund |

There is no distributed transaction across HTTP, archive, scheduler metadata and SQLite.
Power loss can leave an uncertain capture even if the original manifest was sealed.
Never derive a new trusted pin solely from that same untrusted archive to justify a
retry. Keep originals and investigate explicitly; missed-day recovery is future work.
Captured failed-source references remain available in private state for investigation.

## Status and logs

`status` is offline/read-only. It reports owned plist installation/loading, application
enablement, separate launchd override/effective enablement and host timezone
verification; last attempted run and last successful
collection; selected/latest persisted dates and observation count; local request usage,
remaining configured allowance and policy pause/duplicate state; missing runner
completion dates, stale/blocked dates, pending date/phase/start UTC, last error and
storage free bytes. Missing data is unavailable with blockers, not fabricated zero.
The status command exposes no key, credential hash, raw response, original policy
payload or directory listing. Local counters do not prove other applications' key usage.

`at`/`started_at` and pending/run timestamps are UTC instants; trading dates and schedule
interpretation are Seoul dates. Synthetic origins remain explicitly labelled. CLI JSON
schemas are `krx_scheduler_status_v1`, `krx_scheduler_run_v1`, `krx_launchagent_plan_v1`
and `krx_launchagent_management_v1`. A failed run exits 1; structured blocked/skipped
outcomes can exit 0, so inspect `outcome` and `reason`, not exit status alone.

launchd stdout/stderr paths are inside the private runner directory. Logs contain
sanitized summaries and existing capture identifiers, not raw provider data or keys.
There is no automatic log rotation, backup, archive expansion or historical recovery.
Retain/inspect logs privately and plan storage capacity under existing archive/SQLite
limits. No source evidence, policy package or research approval is deleted by management.

## Offline validation

Tests use explicit synthetic operator documents and MockTransport, temporary settings,
private temporary stores and fake LaunchAgent paths. They exercise 940-row capture to
SQLite, native receipt verification, idempotency, UTC/KST boundaries, allowlist/weekend
skips, missing/expired permissions and credentials, quota, locks, pause, uncertain
interruption, offline ingest recovery, atomic state writes, private credentials and
installed-package CLI previews. No real LaunchAgent was installed/enabled, no real
KRX request or brokerage/PAPER action was made, and no real private recurring policy
was issued. Native activation and genuinely authorized future collection remain
separate operator work.

## Production provisioning and first run

Use the [production readiness guide](krx-production-readiness.md) for original-document
review, private recurring-policy provisioning, `krx-scheduler preflight`, and the explicit
manual-first capture/verification/import sequence before enabling launchd. No private
policy or live collection is provisioned automatically.

## Bounded catch-up and verified completion

New additive operations:

```sh
# Read-only; no directories, DBs, captures or permissions are created.
dsv krx-scheduler backlog --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler run-due --config "$CONFIG" --config-pin "$CONFIG_PIN"

# FUTURE operator action, after genuine policy review and persistent enablement.
# Not executed during development; real collection remains disabled.
dsv krx-scheduler run-due --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --execute --max-runs 7 --max-batch-seconds 240
```

The batch holds the same runner lock as `run-once` across all dates. It delegates each
single capture to the original locked implementation; no alternative client, database,
policy validator or request ledger exists. Each HTTP request still has one attempt,
zero retries, at most 30 seconds/2 MiB, direct verified TLS and the original durable
quota/duplicate guard. A batch allows 1–7 work items and 1–300 cooperative seconds
(default 7/240). Offline ingestion recovery consumes a work-item slot too. Validation
or filesystem calls cannot be forcibly preempted; this is not hard process isolation.

Before new captures, operational SQLite must pass its original full integrity check.
A successful scheduler summary alone never proves completion. Each completed date is
reconstructed from its original pinned capture/authorization and pinned ingestion
batch, then the stored original response and normalized observations are revalidated.
The shared read-only `verify_stored_capture` reuses the ingestion source verifier and
authoritative batch validator; the write contract and schema remain unchanged.

`krx_collection_backlog_v1` distinguishes:

- `completed`: original source and actual stored batch verified; this is not PIT approval.
- `due_dates`: unrequested missing allowed dates, oldest first, inside the current lag.
- `expired_dates`: missing allowed dates older than the lag; never auto-requested.
- `reserved_without_verified_completion`: an existing request intent blocks requery;
  this includes failed captures and unregistered manual collections, not successful completion.
- `stored_without_verified_history`: rows exist but the matching original request history
  is missing/unregistered. New requests stop until the original history is restored and
  explicitly verified. The DB alone is not a trusted source pin or permission to retry.
- `not_yet_eligible_dates`: current/future observations, never substituted for completed bars.
- `next_request_readiness`: the existing policy/key/quota preview for the oldest due date.
  It is not a request reservation, current calendar proof or independent authorization.

Missing/unavailable values are retained; completed counts do not hide reserved or expired
observations. Dates omitted from the reviewed allowlist cannot be invented as missing
sessions. The existing latest-date `preflight` remains compatible; it may report a
latest-date duplicate while older allowed dates are still due. Use the per-date backlog
preview and original execution-time checks rather than bypassing any denial.

Known failed requests remain individually auditable and are excluded from automatic
requery. A batch stops on its first failure/denial. A later invocation can process other
unrequested dates; the failed date remains explicitly unresolved. An uncertain
`capture_pending` blocks all automatic work until the outcome can be independently
resolved. A trusted `ingest_pending` resumes original ingestion offline before any new
request. No original receipt, manifest, intent, failure or quota reservation is erased.

The original atomic `state.json` v1 and source archives remain authoritative for runner
capture references. Two bounded, config-bound local metadata files reuse existing
private/fsync/atomic-replace/lock utilities: `verified-completions.json` for explicitly
registered external/manual references and `last-batch.json` for the last mutable batch
summary. They survive restart; missing dates are reconstructed from the pinned policy,
source proofs and SQLite. A missing registry does not authorize duplicate collection.
They are not a new database, immutable economic ledger or independent custody store.
Up to 366 combined completions and the existing 512 KiB/file limits apply; large external
plan documents can reach the byte limit before 366 registrations. Normal runner history
retains compact original references. No unbounded retention/crash durability is claimed.

## Register an already stored manual collection (offline)

This is an explicit future operator action, not performed on the real private pilot in
this PR. Supply the original independently retained **plan, capture and batch pins**.
The batch pin must originate from the original trusted ingestion return, not a hash
read solely from the mutable DB. Never create a trusted capture pin from an untrusted
archive's own manifest. Keep private values out of shell tracing, GitHub and support logs.

```sh
dsv krx-scheduler register-completion --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --source-plan "$ORIGINAL_PLAN" --source-plan-pin "$ORIGINAL_PLAN_PIN" \
  --source-attempt manual-first --capture-pin "$ORIGINAL_CAPTURE_PIN" \
  --batch-pin "$ORIGINAL_BATCH_PIN"
```

Registration is read-only for the capture/SQLite and only updates private scheduler
references. It requires an already-ingested, exactly matching full-market batch; it
never imports an old Samsung-only response, sends HTTP, enables scheduling or grants
service permission. Repeating an identical registration is idempotent; conflicting
same-day identities, modified data, wrong pins and absent storage are rejected.

For a new policy/config after renewal, preserve the original source policy and supply
its original reference explicitly:

```sh
dsv krx-scheduler register-completion --config "$NEW_CONFIG" --config-pin "$NEW_CONFIG_PIN" \
  --source-plan "$OLD_PLAN" --source-plan-pin "$OLD_PLAN_PIN" \
  --source-attempt "$OLD_ATTEMPT" --capture-pin "$OLD_CAPTURE_PIN" \
  --batch-pin "$OLD_BATCH_PIN" \
  --source-authorization-root "$OLD_POLICY_ROOT" --source-authorization-pin "$OLD_POLICY_PIN"
```

The original capture archive must still be the configured shared archive. Completed
history may remain outside a renewed allowlist; that never grants permission to request
that date again. All new requests still require the current allowlist and lag. The old
grant is replayed at original response
time, even after expiry; it is never rewritten into a current grant. A matching uncertain
runner pending entry can be cleared only by this explicit registration of the original
already-stored successful source with all pins and the exact original runner identity.
An unrelated registration cannot clear a pending request or conceal a failure.

## Operator rollout and long-term policy procedure

No real policy, pin, archive, operational SQLite or LaunchAgent was modified in this
implementation. In particular the currently issued `daily_limit=1` policy stays one.
For future operations:

1. Review and issue a **new** policy package and pin using the existing recurring
   procedure below; keep the capture archive shared so quota/duplicates cannot reset.
2. Prepare a new independently pinned config. If a prior config already owns runner
   state, use a new private runner directory and explicitly re-register original source
   references. Never edit/re-hash old state to change its config identity.
3. Register the already successful manual collection offline using its original pins.
4. Inspect `backlog` and `next_request_readiness`, SQLite validation, permitted sessions,
   capacity, expiration and any unresolved request. Do not use this feature to retry an
   unknown outcome or fetch an expired date.
5. Only after separate operator approval, enable application execution and later the
   owned LaunchAgent through the original explicit resume workflow. No such enablement
   was performed in development. A manual batch requires the same persistent consent.
6. Future plists invoke `run-due`, not `run-once`. An existing old plist fails strict
   ownership comparison after this upgrade: pause/uninstall it with the old reviewed
   version/config, then preview and explicitly install the new plist. Do not overwrite
   unrelated/changed jobs. The current real Mac has no collection job installed, so no
   native upgrade was performed here.

At 19:00 KST, same-day bars remain excluded. The allowlist fallback is operator-reviewed
scope, not an authenticated calendar. Full historical gaps beyond seven days need a
separate reviewed source/policy boundary; increasing the daily quota cannot remove it.

Completion/history inspection deliberately replays source and batch evidence rather
than trusting cached mutable JSON. Cost grows with retained history, and a full SQLite
audit is performed before an executing batch. The cooperative deadline is rechecked
after this verification and before a new capture; verification itself is not forcibly
preempted. Large histories can exhaust a batch budget without issuing a request. This
PR does not claim constant-time status, faster cold replay or hard memory isolation;
review measured local audit costs before longer retention or larger deployment.

A missing capture ledger alongside stored capture-derived batches also blocks new
requests even when those stored dates fall outside the current allowlist/lag. Missing
quota history or usage lower than recorded same-KST-day responses is rejected; absence
is never a quota reset. Restore and independently verify original history rather than
creating an empty archive or changing pins to bypass the failure.
