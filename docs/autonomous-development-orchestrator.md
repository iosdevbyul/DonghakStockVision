# Track D — Autonomous Development Orchestrator (design only)

## State and non-goals

This document proposes a bounded development orchestrator, **not** an enabled
autonomous agent. Until separate explicit operator approval, GitHub merges
remain manual. Do not deploy a scheduled runner or introduce a token with
write/merge privileges in this phase.

At design time: `main` branch returned `protected=false`; branch protection
details returned 403 (integration lacks access); ruleset enumeration returned
403 (GitHub requires plan/repository eligibility). These are blockers, not
permissions to bypass policy. An administrator must independently verify and
enable branch protection/required checks, or leave auto-merge disabled.

## Control plane

Trello Next (operator-prioritized, explicit scope) -> orchestrator planner ->
isolated feature branch -> Codex worker -> local formatting/type/tests ->
PR creation -> existing full GitHub Actions checks -> independent policy
evaluation -> merge only if authorized -> Trello Done -> bounded next task.

All state transitions must be durable, auditable and idempotent. The planner
records task ID, base SHA, branch, PR number, head SHA, run ID, check results,
risk decision, operator authorization and next transition. Reconciliation
must not re-run unknown-outcome changes; any uncertain state pauses. No
automatic force-push, direct main push, workflow bypass or test reduction.

## Risk classification

**Allowlisted candidate (never sufficient by itself):** pure documentation,
non-executing synthetic tests, and isolated local developer tooling with no
credential, network, database, deployment, trading or CI-security impact.
The change must contain only explicitly allowlisted paths and operations;
renames, deletions, symlinks, generated files and unexpected dependencies
require manual review. A path match is never proof of harmless behavior.

**Manual review required:** application runtime, trading/PAPER, model/signal,
risk policy, KRX collection, data authorization and eligibility, production
SQLite/storage, API security/exposure, dependencies/lockfiles, workflows,
scripts that can execute commands, settings, monitoring or deployment,
credentials, and any unclassified/ambiguous change.

**Always prohibited for unattended automation:** real exchange/broker orders,
unapproved KRX requests, disabling tests/security controls, editing protected
evidence, live database mutation, bypassing request budgets, broadening
authorization, secret exfiltration, modifying branch protections or GitHub
Actions permissions. Never infer PAPER eligibility from unreviewed data.

## Independent merge gate (future implementation)

The gate fails closed unless **all** hold:

1. A separately recorded operator approval enables autonomous merge for this
   exact repository, low-risk classes, UTC expiry, nightly work window, and
   maximum merges/day. Scope and revocation are persisted outside PR content.
2. Branch protection and all required checks were independently verified by
   an administrator and are enforced (not only described in a workflow).
3. PR base is current main; the reviewed head SHA equals the tested SHA and
   remains unchanged until merge. No merge conflicts or unexpected changes.
4. The change matches an explicit low-risk allowlist and independent review
   produced no policy-sensitive findings. Any uncertainty becomes manual.
5. Full existing CI, Ruff lint and format, strict Mypy, pytest, architecture,
   build and installed-wheel smoke checks succeeded on the correct commit.
   Do not consider queued, skipped, stale or partial checks successful.
6. No outstanding review requests, conversations, policy findings or pending
   manual operator holds. Required human approvals remain required.
7. The merge actor cannot bypass branch protection, and the merge method
   preserves GitHub's rules. Recheck head/base/checks immediately before merge.
8. Idempotent Trello and audit updates succeed or are reconciled without
   accidentally repeating a merge. On unexpected outcomes, halt.

With current settings, the merge gate **must return DENY**. A CI-passing PR
does not grant approval. This design PR must not itself introduce a merge bot.

## Execution limits

One active development PR per track; one self-hosted macOS CI job at a time.
Maximum two code-fix retries on the *same* branch after a verified failure.
No endless resubmission after queued/running CI or ambiguous runner outcomes.
Use bounded time, token/spend, daily PR and merge limits. Pause upon runner
offline, permissions error, rate limit, changed base, security finding or
conflicting cross-track changes. Prevent the orchestrator from modifying its
own policy or granting itself higher privileges.

## Phases

1. **Design only (this PR):** publish policy, explicit hazards and operator
   approval template. Auto-merge disabled; no schedules or secrets.
2. **Dry-run controller:** read-only GitHub/Trello observer and simulated gate
   evaluations. No code generation, merging, PR mutation or credentials with
   write privileges. Show reproducible fail-closed decisions and tests.
3. **Assisted development:** bounded Next task, branch/PR/CI/retry; manual
   merge remains. Measure duplicate prevention, retries and crash recovery.
4. **Conditional auto-merge pilot:** separate explicit operator approval,
   admin-verified enforced branch protection and expiry. Only genuinely
   low-risk explicitly allowlisted changes, one trial at a time; manual
   emergency stop and complete audit required.
5. **Limited unattended operation:** only after sustained evidence of safety.
   Never infinite, never unrestricted, never crossing KRX/PAPER approval walls.

## Operator approval template (NOT an approval)

