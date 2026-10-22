# Paper simulation portfolio valuation foundation

`backtest/paper_valuation.py` is an explicit, read-only analysis interface for
completed synthetic KR/US Paper Broker results. It values an account using
separately pinned opening/closing marks, derives inventory cost observations and
checks them against existing execution evidence. It does not change execution,
risk authorization, settlement, scheduling, public APIs, historical accounting or
`simulation_diagnostic_report_v1` / comparison v1.

## Existing owners and accounting differences

| Existing contract | Reuse or boundary |
| --- | --- |
| Trading Core `Instrument`, `Money`, `Position`, `AccountSnapshot`, `FillReceipt` | Full exchange-qualified identity, exact native amounts, authoritative shares/cash/fills |
| Risk `MarketQuote` | Explicit positive native price, observation/availability timestamps, source ID |
| Paper Broker and `PaperSimulationAdapter` | Authoritative reservations, settlement, fees/taxes, deterministic execution, command replay and conservation |
| `FrozenJSON`, canonical digest, existing exact Decimal context | Immutable versioned output, deterministic fingerprints, context-independent arithmetic |
| Historical research Ledger/Performance | Inspected but not reused as a paper settlement or cost-basis engine |

The historical Ledger owns KRW `cost_basis_krw`, its own reservation/transition
proofs and policy-quantized average-cost inventory reductions. Historical
Performance obtains marks through frozen tapes, release plans and virtual
clocks. Paper Broker supports native KRW or USD, has a different fill/checkpoint
contract, and **Position contains quantity, sellable and reserved shares but no
initial cost basis**. Passing paper results to historical Performance would
invent missing evidence or reinterpret its rounding rules.

This component therefore projects costs from verified paper fills, using the
explicit new `paper_average_cost_exact_v1` analysis policy. It does not create
another settlement ledger, execute trades or change historical Ledger formulas.
There are no source-file moves, new infrastructure dependencies or architecture
debt exceptions.

## Input trust and deterministic replay

`value_paper_simulation(experiment, job, result, trusted_result_fingerprint,
adapter, evidence, trusted_evidence_hash)` requires the original immutable
experiment, job and registered scenario adapter. `result` is the completed
`paper_simulation_result_v1` output, not a scheduler record or a user-created
performance summary.

1. Independently retain the approved paper result fingerprint and valuation
   evidence hash. A hash supplied alongside arbitrary replacement content is
   not an external approval. The caller owns this trust boundary.
2. Revalidate the immutable typed evidence, nested Money/MarketQuote owners and
   its exact canonical pin. Verify the existing paper result version/provenance
   and original result fingerprint.
3. Explicitly call existing `PaperSimulationAdapter.replay_validate` in a fresh
   broker under the existing cooperative step/deadline budget. This independently
   binds experiment, strategy, dataset, scenario, seed, run, policies, receipts,
   risk evaluation and all settlement results. Forged fees/cash with recomputed
   document hashes still fail this semantic check.
4. Bind the valuation evidence to the exact experiment/run/execution fingerprint.
   Opening time must equal the initial scenario frame; closing time must equal
   the final scenario frame. These are supplied virtual boundaries, never today's
   time or inferred execution timestamps.
5. Match marks by full symbol/exchange/currency identity, not ticker alone.
   Reconcile projected share counts, cash transitions and fee/tax totals to the
   authoritative replayed result.

`ValuationEvidence` has an evidence ID, the three identity hashes above, explicit
opening/closing timestamps, tuples of opening/closing `MarketQuote` owners, a
mandatory `max_age_seconds` (0–86,400 inclusive), and `cash_flow_scope`. Its
canonical document includes `synthetic_paper_valuation_evidence_v1` and the
accounting policy version. At most 16 marks per boundary are accepted, matching
the existing bounded scenario instrument contract. No default price, tolerance,
FX rate or permissive risk policy is created.

Marks must be independently trusted **synthetic observations**. Copying the last
fill, scenario quote or an opening mark as a closing mark is never performed by
the valuation component. Test examples explicitly supply their fixture quotes;
that is test evidence construction, not an automatic production fallback.
A quote may differ from the final execution price; it must have its own pinned
identity and valid time evidence.

## Valuation and exact formulas

