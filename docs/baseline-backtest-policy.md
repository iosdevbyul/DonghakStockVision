# Approved long-only Up reversal baseline

This is a versioned policy implementation, not a backtest result or a new model.
No historical run, OOS scoring, selection, tuning, fitting or portfolio simulation
was performed to choose these rules. The raw signal domain and linear inference
path are unchanged. This baseline is an explicit consumer of the signal domain;
it does not turn every model score into an order.

## Scope and public boundary

`backtest.baseline_policy.BaselineBacktestPolicy` freezes the approved rules.
`BaselineCostPolicy` supplies the separately approved research cost assumptions.
Both are immutable; `to_dict()` returns detached JSON-compatible data and
`fingerprint` hashes the complete canonical declaration. No constructor offers
threshold, holding-period, barrier or portfolio parameter alternatives.

```python
from datetime import date
from decimal import Decimal
from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy

policy = BaselineBacktestPolicy(
    costs=BaselineCostPolicy(Decimal("1"), "down_per_component_KRW"),
    allowed_research_flags=(),  # Strict: explicitly approve any research limitations separately.
)
contract = policy.contract(date(2025, 1, 1), date(2025, 12, 31))
```

The dates above illustrate contract construction only, not selection of a new
evaluation window. An actual future run must supply its approved period, trusted
model pins, frozen inputs, quality policy and settlement-date evidence. Omitting
costs (`costs=None`) preserves a deterministic blocked declaration; contract
compilation, candidate creation and sizing then fail closed with
`BLOCKED_ON_COST_POLICY`. No zero-cost replacement exists.

`assess_candidate()` consumes an already generated `SignalCandidate`, its trusted
Up `HGBArtifact`, the exact eight feature values bound to the signal's feature
hash, an `EntryWindow`, and a caller-verified immutable account-context reference.
It never loads an estimator, predicts, reads a database, or creates a fill.
Artifact authentication and snapshot/segment checks remain responsibilities of
the existing trusted resolver and signal integration service. The new boundary
checks model direction/fingerprint, feature fingerprint, chronology and account
availability again. Passing a fabricated hash is not authentication.

The output contains either a reason and no candidate, or an existing `TradeIntent`
plus the original Up score, anchor-derived barrier and account source hash.
Candidate eligibility is not execution approval: the existing entry quality
preflight must still succeed. A missing signal remains unavailable.

## Locked strategy

| Component | Approved value |
| --- | --- |
| Executable model | Up HGB-7 only, conditional prior-decline population |
| Down HGB-15 | Retained unchanged; never an executable direction in this baseline |
| Eligibility | Raw Up score >= 0.5, scored, quality allowed |
| Threshold provenance | Reuse of the preapproved classification threshold; not OOS-tuned or claimed optimal |
| Direction | Long-only cash equity; no short, leverage or fractional shares |
| Duplicates | One position per ticker; ignore held-ticker signals; reject pending duplicate entries |
| Barrier width | max(0.02, 2 * anchor T volatility_10) |
| Target / stop | Actual simulated entry execution price * (1 +/- barrier width) |
| Holding limit | Five observed sessions; entry session is session one |
| Time exit | Session five close if neither barrier has triggered |
| Same-bar target and stop | Pessimistic: stop first |
| Initial capital | KRW 100,000,000 |
| Target notional | 5% of current causally valued equity at entry |
| Position limit | 20, including pending entry slots |
| Candidate priority | Higher raw Up score, then ticker ascending |
| Participation | At most 1% of entry-session volume; integer downward reduction |
| Trading-value floor | Explicitly disabled; zero trading value/no-trade still rejected |
| Slippage | 10 bps adverse on each executed side |

The five-session holding rule was explicitly approved for this baseline. It was
not automatically copied from the label horizon. `barrier_width()` consumes
anchor volatility only; `exit_levels()` produces exact reference levels, not
orders or simulated exits. Extreme volatility producing a nonpositive stop is
rejected rather than clamped. Target/stop gap-through execution and unavailable
exit-bar handling are not implemented here; a runner must fail closed rather
than invent optimistic barrier fills or prices.

## Time and liquidity

The [merged research contract](research-backtest-contract.md) is reused:
T complete bar -> after-T feature/signal -> immediate next observed session open.
Friday-to-Monday is a valid transition. No same-session fill, retries into T+2,
forward/backfill or quality bypass is allowed. The complete daily bar remains
available only after the following Seoul midnight. UTC compares actual times.

`desired_quantity()` uses known current equity, available cash after reservations,
and the slipped/rounded entry price. It accepts **no volume parameter**. It
floors the 5% target notional to integer shares, then finds the largest affordable
quantity including rounded entry commission. A result of zero means rejection.
The future runner must use only causal marks for equity and reserve cash/slots
sequentially in the declared priority; the pure function does not manage cash.

`participation_quantity()` is a separate execution-side cap. It rejects access
before `bar_available_at`, floors daily volume / 100, and reduces desired size.
Zero-volume and quantities below one reject; it never searches later sessions.
**Full-session volume is unknowable at that session's open.** This cap is a
retrospective research fill assumption, applied only after the bar becomes public;
it is not open-time decision information or evidence of actual liquidity.
A future runner must distinguish economic reference time from fill-known time
and must not reuse unresolved cash or positions before confirmation. No intraday
volume, partial-fill simulation, volume-based signal selection or advance volume
access is introduced. Same-session candidates cannot be reordered using this cap.