- Repository: iosdevbyul/DonghakStockVision
- Authorized risk classes: NOT APPROVED
- Auto-merge enabled: **NO**
- Approval principal / date / expiry: UNSET
- Max PRs, merges, time window and spend: UNSET
- GitHub protected-branch verification: NOT VERIFIED
- Required checks and policy evaluator: NOT VERIFIED
- Kill switch and operator alert channel: NOT CONFIGURED
- Permitted KRX network requests / real money orders: **ZERO**
- Revocation and emergency-stop procedure: NOT VERIFIED

No default values convert unset fields into consent. This record is not an
authorization artifact and must never be accepted as one.

## Trello as the operator's primary dashboard

The operator monitors development through Trello; GitHub remains the source of
truth for commits, PR states, check runs, reviews and merge results. Trello
updates are a required part of every orchestrator state transition, not an
optional end-of-run report.

- **Dashboard:** project-wide summary, active Track A/B/C/D tasks, current CI
  queues, failed or blocked work and last successful synchronization. Do not
  call a stale snapshot current.
- **Next:** scoped and authorized future tasks. A card enters In Progress only
  after a worker actually claims the task.
- **In Progress:** work actively being implemented, tested, repaired or
  reviewed for merge. CI success alone never moves a card to Done.
- **Backlog:** held work requiring operator authorization, manual intervention
  or prerequisite resolution. A paused task must not be invisibly retried.
- **Done:** only after independently verified PR merge and successful
  post-merge reconciliation. A PR closed without merge is not Done.

Each active task card records repository, Track, task identifier, branch,
base SHA, PR URL and number, tested head SHA, GitHub Actions run URL/ID,
last observed CI status, validation result, risk class, merge policy, blockers,
last synchronized timestamp and next expected transition. Unknown fields are
marked **UNKNOWN**, not optimistically reported as successful. Cards and
comments remain entirely English.

### Event and reconciliation rules

1. Claim an explicitly scoped Next card using a stable Trello card ID, then
   move to In Progress. Prevent duplicate PRs or parallel work for that ID.
2. Record branch creation and successful commit/PR creation with immutable
   identifiers. Do not claim the PR exists before GitHub confirms it.
3. Record CI queued/running/success/failure against its exact head SHA and run
   ID. Failed checks retain the card in In Progress during bounded retries.
4. On blocked authorization, runner outage, missing permissions, or uncertain
   external results, move to Backlog when work is genuinely paused and record
   the reason. Do not trigger another KRX request or unknown-outcome merge.
5. Record separate operator-approval and merge decisions. Auto-merge remains
   disabled until the independent policy requirements and authorizations hold.
6. On confirmed merged PR, move to Done and write the merged SHA and status.
   GitHub confirmation is required; a Trello card cannot authorize merging.
7. Periodically reconcile GitHub and Trello. Use idempotent event identifiers
   (task ID + PR number + head SHA + run ID + event type) so retries update or
   deduplicate instead of posting duplicate comments or generating new cards.
8. If Trello writes fail, retain the event in a durable outbox for bounded retry
   and flag dashboard synchronization as stale. Never silently report Done.
   Trello errors must not relax GitHub merge safety checks.

The orchestrator must not treat arbitrary Trello descriptions or comments as
executable instructions or authorization. Only validated task fields from
approved scope can schedule work. Trello API credentials must be separately
scoped and stored outside repository history; no credential is provisioned by
this design phase.

### Proposed card status vocabulary

- `PLANNED` — approved future task, in Next.
- `IMPLEMENTING` — worker actively changing isolated branch.
- `PR_OPEN` — PR created; CI may be queued.
- `CI_RUNNING` / `CI_FAILED` / `CI_PASSED` — exact verified GitHub status.
- `AWAITING_MANUAL_MERGE` — checks passed but merge requires operator.
- `BLOCKED` — needs attention, with reason and Backlog placement.
- `MERGED` — verified merged into main, in Done.

The initial dry-run phase may *simulate* Trello transitions without writing
cards. Actual authenticated Trello mutations are a distinct, reviewed
capability introduced later; no automatic card-writing agent is enabled by
this document.

## Operator-requested unattended mode (requested 2026-10-31)

The operator explicitly requests an unattended loop: implement a bounded task,
open a PR, wait for the complete CI result, merge eligible changes, reconcile
Trello, and continue with the next authorized Next card. Trello is the primary
operator-facing dashboard and must be updated at every verified transition.

This request authorizes **design and preparation**, not ignoring the hard
preconditions above. As of this revision `main` reports
`protected=false`, and mandatory check/branch protections cannot be
verified. Thus the effective merge setting remains **OFF** and cannot
automatically turn on because an operator is away. The initial controller
must fail closed, log `PROTECTION_UNVERIFIED`, and continue only with
safe non-merge steps. Runtime and PAPER/KRX behavior changes remain
manual-review-only regardless of successful CI.

Once independently verified branch protection, required checks, credentials,
audit store and kill switch are available, the operator may provide a
separate time-bounded approval for a **specific low-risk allowlist**.
Only then may a reviewed implementation enable automatic merges. No
unbounded/infinite agent, auto-approval of its own code, or self-modification
of the merge policy is permitted.
