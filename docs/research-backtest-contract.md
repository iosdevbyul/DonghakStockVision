# Research backtest contract v1

This change defines contracts and pure preflight validation, not a strategy or a
backtest engine. It does not evaluate OOS data, optimize thresholds, fit models,
generate strategy orders, calculate fills, or simulate a portfolio.

The existing Phase 4 runner, execution code, ledger and inference/signal paths
remain unchanged. `Fill` reuses the existing immutable `VirtualFill` contract;
there is no competing fill representation. The new `BacktestContract` is a separate
policy declaration for the research-signal integration. It is **not** automatically
accepted by the old runner. A reviewed adapter is required before execution.

## Layer boundaries

```text
ResearchSignal
    -> StrategyPolicy (not implemented)
    -> TradeIntent (explicit intent only)
    -> ExecutionPolicy / simulator (not implemented here)
    -> existing VirtualFill
```

`OrderSide` is BUY/SELL. `PositionSide` includes vocabulary for flat/long/short,
but this first contract accepts only long cash-equity intents. Up/down scores do
not select a side or generate an intent. A TradeIntent must explicitly reference
a signal, strategy policy, contract and chronology. It has no automatic quantity,
price, routing or submission behavior. No HGB score, including 0.5, is a trade rule.

## Chronology and information boundaries

`ObservedSession` contains ticker, trading date, sequence, open/close timestamps
and complete-bar availability, **without OHLCV prices**. Times are aware and
normalized to UTC; trading dates use Asia/Seoul. Session date/sequence and market
time ordering must be strictly increasing per ticker. Open and close times are
explicit input, never a default hard-coded exchange schedule.

- `signal_anchor`: session T whose completed daily bar supplies the feature input.
- `feature_cutoff`: latest information boundary used by the feature calculation.
- `signal_time`: when the research signal is available.
- `decision_time`: when a future StrategyPolicy authors its intent.
- `eligible_execution_session`: the immediate next observed session for that ticker
  in the frozen research calendar, T+1 in **session order**, not calendar-day order.

The validator requires:

```text
T close <= T complete-bar availability <= feature_cutoff <= signal_time <= decision_time
and, for an eligible entry:
decision_time < next observed session open
```

The current historical daily publication assumption is preserved: the full T bar
is available no earlier than the next Asia/Seoul midnight. A signal built from T
high/low/close cannot be executed at T open or T close. Decisions delayed beyond
the immediate next open are rejected; they are not silently moved to another day.

`SignalChronology.from_signal` checks the existing signal's ticker, data cutoff
and quality result. It does not reinterpret its scores. The upstream snapshot
resolver remains responsible for causal feature provenance. Metadata alone does
not prove that an arbitrary caller computed its features without future data.

Future **prices** belong only to the execution-side boundary. EntryWindow receives
price-free calendar metadata; `assess_entry` receives next-session bars only after
their declared publication. No method in this change reads a database or searches
future prices. This is an API separation, not a Python security sandbox.

A future simulator may use next-session open as an economic reference while the
complete daily bar and confirmed fill become known later. It must preserve both
fill_at and fill_known_at in VirtualFill, and must not expose the full next bar or
credit an account before the applicable information boundary.

## Single entry attempt

The explicit execution/entry configuration requires:

- `next_observed_session_open`
- `complete_daily_bar_next_seoul_midnight`
- `simulator_only` future access
- `open` reference price
- `immediate_next_observed_only`
- `unfilled_no_retry`

The frozen calendar must retain observed sessions with unusable bars; filtering
those sessions to hunt for a later tradable bar is forbidden. Approved research
calendar exclusions remain distinct from holidays. This contract cannot detect a
session deliberately omitted from an untrusted external calendar; the future
adapter must bind it to verified frozen input provenance.

Friday T may therefore have Monday as T+1. Missing next session, missing/invalid
bar or open, no-trade bar, quality failure or insufficient liquidity minimums
produce unfilled. Same/prior-session attempts and T+2 retries are rejected.
End-of-period orders cannot use a session beyond the declared end.

`EntryAssessment` distinguishes eligible_not_filled, unfilled and rejected with
machine-readable reasons. Passing preflight does not make a Fill, prove liquidity,
calculate quantity or authorize any real order. The validator does not modify
cash, holdings or reservations.

## Required immutable policy configuration

`BacktestContract.from_dict` rejects missing and unknown fields. It uses the
existing canonical JSON and Decimal validation primitives. Decimal values or
exact decimal strings are accepted; float money, NaN, infinity and negative rates
are rejected. Equivalent decimal representations have identical fingerprints.
All nested configuration is frozen; returned dictionaries are detached copies.

