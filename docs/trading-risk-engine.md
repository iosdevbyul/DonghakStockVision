# Paper pre-trade Risk Engine foundation

`donghak_stock_vision.trading.risk.evaluate_risk(order, context, policy)` is a pure,
deterministic preflight over caller-supplied immutable evidence. It performs no
I/O, clock reads, model inference, order submission, reservations or account
mutation. It adds no CLI or HTTP routes and does not activate paper/live trading.
See [Trading core architecture](trading-core-architecture.md) for the domain ports.

## Boundaries and reuse

The evaluator reuses `OrderIntent`, `Instrument`, `Money`, `Quantity`,
`AccountSnapshot`, positions, balances, `OrderSide` and `RiskDecision` from Trading
Core. Existing decimal/time validators and canonical SHA-256 utilities are reused.
Its imports load no FastAPI, SQLite adapters, HTTP clients, broker implementation
or models. Offline tests verify this in a fresh interpreter.

The existing Strategy/Decision Engine, research admission, Ledger, virtual fill,
Position History, Performance and portfolio contracts are unchanged. Projected
risk notionals are temporary what-if checks, not another economic ledger or a
replacement for validated historical P&L. The new evaluator is not automatically
inserted into any existing execution path.

## Explicit inputs

All `RiskPolicy` fields are required. There is no default configuration and no
implicit enabled state. `None` policy/context/order inputs return a denied
assessment. Constructors reject invalid domain structures; the evaluator also
revalidates nested supplied owners and rejects malformed/noncanonical objects
rather than trusting a prior validation or silently correcting them.

`RiskPolicy` (`paper_pre_trade_policy_v1`) contains:

| Field | Meaning |
| --- | --- |
| `policy_id` | Caller-selected policy reference; not an operational approval |
| `account_currency` | KRW or USD valuation/limit/cash denomination |
| `allowed_markets` | Explicit nonempty KR/US allowlist |
| `max_order_notional` | Positive maximum order value in account currency |
| `max_position_fraction` | Positive fraction, at most one, of supplied equity |
| `max_exposure_fraction` | Positive fraction, at most one, of supplied equity |
| `max_daily_loss` | Positive absolute daily net-loss threshold in account currency |
| `max_price_age_seconds` | Inclusive maximum reference-price observation age |
| `max_fx_age_seconds` | Inclusive maximum direct-FX observation age |
| `max_account_age_seconds` | Inclusive maximum account observation age |
| `max_valuation_age_seconds` | Inclusive maximum equity/P&L observation age |
| `risk_timezone` | Explicit IANA timezone defining the account's calendar risk day |

Zero-age policies require observations exactly at the supplied evaluation cutoff.
Boolean ages, negative limits, float ratios, missing markets and invalid zones
are rejected. Fractions cannot authorize leverage. Example policy values in
synthetic tests are mechanics only, not suggested investment parameters.

`RiskContext` requires an account snapshot and its declared account currency,
`account_complete`, a valuation or explicit `None`, quote/FX/pending-order tuples,
a buy-cost buffer or explicit `None`, `trading_enabled`, and `evaluated_at`.
`account_complete=True` is the caller's assertion that the account and pending
order inventory are complete and coherent. Unknown/false completeness denies.
An explicitly empty pending tuple means no pending orders; omission is invalid.

`PortfolioValuation` supplies authoritative account-bound equity and daily net
P&L, their currency, risk date, observation/availability times and source ID.
Equity must be positive. The risk date must match both the observation date and
the evaluation date in `risk_timezone`. This is an account calendar risk day, not
a claim of verified exchange sessions. No exchange holiday calendar is inferred.
The engine never reconstructs P&L, performance or equity from fills.

Quotes carry the exact exchange-qualified instrument, positive same-currency
price, observation/availability times and source ID. Source IDs and completeness
assertions must be authenticated by the future application boundary; this pure
module cannot verify a broker or publication registry.

## Checks and arithmetic

