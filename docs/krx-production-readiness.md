# KRX production collection readiness and first run

This guide prepares existing read-only collection; it does not grant permission or
execute a request. No real policy was issued, no LaunchAgent installed/enabled and no
real provider request made while implementing this workflow. All execution examples
below are **future operator actions**, not development results.

## Current evidence and remaining operator review

The retained private evidence contains two screenshots (approval and request), a
`Spec.docx`, and operator-copied `terms.rtf`. Read-only inspection confirmed:

- Both retained daily-service pilot packages record approval from **2026-09-29 through
  2027-09-28**, and their permission/terms/specification hashes match the retained bytes.
- The request screenshot contains `GET`, `AUTH_KEY` and `basDd`. The specification
  contains `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`, `basDd`, `OutBlock_1`,
  instrument identity and daily-price fields. Authentication is corroborated by the
  request screenshot; it is not claimed to occur in every specification paragraph.
- Copied terms include Articles 1–13, the 2025-12-26 effective date and 10,000 requests
  per key/day. They are operator-copied webpage text, not a provider-issued download.
- The operator-confirmed entitlement is existing approval history. Local screenshots,
  document hashes and old package classifications do not establish a cryptographic
  provider attestation or independent custody.
- No new private `recurring-policy.json` was found. A live full-market policy, its pin
  and a pinned scheduler configuration are not provisioned by this PR. No actual
  production-ready result is claimed from synthetic tests.

Do not request replacement approval screenshots or silently broaden an old one-ticker
policy. A separately reviewed prospective operating policy is needed, not a new KRX
service entitlement. Read the [exact existing recurring contract](krx-full-market-recurring.md)
and [scheduler configuration](krx-daily-scheduler.md).

## Minimal provisioning checklist

The operator must explicitly review and confirm these settings before issuing the new
`operator_full_market_recurring_v1` package:

| Setting | Required review |
|---|---|
| Service | `stk_bydd_trd`, GET, exact HTTPS endpoint above; KOSPI/KRW full-market daily response |
| Entitlement | Preserve 2026-09-29 through 2027-09-28; no basic-information API permission substitution |
| Policy window | Explicit canonical UTC start/review/end; end exclusive, at most 366 days, wholly within approval |
| Session allowlist | Explicit sorted dates independently checked against authoritative session evidence; no automatic weekday expansion |
| Observation lag | Existing 1–7-day maximum; never today's incomplete bar; current collection day must also be allowlisted for the scheduler |
| Budget | Initially `daily_limit=1`, reviewed service limit 10,000; each capture 1 request, 0 retries, concurrency 1, 30 seconds, 2 MiB |
| Terms and originals | Review retained source bytes and applicable private/noncommercial/no-third-party-use conditions; preserve originals |
| Origin | `transport=provider_https`, `document_origin=operator_reviewed_source_copies` for the retained screenshot/copied-RTF combination |
| Uses | Private capture true; forward signal and PAPER false |
| Credential | Privately bind the actual existing key using the original credential-binding contract; never print it or its digest |
| Paths | New canonical private full-market archive, separate operational SQLite, runner directory and stable interpreter/working directory |
| Pins | Independently retain policy/config/daily-plan/capture pins outside mutable packages; do not invent values |

No concrete policy start/end, new session list or archive identity is silently chosen
here. Those are the remaining operating-scope confirmation, including storage capacity
and explicit permission for the first request. Once that bounded recurring scope is
reviewed, repeated daily reapproval is unnecessary within it. Expiry/revocation/changed
terms and scope still require review. Provider counters remain account-wide; existing
local counters cannot know another application's usage.

Create a **new**, mode-0700 policy directory containing exclusive mode-0600 copies named
`permission.bin`, `terms.bin`, `specification.bin`, and `recurring-policy.json`. Use the
exact fields in the existing recurring guide; preserve original private files and old
packages. No new permission registry or signature scheme is required. The existing
signed independent-review path remains unchanged.

Canonicalize the reviewed JSON with `FrozenJSON.freeze`, then retain its `identifier`
separately. Hash original bytes using SHA-256. The actual private credential digest
uses `SHA256(b"DonghakStockVision:krx-credential-binding:v1\0" + key.encode())`, exactly
as `verify_credential` checks. Bind it privately; this is not a KRX-issued attestation.
Do not use a TEST credential, synthetic originals or placeholder pin for production.
The existing authorization verifier must accept the package before any live capture.

## Prepare private paths and storage