| Component | Required choices |
|---|---|
| Contract | contract version, historical_research scope, inclusive start/end |
| Execution | version, timing, availability, future access boundary, existing fill type |
| Entry | version, open reference, one-attempt policy, invalid-session outcome |
| Exit | version, maximum holding sessions, explicit counting basis, target/stop rules, time-exit rule reference, reference price, collision policy |
| Costs | version, executed-notional basis, complete date schedule, rounding |
| Slippage | version, adverse proportional method, explicit rate, adverse rounding |
| Sizing | version, initial KRW capital, sizing policy reference, maximum concurrent positions, cash availability, leverage, short/fractional permission |
| Liquidity | version, minimum volume/value, maximum participation rate, rejection policy, lack-of-fill-evidence acknowledgement |
| Quality | version, explicit allowed research flags, mandatory existing segment validation, no corporate-action price corrections |

References contain artifact_id, version and content_hash. They are required
identities of future externally resolved policies, not implementations or approval
of the referenced behavior. A structurally valid contract is not executable until
those policies are resolved and approved. There are no production policy fixtures
or recommended numeric values in this change.

## Exit and intraday ambiguity

Target and stop rules must be explicitly disabled or reference an external rule.
Time exit requires a versioned rule reference. Maximum holding sessions must be
positive and supplied explicitly; its count basis must explicitly choose either
entry_session_is_one or sessions_after_entry. No rule is evaluated here.

The label horizon is five sessions. It supplies **no default holding period**.
Exit reference price must be explicitly open, close, or an external rule price.
Daily OHLC cannot reveal which of target/stop happened first. Collision policy is
mandatory: pessimistic or reject_as_ambiguous. There is no optimistic option or
implicit precedence. A future implementation must apply the selected conservative
rule, not infer an intraday price path.

## Costs, capital and liquidity

A cost schedule contains inclusive, non-overlapping date intervals and must cover
the full requested period without gaps. Each interval explicitly supplies buy and
sell brokerage fee rates, sell tax and sell market levy rates, and a minimum-fee
policy. The levy is sell-side only. Minimum fees are explicitly disabled or a
positive KRW brokerage minimum per fill, distinct from tax/levy. No charges are
calculated by this contract. Dates/rates are caller inputs, not assertions about
historical KOSPI tax law. Rounding quantum and mode are explicit.

Slippage is a separate explicit adverse proportional rate and price rounding
policy. An explicit zero is allowed; an absent value is not. The future engine
must apply the existing adverse BUY/SELL meaning rather than favorable rounding.

Initial capital, sizing policy and concurrent-position cap have no defaults.
Available cash explicitly excludes reservations. V1 requires leverage=1,
short_permission=false and fractional_shares=false. These values must be supplied;
unsupported borrowing, shorting and fractional shares are rejected. Long-only is
a capability restriction, not a selection of a strategy or an all-in allocation.

Positive volume and value are necessary, never sufficient, evidence for a fill.
Preflight checks the supplied minimum volume/value. Maximum participation rate is
a required bound for a future quantity-aware execution validator; this change has
no sizing or participation calculation and makes no liquidity guarantee.

## Data quality

The current price basis is KOSPI/unadjusted, with matching provider/market/basis
across the signal and candidate bars. Existing DailyBar and check_segment rules
are reused. No-trade, suspected discontinuity/gap, excluded corporate-action
segments and incomplete dates cannot be waived by an allowlist. Validated quality
metadata is required where verified sessions are asserted. Research limitations
may be explicitly acknowledged, but prices are never repaired, inferred, adjusted
or forward/back-filled here. This does not establish PIT or survivorship evidence.

## Identity and future results

The contract fingerprint includes contract version/period and every execution,
entry, exit/ambiguity, cost, slippage, sizing, liquidity, permission and quality
choice. Cost intervals and flag sets are canonically ordered. Component hashes
are available for traceability. No current timestamp or randomness enters identity.

`ResultProvenance` defines future references to dataset, both directional models,
signal policy, backtest contract, execution policy, cost policy and result. It
checks hashes and can verify contract/component links. It does not produce a
result, calculate performance, or replace the existing RunManifest/source-origin
contracts. Source documents must be retained separately for long-term replay.

## Next approval step

READY FOR BASELINE BACKTEST POLICY DESIGN means the structural contracts are ready
to discuss and approve policies. Still undecided: strategy conditions, target/stop
rules, holding duration/count basis, time exits, collision choice, initial capital,
sizing rule and cap, historical cost schedule, slippage/rounding values, liquidity
limits and allowed research-quality flags. Nothing is selected from OOS results.
A runner adapter, actual execution implementation for these policy declarations,
policy artifact resolution and persistence remain outside this change.