All inputs must be known by `evaluated_at`; observation cannot follow availability.
An aware future intent, account, quote, valuation, pending order or required FX
rate denies. Quote/FX/valuation/account ages are measured from observation, not
from a later receipt that could make old information appear fresh. The inclusive
age boundary is checked exactly through integer seconds and microseconds.

The engine uses bounded exact Decimal arithmetic in an explicit 128-digit
context, with rounding/inexact/invalid arithmetic trapped. It does not use floats,
round tolerances, division, FX inversion or new fee/tax schedules. Source numeric
bounds remain the existing 24-digit and exponent-12 bounds; whole shares retain
the signed-64-bit positive order bound. Derived products may exceed source Money
bounds and remain exact Decimal risk metrics. Arithmetic failure denies.

- Candidate notional uses `quantity × max(reference price, limit price if any)`
  and a direct FX rate if needed. This bounds a limit buy conservatively and
  does not claim that a market order will execute at this price.
- A buy requires `notional + buy_cost_buffer <= available base-currency cash`.
  Reserved cash is not added back. The buffer is explicitly supplied, nonnegative
  and in account currency, including the caller's applicable cost/adverse-price
  allowance. Unknown costs deny a buy; explicit zero is possible only if supplied.
- Every nonzero held position requires a fresh reference mark and any required
  FX. Every pending buy contributes its full conservative notional. Pending sells
  do not free exposure or generate cash in advance. Partially filled pending
  orders must be supplied with their relevant remaining intent by the owner.
- A proposed buy adds its notional. A proposed sell requires sufficient existing
  sellable quantity and subtracts only its reference-marked quantity from the
  target's what-if exposure. It never credits hypothetical sale proceeds.
- Every projected position, including other holdings, must satisfy
  `position_value <= supplied_equity × max_position_fraction`.
- Total projected gross equity exposure must satisfy
  `sum(position_values) <= supplied_equity × max_exposure_fraction`.
- Daily P&L at or below `-max_daily_loss` denies. Different-currency P&L is
  rejected, never numerically compared to the account-currency threshold.

Concentration/exposure use supplied current equity as the denominator and risk
notionals as numerators; they do not forecast post-fee NAV or actual fills. The
caller is responsible for coherent marks, equity, pending orders, reservations
and cost bounds. Missing coverage never becomes zero exposure. All policy checks
apply to buys and sells where meaningful: disabled state or daily loss also
blocks sells, and a sale that leaves an existing limit breach is denied. No
emergency-exit exemption or new trading strategy is introduced.

## KR/US and FX

Trading Core validates KOSPI/KOSDAQ against KRW and NYSE/NASDAQ against USD,
including alphanumeric KRX codes. Exchange-qualified identity must match exactly;
a quote from another exchange is not substituted based on symbol text.

For foreign instruments the evaluator requires a positive direct rate expressed
as account-currency units per one instrument-currency unit. A USD instrument in
a KRW account therefore needs USD → KRW, not an inferred inverse KRW → USD.
Same-currency evaluation needs no FX. Duplicate quote or FX-pair definitions are
rejected; neither stale rates nor cross-rate triangulation are accepted.

FX conversion here values risk and purchasing capacity only. It does not convert
cash, establish native settlement funds, model spread, or grant FX permission.
The future paper adapter must independently ensure settlement funding and costs.
Cash-equity long-only whole shares are supported; shorting, margin and fractional
shares remain outside this contract.

## Results and rejection codes

`RiskAssessment` (`paper_pre_trade_risk_v1`) exposes `allowed`, the bound order,
ordered `RiskRejection(code, field)` entries, evidence ID, account currency and
nullable notional/projected-position/projected-exposure metrics. `to_dict()` uses
stable string decimals and enum values, with no rendering clock. Independent
checks accumulate in fixed evaluation order; malformed structures stop dependent
arithmetic. Metrics with incomplete mark coverage remain null, not invented zero.

