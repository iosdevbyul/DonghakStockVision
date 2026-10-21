# Deterministic Paper Broker foundation

`trading.paper.DeterministicPaperBroker` is an explicitly simulated, in-memory
adapter for one virtual account with KRW and/or USD balances and KR/US whole-share
long equity positions. It implements the existing paper-only `PaperBroker` port.
It has no network, broker credentials, HTTP route, database, scheduler or live
submission method. `mode` is always PAPER and `simulated` is always true.

## Reuse and boundaries

See the existing [Trading Core](trading-core-architecture.md),
[Risk Engine](trading-risk-engine.md) and
[architecture baseline](modular-architecture-baseline.md) contracts.

The adapter reuses Trading Core `OrderIntent`, `OrderStatus`, `Money`, `Quantity`,
`AccountSnapshot`, `CashBalance`, `Position`, `FillReceipt`, lifecycle/fill
validators, and the existing Risk Engine (`evaluate_risk` / `RiskAssessment` /
`RiskDecision`). Exact numeric/timestamp validation and canonical SHA-256 helpers
are reused. The new `paper_types` module holds only synthetic fixture policy,
commands, order/receipt state and checkpoint values; it imports no application
or infrastructure implementation.

The existing Backtest Ledger is intentionally not generalized: its RunManifest,
KRW virtual ledger policy, execution receipt and provenance/hash contracts are
research-specific and reject USD. Historical Ledger, Position History,
Performance, strategy rules and frozen results remain authoritative and
unchanged. The small native-currency paper book tracks only owned cash/shares,
reservations and actual supplied fixture fills. It does not add another
historical P&L/cost-basis/performance engine or translate historical receipts into
broker instructions. Equity/daily net P&L are explicit supplied risk evidence,
not fabricated by this adapter.

## Explicit configuration and clock

Construct the broker with an unreserved initial `AccountSnapshot`, a complete
`FixturePolicy` and initial `PaperFrame`. Initial cash reservations, share
reservations or unsettled/unavailable initial shares are rejected because no
external pending-order ancestry is supplied. Balances can contain KRW, USD or
both. Initial positions need not imply a known acquisition time.

Every policy field is explicit; no fee, slippage, risk or enabled defaults exist:

- RiskPolicy per supported native currency, including all existing explicit risk
  limits, time-to-live values and risk-day timezone.
- `CurrencyMechanics` per currency with positive power-of-ten price and cost
  quanta. These are synthetic mechanics, not authoritative exchange tick tables.
- Nonnegative buy/sell percentage fees, sell tax and adverse slippage rates below
  one; combined sell fee/tax rates must be below one. Rounding can still make a
  tiny sale uneconomic, in which case it cannot fill.
- Inclusive maximum session observation age and a finite command budget.

A frame contains its explicit ID/clock, typed reference quotes, direct FX,
account-bound equity/daily P&L valuations, exchange session observations and a
paper-fixture enabled/unknown/disabled input. Advance only through `advance(frame)`;
there is no wall-clock read. Regressing clocks or reusing a frame ID with different
contents is an error. Repeating the identical frame is idempotent.

Broker account reads are virtual observations at the explicitly supplied frame
clock, with a state-bound source ID. They do not rewrite original quote,
valuation or initial-source timestamps or claim historical/PIT qualification.
Quotes/sessions retain distinct observation and availability times. Synthetic
fill events assert that their fill facts become available at their explicit event
time; execution and availability are equal by this fixture contract only.

## Risk evidence and acceptance

```python
assessment = broker.preflight(order)
receipt = broker.submit(
    order,
    idempotency_key="explicit-submission-key",
    risk=assessment.to_decision(),
)
```

Preflight uses the broker-owned current account, all pending remaining orders,
configured risk policy and current frame. The caller cannot replace available
cash, sellable shares or the pending inventory. Submission **always re-runs the
existing evaluator internally** for a new valid intent. Supplied risk evidence
must exactly equal the current full-intent-bound decision. Missing, forged or
stale evidence denies; a previous allow object is never trusted as a grant.
Independent structured RiskAssessment rejection codes remain on the receipt,
alongside broker-specific reason codes such as `risk_evidence_missing` or
`risk_evidence_mismatch`. Bad command/domain structures raise validation errors
without committing account state.

