# Shared account reservations and execution v1

This is a deterministic research account adapter over the existing Ledger,
Decision Bridge, Execution and Position History validators. It introduces no
broker, model scheduler, strategy, cash allocator or second P&L ledger.

## Investigation and compatibility decision

The ledger already keys reservations by immutable accepted order ID and positions
by exact ticker strings. `reserve_buy` checks available cash, including the
explicit notional and cost ceiling, and prevents a second active BUY for the same
ticker. `reserve_sell` decrements sellable shares and increments reserved shares.
Ledger fills consume only the referenced order's reservation. All transitions
increment the account revision, preserve sequence/time monotonicity and check
cash and share conservation. Cancellation/expiry/rejection release the remaining
reservation of only that order. The ledger already supports partial fills and
partial SELL remainder cancellation; the public virtual execution policy supports
**full fills only** and continues to reject partial-fill requests.

The limitation was above this ledger. Legacy `execute` pins the entire ledger to
the reservation checkpoint. A later reservation, even for another ticker, changes
that state. `BacktestRunner.process_event` therefore reports
`concurrent_reservation_unsupported` when another order is pending. Removing that
check alone would leave every earlier order failing the execution revision check.
Position History also reproduces the original pinned execution when validating
fill receipts, so relaxing only execution would break evidence replay.

The legacy runner, schemas, default execution call and single-ticker reports are
unchanged. Its pending-order restriction remains deliberate. Full multi-ticker
model scheduling and portfolio research are deferred. Instead, this PR exposes a
small reusable account-level interface and a deterministic synthetic harness in
`tests/backtest/test_shared_account.py`. The harness projects decisions, admits and
reserves each in ticker-priority order against the latest ledger; it then fills
multiple accepted orders through the new account transition. Real historical
multi-ticker input construction remains unsupported. Original HGB and linear
inference paths and shared evidence v2 remain untouched.

## Explicit optimistic transition

A caller can opt into `execute(..., account_transition=transition(...))` with:

```json
{
  "version": "shared_account_execution_v1",
  "expected_revision": 2,
  "expected_ledger_hash": "SHA-256 identity of the current verified LedgerState"
}
```

Both values are caller-supplied expectations. The separate `expected_revision`
argument must agree with this token. Before execution, the current ledger is
restored from its checkpoint and compared to the expected revision **and** hash.
A stale token is rejected, never automatically rebased. Accepted orders continue
to be admitted and reserved against their exact current revision. Two candidates
prepared from an old shared revision cannot both be accepted without recalculating
the second decision/admission against the new account.

For an execution opt-in, the original reservation checkpoint must be a literal
command-history prefix of the verified current ledger, bound to the same run,
tape and ledger policy. Every original admission, candidate/order identity,
execution policy, decision view and reservation event binding is still checked.
The target order must equal its original reserved state, and no intervening
command may target that order, even an amendment that restores the old amount.
Other orders may be reserved, filled or released in the meantime. An unrelated
branch with the same revision or cash balance is not an ancestor.

The filled receipt includes the exact `account_transition` token; existing fill,
source event, decision/candidate/acceptance clock and policy references stay intact.
Position History replays that token against the actual pre-fill ledger prefix.
It still reconstructs FrozenAnalysis, Phase 3 decisions, acceptance and execution,
checks every fill source in the frozen tape and reconciles the final ledger.
No persisted ledger/checkpoint/order schema is changed. Omitting the opt-in retains
legacy pinned-revision behavior and byte-identical legacy output/hash semantics.

Identifiers remain distinct:

- Decision result ID: source of the admitted request.
- Candidate ID: submitted VirtualOrder identity from admission.
- Accepted order ID: open VirtualOrder, bound to acceptance time and ledger policy.
- Reservation ID: immutable reserve LedgerCommand identifier/checkpoint reference.
- Fill event ID: existing `digest({"full_fill": accepted_order_id})`.
- Fill hash: VirtualFill contract identity, including original source/time/costs.

There is no wall-clock timestamp, thread or asynchronous account mutation.

## Cash, securities and lifecycle

Cash is KRW, cash-only and long-only. Available and reserved cash remain
nonnegative; total cash is their sum, with exactly the sum of pending order cash
reservations in reserved cash. Rejected admissions and executions return the
unchanged immutable ledger. Each BUY's notional plus explicit cost ceiling must
fit its admission budget and currently available funds. A fill's actual price,
fees, taxes and adverse slippage use the existing full-fill policy, and its cost
cannot spend another order's funds. A full fill releases its own unused ceiling.
There is no top-up from future cash and no margin, borrowing or short sale.

Positions use exact security ticker strings, including `000075` and `00088K` in
the synthetic harness. No corporation-name or suffix inference occurs. Each SELL
reserves unreserved sellable quantity; holdings, sellable and reserved quantities
reconcile per ticker and to the sum of all that ticker's order reservations.
Existing Decision Engine/admission guards continue to block another pending order
for the same ticker. The lower ledger can represent disjoint same-ticker SELL
reservations and partial remainder accounting; tests explicitly verify that
competing SELLs cannot reserve the same shares.

