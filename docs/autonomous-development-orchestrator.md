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

## Trello conventions

English-only cards, descriptions and comments. Next = approved future tasks,
In Progress = presently worked on, Backlog = held work, Done = verified merged
work. Record exact PR/head/CI evidence without promising runtime success.