The existing `submit_paper(order, risk)` protocol uses the explicitly supplied
order ID as its submission idempotency key and returns OrderStatus. The richer
`submit` method requires a separate explicit key and returns the receipt. Use one
consistent submission key per order; a new key cannot resubmit an existing ID.

Acceptance reserves resources and returns OPEN, without a fill. Market-closed or
unknown session evidence can therefore leave a risk-approved order pending;
risk approval and execution eligibility are different facts. No decision or
approval here confers operational, live, P1/P10, strict PIT or publication status.

## Reservations and accounting invariants

Buys reserve native-currency cash. Market buys pin an acceptance-time price cap:
reference price multiplied by `(1 + slippage_rate)`, rounded upward to the price
quantum. Limit buys use the exact quantum-compatible limit as their cap.

Per-share reservation is cap plus the **per-share upward-rounded buy fee**.
Multiply that bound by the outstanding whole-share quantity. This conservatively
covers the extra fee rounding of arbitrary partial fills down to single shares;
a single full-order fee estimate would not do so. The buffer passed to Risk is
the nonnegative difference between this bound and its conservative reference/
limit notional. The existing Risk Engine may therefore reject an order more
conservatively than the eventual hold, e.g. an unmarketable low limit buy.

Sells reserve sellable shares and never reserve or credit hypothetical proceeds.
Current available cash already excludes other orders' holds. Pending buys remain
in the risk exposure projection; pending sells never free exposure in advance.

After each candidate transition, before the single commit point, verify:

1. Available/reserved cash and share counts are nonnegative and well typed.
2. Cash by native currency equals initial cash minus buy gross/costs plus sell
   gross minus costs across the actual unique FillReceipts.
3. Reserved cash equals outstanding buy holds; reserved shares equal outstanding
   sell quantities. Sellable plus reserved shares equals owned shares.
4. Owned shares equal initial shares plus actual buys minus actual sells.
5. Requested quantity equals filled plus remaining plus explicitly closed quantity.
   Terminal orders have no holds; filled orders have the full requested fill total.
6. Order IDs, fill IDs and operation/key pairs are unique. FillReceipt order
   bindings and aggregate quantity are checked with existing Core validators.
7. Revision matches the recorded command count and the complete immutable owner
   matches its pinned state digest.

State transitions are serialized with a local RLock and committed through one
immutable head replacement. Validation/arithmetic failures leave the prior head,
reservations and idempotency journal unchanged. This is in-process atomicity,
not a durable database transaction or a security boundary against arbitrary
Python code execution.

## Explicit fills and order lifecycle

```python
broker.advance(later_frame)
receipt = broker.execute(FillEvent(
    event_id="explicit-liquidity-event",
    order_id=order.order_id,
    quantity=Quantity(2),
    at=later_frame.now,
    source_id="synthetic-liquidity-source",
))
```

The event asserts deterministic fixture liquidity for its exact quantity; no fill
probability, volume participation, matching queue or real liquidity is inferred.
An order must be OPEN/PARTIAL, the event must match the current clock and occur
strictly after acceptance, and the reference price must be observed after
acceptance. Sessions must be explicitly open, known and within their exact
microsecond-aware age bound. Future, missing or stale quotes/FX/valuations cannot
produce a fill.

Every fill attempt internally evaluates the **entire remaining intent** against
current risk evidence. Its own hold is temporarily made available only for this
check and its own pending candidate is removed, avoiding double counting. All
other holds and pending buys remain. No actual hold is released by this temporary
risk view. Disabled state, daily loss or other risk failure returns a PENDING
attempt and keeps the outstanding hold until cancellation or a later eligible
explicit event.

Fixture execution prices are reference times `(1 + slippage)` for buys (round up)
and `(1 - slippage)` for sells (round down). A limit must still be satisfied after
slippage/quantization; otherwise the attempt is pending. Percentage fees and sell
tax round upward per **actual fill** to the configured cost quantum. Buy tax is
zero in this synthetic policy. These are supplied mechanics for both markets,
not a claim about actual Korean or US taxes, fees or execution pricing.