Set the following variables to the actual reviewed private absolute locations. These
examples deliberately contain no private user paths, credentials or pins:

```sh
umask 077
# Only after reviewing a NEW full-market archive location; never reuse the old pilot.
mkdir -m 700 "$CAPTURE_ROOT"
# Initializes the separate operational store, never the historical research DB.
dsv market-storage init --root "$OPERATIONAL_ROOT"
dsv market-storage validate --root "$OPERATIONAL_ROOT"
dsv market-storage status --root "$OPERATIONAL_ROOT"
```

If already initialized, inspect/validate rather than replacing anything. Keep configured
storage/archive limits and sufficient free space. No automatic retention, rotation,
backup or independently authenticated custody is implied.

Prepare the existing `krx_scheduler_config_v1` template with reviewed policy root/pin,
archive root, operational/runner roots, absolute installed venv Python and working
directory, explicit private credentials file (or process environment), hour 19, minute
0 and timezone `Asia/Seoul`. Keep its canonical pin separately. The existing dotenv key
name is `KRX_API_KEY`; the outgoing provider header is `AUTH_KEY`. Never put a key in a
plist, command argument, shell trace, Git or a support report.

## Offline preflight

```sh
dsv krx-scheduler preflight --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler status --config "$CONFIG" --config-pin "$CONFIG_PIN"
dsv krx-scheduler install --config "$CONFIG" --config-pin "$CONFIG_PIN"
```

The last command is **installation preview only**. None of these commands make KRX
requests, create a policy, initialize a DB, write runner state or install a LaunchAgent.
Preflight rejects all activation/network flags. Invalid config/pin returns the existing
CLI validation error (exit 2). Otherwise it emits `krx_production_preflight_v1` JSON;
a blocked report can exit 0, so inspect its readiness fields and blockers.

The report uses the original policy/source/credential/quota verification and operational
store's full integrity check. It reports service/policy validity, selected completed
session and request budget, duplicate/pause state, private archive access, SQLite counts,
source integrity, capacity, pending capture state, host timezone and OS/application
scheduler enablement. It returns no key, binding digest, policy/config pin or private
filesystem path. `verified_locally` means local checks, not provider authentication.

Interpret the flags separately:

- `ready_for_manual_provider_capture=true`: configured real-provider scope passed the
  current offline checks. Explicit live request confirmation is still required.
- `ready_for_mock_capture=true`: only explicitly labelled mock evidence passed. It
  never means production permission or a genuine provider response.
- `ready_for_scheduled_collection=true`: provider checks plus Seoul host timezone and
  effective enabled/loaded LaunchAgent/application state passed. It is expected false
  before the first manual run. Disabled scheduling does not prevent direct manual
  `forward-capture capture` with its own explicit authorization flags.
- A timeout/conflicting LaunchAgent can block scheduler inspection without granting or
  denying direct manual service entitlement. A timezone mismatch blocks scheduled
  readiness; it does not change native Seoul date validation for manual capture.

Missing policy or credential, altered originals, wrong service/scope/pin, expired
approval, no eligible session, duplicate/exhausted quota, missing/private-path failures,
bad SQLite, insufficient capacity or an uncertain pending capture produce explicit
blockers/actions. Inspect the existing `forward-capture authorization-readiness` output
for detailed policy rejection codes. Preflight is not a quota reservation; recheck at
execution. All persisted/report instants use UTC; trading dates remain Seoul dates.

For the manual `forward-capture` commands below, the credential must already be in
the private process environment, or in the existing `.env` loaded from the reviewed
working directory. The scheduler config's `credentials_file` is not automatically
read by the separate manual CLI. Never shell-source an unreviewed dotenv file or
print/export the key into a shared command history.

## Prepare the daily plan without network access

After provisioning and review, use the existing owner's exact canonical contract.
The following operator snippet derives a daily plan and its pin from the existing
pinned policy and runner selection. It **does not issue a policy or authorize a call**.
`PLAN` and `PLAN_PIN_FILE` must be new, distinct private files outside the mutable
archive/policy package. Do not rerun with existing output files.