`order_candidates()` rejects duplicate tickers, mixed decision times, anchor
sessions, account views, strategy policies or backtest contracts. It returns a
stable order, without allocating positions or modifying an account.

## Date-aware taxes and approved cost assumptions

Ordinary taxable KOSPI share sales are in scope; exemptions, other securities,
other markets and investor-specific tax status are not inferred.

| Settlement/transfer date, inclusive | Securities transaction tax | Rural special levy |
| --- | ---: | ---: |
| 2022-01-01 to 2022-12-31 | 0.08% | 0.15% |
| 2023-01-01 to 2023-12-31 | 0.05% | 0.15% |
| 2024-01-01 to 2024-12-31 | 0.03% | 0.15% |
| 2025-01-01 to 2025-12-31 | 0% | 0.15% |
| 2026-01-01 to 2026-10-01 | 0.05% | 0.15% |

Sources reviewed on 2026-10-04:

- [Official Securities Transaction Tax Enforcement Decree amendment history](https://law.go.kr/LSW/lsRvsDocListP.do?chrClsCd=010102&lsId=005028): Article 5 phased rates for 2022–2025.
- [Decree No. 36001, effective 2026-01-01](https://www.law.go.kr/lsInfoP.do?chrClsCd=010102&lsiSeq=282431&viewCls=lsRvsDocInfoR): restores KOSPI transaction tax to 0.05% for transfers from that date.
- [Rural Special Tax Act, Article 5(1), item 5](https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1032879999): 0.15% of sale value.
- [Mirae Asset's dated implementation notice](https://securities.miraeasset.com/bbs/board/message/view.do?categoryId=66&messageId=2336448&selectedId=1000000202&selectedId=1000000203&selectedId=1000003101): 2026 transfer-date change applies to sales executed from 2025-12-29. This corroborates why a fill-date January 1 switch is incorrect.

The final date is a **bounded implementation coverage limit**, not a claim that
the law expires on 2026-10-01. Dates outside the verified table are blocked until
a separately reviewed extension. A trade before that limit can still settle
after it and must be blocked. Settlement dates/evidence must be supplied by a
verified external settlement calendar. The research calendar (which excludes
incomplete but real trading dates) cannot substitute for it. No calendar-day +2
or observed-bar +2 calculation is implemented.

Separately approved, versioned **research assumptions**, not broker history or
verified statutory settlement rounding:

1. Apply 10 bps adverse slippage to the reference price.
2. Round BUY price up and SELL price down to whole KRW.
3. Executed notional = that price * integer quantity.
4. Commission on every executed entry and exit = notional * 0.00015, ceiling to
   whole KRW, with no minimum or maximum fee.
5. SELL tax and levy each use their legal rate for the supplied settlement date.
   Floor each component to KRW, then sum; keep both separate from commission.
6. BUY has no sell-side tax/levy. Slippage is already in price and is not deducted
   again. Costs are not included inside the 5% target notional, but cash must cover
   them in addition to notional.

`charges()` returns commission, transaction tax and levy separately. Rates,
source URLs, scope, date basis, assumptions and rounding participate in the cost
fingerprint. All arithmetic uses explicit Decimal context; float financial
arithmetic and ambient-context rounding are avoided.

## Compatibility and identity

The merged `BacktestContract` has two additive explicit forms:

- `minimum_trading_value_krw=None` disables only the optional floor. Existing
  positive floors and hard no-trade checks retain their behavior.
- Costs may include the paired `date_basis=settlement_date` and `tax_rounding`
  fields, distinguishing commission rounding from tax rounding. Missing one of
  the pair is invalid. Existing payloads remain byte/identity compatible.

The baseline compiler includes these semantics and references its policy hash
from target, stop, time-exit and sizing rules. The policy fingerprint covers every
locked rule, costs, evidence and explicit quality allowlist. The compiled contract
also binds the period. Candidate identity binds signal -> model/dataset/features,
policy, compiled contract, chronology, barrier and account view. The existing
research signal identity preserves Down-model provenance even though Down cannot
create baseline trades. Configuration changes necessarily produce new identities.
Do not change any locked assumption in response to later performance.

No preexisting execution, runner, inference, signal or decision path is replaced.
There is no CLI/run dispatch in this change. Integration with verified account
views, settlement calendars, causal valuation and delayed fill publication belongs
to the next runner implementation. Unadjusted-price discontinuities, no-trade,
unapproved gaps and known incomplete dates remain blocked by existing quality
contracts. There is no price adjustment, split inference or data repair.

## Validation

Synthetic unit examples cover eligibility, conditional/null states, source/hash
binding, next-session preflight, no-pyramiding and pending positions, barrier/exit
configuration, deterministic ordering, fee-aware sizing, delayed-volume checks,
slippage, separate costs, year-boundary settlement rates, missing policies,
immutability and legacy contract identity. They are not investment-performance
experiments. Run the repository offline pytest suite, Ruff, formatting, mypy and
`git diff --check`; no live API or real model evaluation is necessary.

## Approved runner-planning extension (version 2)

The subsequent runner request explicitly approved T-close planning before T+1's
open is known. Strategy version 2 includes the planning-reference price,
slippage/rounding order, immutable intended quantity/reservation, and rejection of
an intended-cost overrun **before** volume-cap reduction. No strategy parameter or
cost rate changed. See [runner chronology and the updated locked fingerprint](baseline-backtest-runner.md).
The former version-1 fingerprint must not be reused for this extended contract.