All amounts are in the single account currency. Cash includes both available
and reserved money; reserved money remains an asset. Position quantity includes
shares reserved for sale; reservations are not extra shares. No liquidation,
fill, exit costs or released reservations are invented.

For each boundary:

```text
cash = available_cash + reserved_cash
position_market_value[i] = explicit_mark_price[i] * owned_quantity[i]
equity = cash + sum(position_market_value[i])
```

Every nonzero position needs a mark. A cash-only account does not. Missing one
position mark makes full equity unavailable while preserving cash, known
position values and per-position reason codes. Zero-quantity positions are
omitted from the open-position projection. Mark age is measured from its
observation time; both observation and availability must be at/before the
boundary. Age exactly equal to the maximum is accepted.

The source fills are processed in original execution order. For verified fill
quantity `q`, execution price `p`, fees `f`, taxes `t`, prior inventory quantity
`Q` and known aggregate cost basis `B`:

```text
purchase_gross = q * p
purchase_cash_outflow = purchase_gross + f + t
new_buy_basis = B + purchase_cash_outflow

sale_gross = q * p
sale_cash_proceeds = sale_gross - f - t
allocated_sale_basis = B * q / Q
remaining_basis = B - allocated_sale_basis
realized_pnl_for_sale = sale_cash_proceeds - allocated_sale_basis
unrealized_pnl = marked_remaining_inventory_value - remaining_basis

realized_return = sum(realized_pnl_for_sale) / sum(allocated_sale_basis)
unrealized_return = sum(unrealized_pnl) / sum(remaining_basis)
net_account_value_change = closing_equity - opening_equity
```

The projection requires:

```text
closing_cash - opening_cash = sale_gross_total - purchase_gross_total - fees - taxes
initial_quantity + purchased_quantity - sold_quantity = ending_quantity
```

Purchase costs are capitalized into basis. Sale costs reduce proceeds. The
broker already settled fees and taxes in cash, so they are not deducted again
from marked equity or total P&L. Source slippage is reported verbatim and is
already embedded in fill prices. It is not another cash expense. No hypothetical
terminal sell commission/tax is charged to unsold shares.

Decimal arithmetic uses the existing exact context. Average-cost allocation and
ratios use exact rational arithmetic over those Decimal inputs, avoiding a new
rounding assumption when division repeats. Every metric has `available`,
`amount`, `exact_ratio` (integer numerator/denominator strings), and `reason`.
An available repeating rational has `amount: null`, its exact ratio, and
`decimal_unavailable_reason: nonterminating_or_precision_bound`. Consumers must
check availability and exact ratio, not interpret every null decimal as missing
evidence. Finite decimal strings are provided when exactly representable in the
existing precision bound; no float or approximate division is used. Returns are
unit ratios, not percentages or annualized forecasts. Nonpositive denominators
produce an unavailable return, not a division by zero or fabricated zero return.

## Initial inventory and external flows

An initial nonzero position has **unknown acquisition cost basis**. Opening mark
price is not historical purchase price. Buying more of the same unknown inventory
does not cure this gap. Sales from it have unavailable allocated basis and
realized P&L; remaining mixed inventory has unavailable basis/unrealized P&L.
After completely exiting it, a new purchase establishes known basis for the new
inventory. Aggregate realized P&L remains unavailable if any earlier disposition
had unknown basis. Per-fill and per-position facts remain available independently.

With known opening/closing marks, period **valuation P&L** can be available even
when acquisition-basis realized/unrealized P&L is unknown. It measures change
over the supplied synthetic window, not lifetime gain on initial inventory. For
accounts opening without shares, known realized plus unrealized P&L reconciles
exactly to total period P&L when full valuation is available.

The only supported cash-flow scope is
`replayed_closed_paper_commands_v1`. It explicitly asserts this entire virtual
account/window belongs to the existing replayed command set: advance, submit,
fill and cancel. That version has no deposit, withdrawal, dividend, corporate
action or external transfer command. Exact replay plus cash conservation supplies
the closed-account proof of zero net external flow. A caller cannot add an
arbitrary cash-flow amount or use this scope for a real external account.

With that pinned scope and complete boundary equity:

```text
net_external_cash_flow = 0
total_pnl = closing_equity - opening_equity - net_external_cash_flow
total_return = total_pnl / opening_equity
```

