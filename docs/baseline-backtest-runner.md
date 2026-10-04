# Locked baseline runner

This change implements an in-memory historical research simulator. Only synthetic
fixtures have been executed. There is no CLI dispatch, real dataset run, OOS
prediction, fitting, tuning, performance ranking or broker integration.

## Policy lock and the approved planning extension

The existing baseline strategy, exits, capital, sizing fraction, position limit,
participation cap, slippage and costs are unchanged. The user separately approved
one necessary addition: quantity and cash reservation must be fixed before the
next session's open is known.

`long_only_up_reversal_baseline_v2` now includes:

- Planning rule: `anchor_close_adverse_price_reservation_no_gap_topup_v1`.
- Planning reference: completed signal session T close only.
- Planning execution price: T close * 1.001, ceiling to whole KRW.
- Intended integer quantity: existing 5% equity/cash-constrained sizing helper,
  using that planning price and equity observable at T.
- Reservation: intended quantity * planning price plus 1.5 bps commission,
  ceiling to whole KRW.
- At T+1, test the **entire intended quantity** at the actual slipped/rounded open
  against this reservation. Reject a shortfall; do not top up or resize using price.
- Only after that check, apply the delayed volume cap. A small volume cap cannot
  rescue an unaffordable intended entry.

Updated policy fingerprint, approved costs and empty quality allowlist:

```text
b0c0a6aebc95f8952dc3db7f2262bd854cf4896b52ba6c5e92207a9e740d2b37
```

Cost fingerprint remains:

```text
c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef
```

The previous policy fingerprint beginning `5686cc5c` must not be supplied for v2.
The runner requires independently supplied expected policy and cost fingerprints,
compares both before simulation, and verifies the compiled backtest contract.
Pins are trusted local configuration, not authentication of arbitrary user input.
Do not compute a replacement expected pin from an unexpected policy during a run.

## Interfaces and reuse

- `BaselineSession`: immutable `ObservedSession`, optional `DailyBar`, quality
  snapshot, supplied settlement date and evidence reference. Missing bars stay
  missing. Dates, metadata and session chronology are validated before running.
- `BaselineSignalSource`: receives only the published prefix for one ticker and
  its cutoff. Synthetic fixtures may use an explicit test double.
- `HGBBaselineSignals`: production adapter using `TrustedHGBResolver`,
  `resolve_snapshot()` and `HGBResearchSignalService.research()`. A read-only
  prefix store isolates inference from the full tape and live DB. It verifies
  artifact integrity, preserves both conditional model outputs, and never refits.
  Up alone is eligible for this baseline. Real inputs reject custom signal doubles.
- `BaselineBacktestRunner`: accepts the frozen session tuple, compiled contract,
  source, dataset fingerprint, expected pins, explicit planning rule and an
  initialized `LedgerState`. The initial account must have exactly KRW 100 million,
  no positions, no reservations and no preexisting orders. Its tape hash must match
  the frozen input; dataset/model references must match its manifest.
- `baseline_book`: constructs existing `VirtualOrder`, `VirtualFill` and
  `LedgerCommand` records. All cash, reservation, inventory and realized-P&L changes
  go through public immutable ledger functions. There is no parallel account engine.
- `BaselineRunResult`: immutable raw evidence document and final ledger. Its
  fingerprint includes both. No CAGR, MDD, Sharpe or win-rate computation is added.

Call `.validate()` to check the request without simulation or scoring, then
`.run()` only after separate execution authorization. Constructing
`HGBBaselineSignals` verifies local files but does not predict. Callers supply a
verified initialized ledger/manifest using the existing APIs; the runner does not
invent settlement calendars, data-source paths, artifact pins or quality approvals.

## Deterministic session order

Records must be ordered by trading date then ticker. Each ticker's session date
and sequence strictly increase. The first version requires synchronous daily
open/close/publication times across tickers for each date; overlapping/delayed
publication groups are rejected, not silently reordered.

For each completed session S, at its explicit publication time:

1. Advance the existing `VirtualClock`.
2. Resolve previously reserved entries for S, ticker ascending. Run existing entry
   preflight, including immediate-next-session, quality and no-retry checks.
3. Check intended-cost reservation coverage. Apply the 1% full-day-volume cap only
   now. Confirm reduced quantity through the ledger; cancel any unfilled remainder.