| Codes | Meaning |
| --- | --- |
| `invalid_order`, `invalid_or_nonpositive_quantity` | Malformed intent/identity/instrument or invalid size |
| `missing_policy`, `invalid_policy`, `invalid_context` | Essential configuration or coherent account evidence unavailable |
| `trading_disabled_or_unknown` | Only explicitly supplied true permits evaluation to allow |
| `instrument_market_not_allowed`, `currency_mismatch`, `account_mismatch` | Identity/denomination/owner mismatch |
| `reference_price_missing`, `reference_price_invalid`, `market_data_stale` | Unusable exact-instrument reference price |
| `fx_missing`, `fx_invalid`, `fx_stale` | Required direct conversion is unavailable or unusable |
| `future_evidence`, `account_stale` | Information unavailable at cutoff or old account state |
| `valuation_missing`, `valuation_invalid`, `valuation_stale` | Unknown equity/P&L or wrong risk date |
| `cash_balance_missing`, `buy_cost_buffer_missing`, `buy_cost_buffer_invalid` | Funding/cost evidence insufficient |
| `maximum_order_notional`, `insufficient_available_cash` | Order/funding limit exceeded |
| `maximum_position_concentration`, `maximum_portfolio_exposure` | What-if account risk limit exceeded |
| `daily_loss_limit`, `insufficient_sellable_position` | Daily-loss or long-only quantity restriction |
| `numeric_bounds_exceeded` | Exact bounded arithmetic could not complete |

The SHA-256 evidence ID binds version, policy, order, complete supplied context
and rejection sequence through existing canonical JSON hashing. Input tuple order
is retained as evidence; set-valued market allowlists are sorted. This fingerprint
supports deterministic evaluation tracking, not authenticity, a signature, an
operational approval or historical-result hash replacement. Structurally invalid
inputs receive no fingerprint.

`assessment.to_decision()` reuses the existing `RiskDecision` and binds the full
original intent. Detailed codes remain on the assessment; the existing port gets
`risk_limit_exceeded` for economic/quantity limits or `eligibility_denied` for
other failed preconditions. It always uses PAPER mode. An invalid order yields
no submit-capable decision. Reusing an allowed decision for an altered order is
rejected by the existing full-intent binding.

## Synthetic example and integration

The offline fixture in `tests/trading/test_risk.py` supplies five requested shares,
a synthetic KRW 10 mark, ten existing shares, KRW 900 available cash, KRW 1 cost
buffer, KRW 1,000 supplied equity and zero daily P&L. Its explicit policy permits
KR/US, KRW 100 order notional, 50% per-position / 80% gross exposure, KRW 50 daily
loss and 60-second evidence ages. It observes at `2026-01-05T14:30:00+00:00`.
The tested assessment allows, with KRW 50 order notional and KRW 150 projected
position/gross exposure. This is synthetic mechanics, not investment performance.

```python
from donghak_stock_vision.trading.risk import evaluate_risk

# Build order/context/policy explicitly from independently verified paper inputs.
assessment = evaluate_risk(order, context, policy)
if not assessment.allowed:
    diagnostics = assessment.to_dict()["rejections"]
else:
    paper_decision = assessment.to_decision()
    # No broker is called here. An application must recheck the current owner,
    # source evidence, policy, settlement funding and freshness before acceptance.
```

Future Mass Simulation can invoke this pure function with isolated frozen inputs
without changing existing backtest hashes or economic ledgers. A future Paper
Broker must retain durable idempotency, atomic reservations, revision checks,
reconciliation, execution pricing and fee/settlement validation. Assessment is a
point-in-time preflight, not a lock against concurrent account changes.

No live submission port, provider requests, training/inference, brokerage
credentials, background execution or operational qualification is introduced.
Corporate Actions, Runner Tail, Run Input Freeze, strict PIT, OOS, publication and
all existing approval restrictions remain unchanged. Full offline regression and
Python 3.13 validation remain in the existing macOS X64 self-hosted CI; local
validation does not claim CI success.