If scope is `None`, flow evidence is deliberately incomplete: net value change
can still be displayed, but net external flow, total investment P&L and total
return remain unavailable with `external_cash_flows_unverified`. Realized and
unrealized return ratios (including per-position returns) are also withheld. Independently
derivable fill-based realized and inventory-based unrealized figures remain
separate. Unsupported deposit/withdrawal declarations are rejected. A changed
ending cash balance cannot manufacture a supported return through self-hashing.
Actual external flows, dividends and corporate actions require a future version
with independent evidence; no adjustment is synthesized here.

## Report, errors and integration

Output is canonical immutable `paper_simulation_portfolio_valuation_v1`, capped
at 1 MiB, with experiment/run/strategy/dataset/scenario references, native market
and currency, opening/closing observations, source fills/receipt hashes and
indices, exact costs, cash-flow scope, performance availability and limitations.
The original result/execution/checkpoint identities and complete separately
pinned mark evidence are retained. `content_fingerprint` excludes only itself.
The full JSON document digest is different; neither replaces original hashes.

`verify_valuation_report` requires a trusted report fingerprint **and** original
execution/price evidence with their trusted pins. It independently repeats source
verification, semantic replay and the complete valuation projection. Rehashing a
changed P&L, scope or mark inside the report cannot bypass this comparison.
Unknown versions, invalid/duplicate/incompatible marks, invalid source owners,
pin/binding mismatches and semantic replay failures raise without a valid result.
Missing, stale and future-dated marks instead give explicit unavailable metrics;
partial known values are not advertised as a full return.

KR/KRW and US/USD are independent. Cross-currency or wrong-exchange observations
are rejected; no FX consolidation or ambiguous symbol matching is supported.
There are no HTTP routes, new CLI commands, database writes or network access.
Analysis explicitly replays a completed run; it is not an implicit API read or
scheduler resume. Existing job-boundary checkpoint semantics and diagnostic v1
contracts remain unchanged. A valuation-aware diagnostic/comparison extension
belongs in a separate versioned follow-up.

## Worked synthetic example

The offline fixture `tests.backtest.test_paper_valuation.book` starts with 900
native cash and no shares. It explicitly buys five shares on a 10 quote and
sells five on a 12 quote with the existing synthetic fixture fee/tax/slippage
policy. The observed fill prices are 10.10 and 11.88:

| Observation | Exact amount |
| --- | ---: |
| Opening cash/equity | 900 |
| Buy gross (5 × 10.10) | 50.50 |
| Buy fee, buy tax | 0.06, 0 |
| Capitalized cost basis | 50.56 |
| Cash after purchase | 849.44 |
| Sale gross (5 × 11.88) | 59.40 |
| Sale fee, sale tax | 0.06, 0.12 |
| Net sale proceeds | 59.22 |
| Ending cash/equity, no remaining shares | 908.66 |
| Realized and total period P&L | 8.66 |
| Total return | 433 / 45000 |
| Recorded total fee, tax, embedded slippage | 0.12, 0.12, 1.1 |

Reproduce with existing controlled test fixtures (development checkout only):

```python
from tests.backtest.test_paper_valuation import book, inputs, project

report = project(inputs(book(
    (("BUY", 5, "10"), ("SELL", 5, "12")), costs=True,
))).to_dict()
assert report["closing"]["equity"]["amount"] == "908.66"
assert report["performance"]["total_pnl"]["amount"] == "8.66"
assert report["performance"]["total_return"]["exact_ratio"] == {
    "numerator": "433", "denominator": "45000",
}
```

`inputs` constructs known synthetic quotes and pins for tests. A real research
consumer must retain approvals/pins independently before invoking
`value_paper_simulation`. For a batch, obtain each successful output from the
existing scheduler and explicitly value it with its own run-bound evidence;
do not value failed/unexamined jobs or replay completed jobs as part of resume.
The API works from an installed wheel; fixtures themselves remain test-only.

These are controlled synthetic economic observations, not historical-market or
real-time forward-paper performance. They prove no profitability or strategy
quality. Future OOS evaluation needs independently approved data, cost/flow
boundaries, robust valuation policies, Corporate Actions/PIT evidence, leakage
controls and independent validation. Optimization or strategy promotion is not
implemented or authorized by this foundation.