A risk-eligible buy fill cannot exceed its pinned price cap or spend another
order's hold. Once risk/eligibility checks pass, an over-cap calculation or
nonpositive/negative-net execution rejects the unfilled
remainder and releases its reservation. With no prior fills the order becomes
REJECTED; after partial fills it becomes CANCELLED, retaining its real fills in
accordance with existing lifecycle transitions. Invalid/overfill commands instead
raise without changes. No rejected or pending attempt invents a FillReceipt.

Receipts explicitly expose the frame ID, fixture policy ID, operation key,
quote source ID and session source ID when available, along with benchmark,
applied adverse slippage, Core FillReceipt fees/taxes and structured risk reasons.
Unavailable sources stay null; a pending receipt does not assert a fill.

Successful partial fills consume only their own reserved portion; unused fee/
price allowance is immediately returned. Remaining quantities retain holds.
Cancellation releases only the remainder. A full fill becomes FILLED and has no
hold. Cancellation of an already terminal order is an unchanged observation.
There is no automatic liquidation, expiry, retry, activation or market discovery.

The executable synthetic fixtures in `tests/trading/test_paper.py` include a
KRW 900 account with ten shares and a five-share buy at reference 10. With explicit
1% adverse slippage, 0.1% buy fee and 0.01 quanta, the order reserves 50.60. A full
fixture fill costs 50.50 gross plus 0.06 fee, leaving 849.44 available and zero
reserved cash with fifteen shares. This is tested synthetic mechanics, not real
trading performance or a recommended cost model. Consumers must retain the paper
receipt/checkpoint envelope or broker mode when handling generic Core snapshots
and fills; those generic types alone do not establish operational provenance.

## Idempotency, replay and recovery

Submission keys, fill event IDs, cancellation keys and frame IDs are explicit,
scoped by operation within this one account. Repeating an identical request
returns its original receipt, even after subsequent state changes, without a new
reservation, fee or fill. Conflicting reuse raises `idempotency_conflict`.
Order IDs remain unique for the broker lifetime, including rejected IDs. A retry
with new evidence needs a new order ID/key. Fill event IDs are global across the
account, not reusable for another order. A pending fill attempt with unchanged
ID stays the same observation; a later attempt requires a new event ID.

`checkpoint()` returns a frozen Python value containing initial sources, policy,
initial frame, complete commands and expected state ID. `restore(checkpoint)`
replays the public transition path, including mandatory risk evaluations, then
checks the exact state ID and command sequence. Missing/changed commands, proof,
policy or expected hash fail. Replay also reconstructs idempotency outcomes and
open holds. Checkpoints are not signed approvals or an untrusted-pickle format;
this PR supplies no persistence, pickle loading, HTTP archive or JSON decoder.

The configured command budget bounds work/history growth. At exhaustion, already
cached repeats remain readable and each still-open order may use a final cleanup
cancellation. Extra terminal no-op commands remain blocked, so cleanup cannot
create an unbounded history. The journal never exceeds twice the normal budget.

## KR/US scope, limitations and future integration

KRX alphanumeric symbols, KOSPI/KOSDAQ KRW and NYSE/NASDAQ USD identity validation
are inherited from Trading Core. Cash settles virtually and immediately in the
instrument's native currency; KRW cannot fund a USD buy. Direct FX and matching
base-currency equity/P&L evidence are still required by Risk when other held or
pending assets use foreign currencies. FX here values risk only; no FX trade,
cash conversion, real settlement funding or currency-specific tax approval occurs.

There is no fractional sharing, shorting, margin, corporate-action handling,
real calendar qualification, spread/order-book model, cost-basis or calculated
realized/unrealized P&L. Supplied equity/P&L and coherent valuation coverage remain
the caller's responsibility. Serialized command order defines determinism;
concurrent distinct requests do not claim a deterministic scheduling winner.

State hashing, invariant validation and checkpoint replay inspect bounded owner
history; they are not constant-time or bounded-memory streaming replay. No engine
speed/memory improvement is claimed. For future Mass Simulation, use isolated
broker instances with explicit frozen fixtures/seeds/event sequences, extend
bounded output/archive transport separately and measure resource use before
scaling. Keep existing research Ledger/Performance validation and approval
restrictions unchanged rather than replacing them with this synthetic book.
