# Corporate-action final coverage

This work is the next evidence gate after the security-lifecycle reconciliation.
It must not run inference, generate signals, adjust prices, or execute a backtest.

## Goal

The final coverage layer may clear `BLOCKED_ON_CORPORATE_ACTION_EVIDENCE` only
when every security in the locked 993-security universe has a reviewed record
covering the required period from 2024-06-14 through 2025-07-07.

For each exact six-digit security, a complete record requires:

- exact historical security identity covering the full required period
- official identity evidence
- complete official query execution for every approved corporate-action class
- authoritative semantics that can support both event and no-event conclusions
- official effective-date evidence for every confirmed event
- no inference from price gaps, issuer names, current mappings, or disclosure titles
- no price adjustment or successor/predecessor price stitching

The approved event classes remain the existing `EVENT_CLASSES` contract:
split, reverse split, capital reduction, bonus issue, stock dividend,
paid-in capital, rights, merger, division, share exchange/transfer, name change,
security replacement, listing restructuring, and other discontinuity.

## First implementation slice

`corporate_action_coverage.py` defines the fail-closed record and universe
summary contract. It deliberately accepts reviewed evidence only. It does not
collect official responses and therefore does not change the current blocker.

A security can be published as either:

- `VERIFIED_EVENT_PRESENT` when every reviewed event has an official effective
  date and official source; or
- `VERIFIED_NO_RELEVANT_EVENT` when the full identity, period, event-class, and
  authoritative no-event semantics are all verified.

Anything less remains blocked. Missing security records are not filled or inferred.

## Completion sequence

1. Lock this contract and its tests.
2. Build additive official acquisition for the required identity and event sources.
3. Preserve source bytes and secret-safe receipts.
4. Review and normalize exact security identities and effective dates.
5. Replay the evidence offline into one record per locked-universe security.
6. Require 993 / 993 complete corporate-action coverage before changing the
   integrated blocker state.
7. Only after that gate passes, proceed to runner-tail integration.

The current lifecycle evidence remains valid and unchanged. This layer is additive
and must link back to the existing DART and security-lifecycle fingerprints in the
later integrated artifact.