`release_pending` requires the caller's current revision/hash, the authentic
acceptance and the unchanged target reservation ancestry. It emits an explicit
`shared_account_release_v1` receipt containing acceptance/order IDs, predecessor
and successor ledger hashes and the original ledger command. Cancellation returns
only that order's remaining cash/shares; expiry requires the original exclusive
expiry boundary. Rejection of a partially filled order remains forbidden by the
ledger. This adapter does not invent a partial virtual fill or amend/rebase a
changed target reservation.

Failed execution **does not automatically release** a reservation: that is the
existing execution/runner policy. It remains pending until an explicit validated
cancellation, expiry or rejection. No synthetic terminal liquidation or automatic
expiry timer is added. Accepted unfilled orders remain visible in `account.orders`.

For shared execution, a repeated fill attempt is rejected as
`reservation_changed` because the target is already terminal; balances do not
change. The lower ledger's existing exact-event/fill idempotency remains intact,
as does legacy execution idempotency. Conflicting fill/cost identities still fail.

## Canonical finite execution batches

`execute_pending(ledger, tape, requests, expected_revision=..., expected_ledger_hash=...)`
checks the batch's starting CAS token and then sorts explicit fill attempts by:

1. Virtual observation cutoff in UTC.
2. Source market event timestamp in UTC.
3. Existing contract kind (`market_event`; all attempts are fills).
4. Exact ticker string.
5. Accepted order ID.
6. Immutable source market event ID.

The event ID includes the original sequence and source evidence. Each order may
occur only once in a batch; duplicate/conflicting requests are rejected before
any transition. This does not renumber or rewrite the frozen tape. Permuted request
records referencing the same tape have identical results. Frozen tape sequence
and availability contracts remain mandatory and are not replaced by sorting.

Ledger command sequences are assigned monotonically from the latest ledger, and
each individual execution records its then-current CAS token. There are no
admissions interleaved into this fill-only batch and no imaginary intermediate
available cash. A batch is a sequence of individually atomic attempts, not an
all-or-nothing transaction: failures leave their orders pending while other
attempts can succeed. Explicit release is a separate operation. The fixture's
admission priority is the existing `ordered_tickers` policy; callers must project,
admit and reserve sequentially in that declared order. This API does not allocate
capital among a set of stale simultaneous decisions.

Every source event must still occur strictly after the decision, candidate and
acceptance timestamps and sequences. Fill-known-at, public price field release,
quality, source evidence and historical next-session/open restrictions remain
unchanged. Same-time higher sequence alone cannot permit an earlier/same-time
market event to fill an order. Daily OHLCV does not become intraday evidence.

## Shared valuation and evidence

`account_view` first validates all fill evidence with existing Position History.
It exposes the verified ledger (cash available/reserved, per-order lifecycle,
per-ticker held/sellable/reserved quantities, costs/fills and account realized P&L),
plus the existing Performance `valuation` projection. Every held ticker needs a
public, quality-acceptable, sufficiently fresh mark. Missing/stale marks retain
null total equity and unrealized P&L, per-ticker reason codes, known subtotals and
coverage counts. An unmarked position is not valued at zero.

Per-ticker realized P&L is the sum of changes in the **existing ledger's realized
P&L** while replaying that ticker's fill commands, under the same Decimal context.
It is reconciled to account realized P&L. There is no independent cost-basis or
profit formula. Full and partial exits therefore reuse existing average-cost
allocation and fee/tax accounting. The view references original run, final ledger
and validated history hashes; the history's fill/order/decision references link to
the immutable evidence. Existing detailed trading report remains single-ticker;
there is no new multi-ticker strategy or report format claiming more coverage.

## Validation and restrictions

New synthetic tests cover concurrent BUY budgets, independent positions and
cancellations, mixed BUY/SELL, share competition/partial remainder accounting,
canonical fill order, stale/forked/amended reservations, duplicate requests/fills,
failed execution followed by explicit rejection, expiry, unavailable valuations,
per-ticker P&L reconciliation and tampered/missing receipt evidence. Genuine public
linear/HGB synthetic inference also reproduces original fill/account values through
the new transition, including shared evidence v2. Legacy Ledger, Decision Bridge,
Execution, Runner, Position History, Performance, Trading Report and historical
service/public inference regression tests remain required in the offline suite.

No protected Market/Analysis database, source KRX/DART/Security Metadata evidence,
model checkpoint, dataset, OOS output, lock, qualification or approval is modified.
No live market or brokerage service is contacted. Operations, AI publication,
iOS/Web and operational qualification are unchanged. Corporate Actions 0/993 and
blocked, Runner Tail blocked, Run Input Freeze blocked and strict PIT restrictions
remain unchanged. Synthetic mechanics and fixture qualifications prove neither
real profits nor operational/PIT approval.

Next recommended PR: a separately requested bounded five-ticker historical
research scheduler that consumes this account adapter and validates marks for all
held tickers. It is not started here. CI is not queried or awaited, per the user's
explicit override; all local offline checks and package builds are required.
