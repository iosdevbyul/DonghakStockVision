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
dsv krx-scheduler run-once --config "$CONFIG" --config-pin "$CONFIG_PIN" --execute

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
path and `-m donghak_stock_vision krx-scheduler run-once --execute --scheduled` with the
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

A new request occurs only on a listed current collection day. Weekends, holidays or
other unlisted days skip without HTTP. The candidate is the latest listed date strictly
before the current Seoul date and within the policy's existing 1–7-day lag. The runner
never chooses an older missing date merely because the latest candidate is a duplicate.

**Current policy limitation:** even at 19:00, same-day daily bars are not eligible under
PR #153's minimum one-day lag. This PR preserves that boundary, so Monday typically
collects the prior allowed Friday. No incomplete/current bar or unauthorized historical
warm-up is substituted. Same-day post-close collection would require a separately
reviewed policy/evidence change. Long holidays or downtime can exceed the lag; missed
dates remain visible for a future explicit recovery feature.

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
