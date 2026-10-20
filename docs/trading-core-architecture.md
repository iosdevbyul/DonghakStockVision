# Trading core foundation

This additive foundation describes KR/US cash-equity domain boundaries. It does
not implement automated trading, a broker adapter, a risk engine, a simulation
engine, or operational approval. Existing CLI, REST, historical results and
research-only contracts retain their existing semantics.

## Modules and dependency direction

- `trading/domain.py`: frozen value objects and structural invariants. Instrument,
  currency, whole-share quantity, order intent, observed lifecycle, supplied fill
  facts and account snapshots have no I/O.
- `trading/broker.py`: a read-only `BrokerReader` protocol, a paper-only
  `PaperBroker` protocol and explicit `RiskDecision` denial reasons. No concrete
  broker implementation or live submission method exists.
- Future application services may consume these ports. HTTP, storage and broker
  adapters must depend on domain contracts; domain code must never depend on
  FastAPI, database adapters, HTTP clients, credentials or a specific broker.

The project dependencies of these contracts are the existing pure
`backtest.validation` primitives (`money`, `integer`, `utc`, `text`, `require`)
and `data.schema.validate_ticker`, plus `backtest.research_contract.OrderSide`
(re-exported with its original name, preserving enum identity rather than
duplicating BUY/SELL).
The latter transitively imports immutable research types and the storage/provider
protocols, but no database adapter, model loader, HTTP client or FastAPI. Reusing
them preserves existing numeric bounds, timezone
requirements, KRX alphanumeric codes and `ContractError` behavior without moving
or changing established modules. A later extraction of shared validation can be
considered separately if needed; this PR does not reorganize the repository.

## Existing implementations retained

`strategy.contracts` / `strategy.engine` remain the decision input, policy and
research decision authority. `models.inference` remains score-only research
inference. `storage.base.MarketDataStore` remains a separate persistence port.
The existing `entry_contract.TradeIntent` remains the authored research strategy
intent with chronology and contract hashes; the new `OrderIntent` instead
describes an account-specific requested order. Neither is automatically converted
into a trading instruction or eligibility grant.

`backtest.contracts.VirtualOrder`, `VirtualFill`, `SimulationAccount` and their
canonical hashes remain authoritative for historical research. They deliberately
require KRW, virtual execution and research provenance. They are not generalized
in place to accept US instruments or live brokers. `backtest.ledger` remains the
only existing economic ledger; `execution`, `account`, Position History and
Performance retain all reservation, pricing, fee, tax and P&L logic. The new
objects do not calculate balances, reservations, settlement, cost basis or P&L.
There is no implicit conversion of a legacy result to an executable order.

## Types and invariants

`Instrument` is an exchange-qualified symbol with a derived KR/US market and an
explicit currency checked against KOSPI/KOSDAQ (KRW) or NYSE/NASDAQ (USD).
KRX short codes reuse the existing validator, preserving `00088K`. US symbols
support uppercase letters/digits and explicit dot/hyphen separators (e.g.
`BRK.B`). This is syntax validation, not a security master, proof of listing,
historical identity, exchange membership, or entitlement to trade. No exchange
or currency is inferred from a bare symbol.

`Money` requires a finite `Decimal`, retains the existing 24-digit / exponent-12
bounds and canonicalizes without ambient-context rounding. Floats are rejected.
Signed values are permitted for supplied domain amounts; cash balances, prices,
fees and taxes impose their own nonnegative/positive invariants. No FX or
rounding policy is introduced. `Quantity` is an integer from zero through
2**63-1, rejecting booleans and fractions. Orders and fills require positive
quantities. Fractional shares, short positions, margin and multicurrency netting
are outside v1.

`OrderIntent` identifies an account, instrument, source decision/evidence and
caller-provided order ID, side, market/limit type, requested quantity and aware
creation time. Limit orders require a positive same-currency price; market orders
cannot supply one. `validate_order_batch` rejects duplicate IDs even for identical
intents. Cross-batch durable idempotency remains the owning adapter's duty.

`validate_transition` checks explicit status observations. It never submits an
order or infers acceptance/fills. Partial-to-partial observations are allowed;
fill evidence is validated separately. Filled/cancelled/rejected/expired are
terminal. Unknown states are quarantined with no transition out: reconciliation
requires a future evidence-backed adapter contract. This is not a replacement
for the research ledger's validated event history.

`FillReceipt` binds the entire immutable intent, source and fill IDs, supplied
quantity/price/fees/taxes, execution time and information availability time.
Execution cannot precede intent creation, and availability cannot precede
execution. `validate_fills` checks duplicate fill IDs, exact order binding and
aggregate overfill. These checks do not establish source authenticity or replace
existing receipt replay and economic validators. Callers must supply the complete
relevant fill set; no persistent fill registry exists here.

`AccountSnapshot` distinguishes observation from availability, requires source
identity, unique currency balances and unique exchange-qualified positions.
Available and reserved cash are nonnegative and same-currency per balance.
Sellable plus reserved shares cannot exceed owned shares. Unsettled/unavailable
shares may account for the remainder. Empty snapshots do not prove completeness
or eligibility; no valuation or synthetic acquisition time is inferred.

## Paper/live separation and risk

`BrokerReader` exposes only account and order-status reads. `PaperBroker` narrows
its mode to PAPER and exposes only `submit_paper(order, risk)`. Protocols specify
interfaces, not runtime enforcement against a malicious adapter. No live write
port, implementation, broker credentials or network calls are provided.

`RiskDecision` defaults to denied with `eligibility_unknown`. Denials explicitly
support eligibility denied/unknown, missing evidence, risk limits and live
execution disabled. Allowed decisions require evidence identity, an empty denial
list, the complete immutable order intent and PAPER mode. Every LIVE decision
must remain denied with `live_execution_disabled`. `validate_for` checks the full
intent (including quantity, price, account and source), order identity and
paper-only permission. Evidence strings are references, not independently verified
approvals; a future application must validate their owner, freshness and policy
before calling a paper adapter. Adapters must also enforce durable
idempotency and cannot treat a structural protocol check as authorization.

These objects grant no P1/P10, strict PIT, Corporate Actions, Runner Tail, Run
Input Freeze, operational eligibility or existing publication/qualification
approval. They do not alter any existing governance boundary. Real/live execution
requires a separate reviewed design and explicit authorization.

## Future integration

Mass Simulation should continue using the established immutable research input,
Ledger, Position History, Performance and compact archive contracts. Any future
mapping into these generic descriptions must carry original source IDs and
explicit exchange evidence and preserve all research provenance; it must not
replace historical hashes or bypass validation.

A future independent Risk Engine may consume an intent, authoritative account
snapshot, market eligibility evidence and explicit policy to produce an
order-bound paper assessment. Unknown or stale evidence must deny eligibility.
Sizing, portfolio limits and strategy selection are not implemented here.
Forward paper execution and a later live port must remain independently gated.

## Validation scope

Offline unit tests cover domain invariants and a structural fake paper broker.
Focused existing contract, validation, ledger, execution and strategy tests cover
backward compatibility. No real broker or provider is contacted. No test claims
that a protocol, symbol or supplied source ID is an operational approval, or that
synthetic results are real investment performance. Full regression remains in CI;
CI settings are unchanged.