4. Release unused reservations after **all** S entry fills have been processed.
5. Evaluate open positions, ticker ascending: quality/gap failures first, then
   stop, target, or fifth-session close. Stop wins a same-bar collision. Entry
   counts as holding session one. Confirm eligible exits via the ledger.
6. Record complete or explicitly incomplete equity using S's published closes.
   Missing/unusable marks block new allocation; there is no mark carry-forward.
7. Generate S signals from published prefixes. Collect eligible candidates,
   sort by descending raw Up score then ticker ascending, and reserve for each
   ticker's immediate next observed session. Reserve slots/cash sequentially;
   one ticker cannot pyramid, and pending entries consume position slots.

Exit proceeds or reservation releases known after S cannot rewrite pre-S
reservations. They can affect only new decisions made after S, for later sessions.
Current equity is fixed across each simultaneous allocation batch; available cash
and position slots decrease as reservations are made.

Daily volume is not known at open. Buy fills use the session open as their economic
reference but become known only when the daily bar is published. Barrier/time exits
use session close as a deterministic accounting-time bucket, **not** an assertion
that a barrier touch happened at that exact time. Fill-known timestamps remain
publication times. These are research simulation records, not reconstructed trades.

## Entry, lifecycle and failure handling

Intended quantities/reservations are frozen records. Invalid/no-trade/missing next
bars, zero participation quantity and cash shortfalls produce explicit unfilled
outcomes and release reservations without looking for T+2. Participation reduction
is one confirmed fill followed by cancellation of the order remainder; it is not
an intraday partial-fill or liquidity model.

Positions retain entry fill, candidate, target/stop, session count and quality
status. Gap-through barriers have no approved execution-price rule and therefore
become unsupported; prices are not clamped to a barrier. Missing/unusable exit
sessions permanently suspend automatic resolution of that episode. Missing a held
ticker on another observed market session is conservatively treated as unsupported.
No later bar silently repairs the interval. End positions are not forcibly sold.

A fatal contract/cost/ledger error raises without returning a successful partial
result. All state is local and immutable; the supplied initial ledger and inputs
remain unchanged. There are no persistent writes or external effects to roll back.
Expected market-quality failures instead appear as explicit unfilled/unsupported
records. Unresolved positions make the result incomplete.

The existing ledger's immediate **virtual** cash accounting is reused. It does not
model broker settlement cash availability. Supplied settlement dates are used for
tax-rate selection only; they must come from verified external settlement evidence,
not the research calendar or a guessed calendar-day offset.

## Costs and raw results

The [locked cost policy](baseline-backtest-policy.md) remains the source of truth:
10 bps adverse slippage, adverse whole-KRW prices, 1.5 bps commission on actual
executed notional ceiled to KRW, separate settlement-date transaction tax and rural
levy each floored to KRW. Tax components are stored separately in events and their
sum is passed to the existing fill contract. No cost is deducted twice.

Result evidence includes input/dataset/model SHA and fingerprint, signal policy
and source, baseline/compiled contract/cost fingerprints, planning rule, runner
version, event order, intended reservations, fills, reasons, closed episodes,
remaining positions, equity snapshots, cash, realized P&L and cost totals.
Checkpoint/replay uses the existing ledger contract. System time, random seeds,
network access and live DB reads are absent from runner execution.

## Pre-run blockers and limitations

Implementation verification does **not** authorize a historical run.

- The currently locked **empty quality allowlist** blocks research flags emitted by
  normal HGB signal integration, including revision-history and universe limitations.
  A real trading experiment needs a separately reviewed explicit research-quality
  policy and corresponding new trusted pin. Hard failures must never be allowlisted.
- A real run still needs its approved dates/universe, immutable market inputs,
  model pins, quality evidence and settlement-date evidence. These were not prepared
  or scored in this task.
- Tax coverage is bounded through settlement date 2026-10-01; later settlement
  dates fail closed even if the economic trade falls within the covered period.
- Daily-volume execution is retrospective, not proof of liquidity or PIT/OOS.
- Gap-through exits, delayed asynchronous publication and incomplete exit histories
  remain unsupported. No inferred corporate actions, price repair or fills exist.
- This is an in-memory implementation using existing immutable ledger checkpoints;
  full-universe runtime/memory scaling has not been benchmarked on real data.

Synthetic tests exercise chronology, all exit rules, reservations, no-pyramiding,
position limits, volume/cash isolation, costs, year-boundary taxes, malformed
contracts, source-prefix isolation and deterministic replay. No real baseline
performance has been produced.