```sh
export CONFIG CONFIG_PIN PLAN PLAN_PIN_FILE
"$PYTHON" - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path
from donghak_stock_vision.storage.krx_scheduler import load_config, _plan
from donghak_stock_vision.storage.krx_recurring import verify_recurring_authorization
cfg = load_config(Path(os.environ['CONFIG']), os.environ['CONFIG_PIN'])
at = datetime.now(UTC)
plan, _ = _plan(cfg, at)
verify_recurring_authorization(plan, 'manual-first', cfg.reference, None, at)
paths = [Path(os.environ[n]) for n in ('PLAN', 'PLAN_PIN_FILE')]
assert paths[0].resolve() != paths[1].resolve()
assert all(p.is_absolute() and not p.exists() for p in paths)
for p, value in zip(paths, (plan.document.payload_json, plan.document.identifier), strict=True):
    with p.open('x') as out:
        os.chmod(p, 0o600)
        out.write(value)
PY
# Read the separately retained plan pin locally; do not publish it.
PLAN_PIN=$(cat "$PLAN_PIN_FILE")
dsv forward-capture authorization-readiness \
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt manual-first \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1
dsv forward-capture dry-run \
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt manual-first \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1
```

Inspect endpoint, basDd, policy validity, origin, actual credential binding, completed
session/lag, configured budget, quota and duplicates. Stop if either preview is blocked.
A date suggested by an unverified calendar or retrospective source is insufficient.

## First manual live capture: future explicit operator action

**Do not execute during development.** Confirm this exact single-request plan and
independent pins with the operator first. Disable shell tracing, ensure private output
handling and use the existing explicit network/authorization flags:

```sh
dsv forward-capture capture \
  --root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt manual-first \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1 \
  --allow-network --confirm-operator-authorization
```

Retain the returned original capture pin separately as `CAPTURE_PIN` without publishing
it. Any transport, service/limit, invalid-content or uncertain outcome stops this
procedure: **no automatic retry or quota refund**. Preserve original failed evidence.
One failed/uncertain request can consume the entire initial daily budget.

```sh
dsv forward-capture verify --root "$CAPTURE_ROOT" \
  --plan "$PLAN" --plan-pin "$PLAN_PIN" --attempt manual-first --pin "$CAPTURE_PIN"
dsv market-storage ingest-capture --root "$OPERATIONAL_ROOT" \
  --capture-root "$CAPTURE_ROOT" --plan "$PLAN" --plan-pin "$PLAN_PIN" \
  --attempt manual-first --capture-pin "$CAPTURE_PIN" \
  --authorization-root "$POLICY_ROOT" --authorization-pin "$POLICY_PIN" \
  --authorization-policy operator_full_market_recurring_v1
dsv market-storage validate --root "$OPERATIONAL_ROOT"
dsv market-storage status --root "$OPERATIONAL_ROOT"
dsv market-storage duplicates --root "$OPERATIONAL_ROOT"
dsv market-storage latest --root "$OPERATIONAL_ROOT"
dsv krx-scheduler status --config "$CONFIG" --config-pin "$CONFIG_PIN"
```

Check successful HTTP/content validation, requested dates, actual full-market row count,
unique market/date/instrument identities and source-bound receipt/manifest/batch replay.
**940 is a synthetic test count, not a guaranteed universe size.** SQLite validation
must report valid; duplicate records must remain zero. Idempotent offline re-ingestion
with the same trusted source cannot spend provider quota. Local storage stays
PIT/training/OOS/signal/PAPER/execution ineligible.

## Enable only after verified manual success

On a later eligible scheduled date, rerun preflight and inspect original policy pause,
local quota and pending state. The manual-first capture is not silently adopted as a
scheduler completion: its original duplicate-date guard prevents another request, and
its SQLite data remains visible in status. Do not clear duplicates or use another root
to bypass the daily budget. Future allowed sessions use the same reviewed recurring
policy; no new daily service approval is required.

```sh
# Actual installation/activation is a separate explicit operator action.
dsv krx-scheduler install --config "$CONFIG" --config-pin "$CONFIG_PIN" --apply
dsv krx-scheduler resume --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --apply --allow-network --confirm-operator-authorization
dsv krx-scheduler status --config "$CONFIG" --config-pin "$CONFIG_PIN"
# Pause future collection if policy, terms, quota, storage or account state changes.
dsv krx-scheduler pause --config "$CONFIG" --config-pin "$CONFIG_PIN" --apply
```

Installation alone stays disabled. Native calendar triggers require the Mac's actual
system timezone to be Asia/Seoul; setting only a child `TZ` is insufficient. The
unchanged minimum one-day lag still selects an earlier allowlisted completed session
at 19:00. Long gaps, ambiguous sessions and unknown outcomes require explicit review;
no historical backfill, signal, PAPER or brokerage action is introduced.
