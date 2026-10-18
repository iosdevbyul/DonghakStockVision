# Five-ticker shared-account historical research

## Integration boundary

`backtest.portfolio.run_five_ticker` acquires bounded public linear/HGB research
inference once and runs exactly five explicit securities against one Ledger.
`portfolio_report.replay_five_ticker` is the offline JSON entry point; it needs
neither databases nor estimators. `render_five_ticker` renders the factual text
report. No HTTP API, scheduler or operational registration is added.

The existing Ledger, PR #111 reservation ancestry/CAS transitions, Decision Engine,
Position History, account valuation and Performance remain the economic authority.
The prior Runner's single-pending behavior remains the default. Only the explicit
`five_ticker_shared_v1` account mode enables concurrent reservations. Existing
single-ticker result/report contracts are not replaced.

## Request contract

`FiveTickerRequest(FrozenJSON.freeze(request))` validates
`five_ticker_historical_request_v1`. Required fields:

| Field | Contract |
| --- | --- |
| tickers | Exactly five unique validated strings, preserving zeroes/alphabetic suffixes; normalized lexically |
| start, end | Inclusive canonical Seoul dates, fewer than 366 elapsed days |
| initial_cash | Positive decimal KRW string, one shared balance |
| model_family, model_id | `linear` or `hgb`, explicit identity |
| model_references | Linear model/training snapshot/dataset IDs; HGB up/down fingerprints and checkpoint SHA-256 |
| market | `KRX_KOSPI` |
| calendar | Explicit session basis and optional existing ResearchCalendarPolicy |
| evidence_scope | `synthetic_fixture` or `retrospective_historical_research` |
| config | Explicit manifest, tape policy, capture time, quality, availability assumption, Decision/Execution/Ledger/Valuation/Admission policies, BUY/SELL reservation ceilings, decision dates, mark field and release steps |

There must be 1–10 explicit decision dates (at most 50 opportunities), inside the
requested range. Admission `ordered_tickers` must contain the same five securities;
this list, rather than caller ticker-list order, determines priority. Releases
are explicit date/ticker/status commands (at most 50); duplicates are rejected.
No checkpoint, sizing, threshold or date is selected to manufacture profitable trades.

A concrete executable request fixture and isolated SQLite setup are in
`tests/backtest/portfolio_helpers.py`. Production callers provide their own
validated policy documents and read-only sources:

```python
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest, run_five_ticker
from donghak_stock_vision.backtest.portfolio_report import replay_five_ticker

request = FiveTickerRequest(FrozenJSON.freeze(request_document))
result = run_five_ticker(request, readonly_market, readonly_analysis,
                         run_output=isolated_output)  # linear
assert replay_five_ticker(FrozenJSON.freeze(result.to_dict()["frozen_input"])) == result
print(result.to_dict()["report"])
```

HGB requires `analysis=None` and an explicit verified `hgb_provider`. Checkpoint
identities are checked before acquisition. SQLite sources must have query_only
set; source/output file identity is rejected, including aliases. Protected source
DBs are never written. Isolated output stores may receive newly inferred signals.

## Scheduler and clocks

The existing tape merges source events in validated availability/sequence order.
At each event, explicit releases run first, followed by eligible pending fills for
that event's ticker. At the last sequence in an equal-availability group, new
opportunities run in the explicit admission priority. All same-time source events
and pending fills therefore precede new reservations. Each decision/admission uses
the latest shared Ledger revision/hash. A pending order only prevents another
order for its own ticker; cash reserved by any ticker is unavailable to all others.
A SELL decision is not an exit; only validated fills change positions.

Source decision-point availability, public signal anchor, actual research
observation, virtual decision, acceptance, daily event occurrence, fill-known and
valuation availability remain separate fields. Daily publication times are an
explicit research assumption. Exact exchange execution timestamps are unavailable;
report fields stay null. No current wall clock is inserted into simulation events.

## Evidence and replay

Frozen transport `five_ticker_frozen_run_v1` carries the original request, one
`historical_input_v2` shared evidence graph, initial checkpoint and bound policies.
`five_ticker_historical_input_v1` scopes the five-security integration. All source
hashes, model/dataset/feature references, calendar, window and market comparisons
are validated. Synthetic inputs require `synthetic_test_only` and stay synthetic;
existing real-data validation/training requirements are not relaxed.

Artifact definitions are canonicalized by artifact_id; duplicates remain invalid.
Only this semantically unordered list is normalized. Replay rebuilds the Runner
configuration and unavailable opportunities from evidence, rejecting mismatched
checkpoints, policies, schedule, opportunity clocks, models and missing artifacts.
`validate_five_ticker_report` compares a supplied report with full offline replay.
Canonical FrozenJSON identifiers cover evidence, not rendered text or elapsed time.
The detailed report's `historical_input_hash` identifies the canonical shared
transport document. Existing Runner/admission fields retain their prior historical
semantic/legacy hash contracts; those hashes need not equal the transport hash.

Immutable owner-bound validation caches reuse the result only for that exact
FrozenJSON and still compare the supplied tape. New/modified owners revalidate.
The Runner caches one Position History proof only while state and evidence match;
execution and history context reuse requires exact packet/source equality. No
process-global cache or alternate economic ledger is introduced.

## Detailed report

`five_ticker_historical_result_v1` contains frozen_input, run/run_hash,
performance/performance_hash, detailed_report/detailed_report_hash and text report.
The detailed report is `five_ticker_historical_report_v1`:

- One row for each requested date/ticker, including explicit unavailable rows.
- Analysis/model/snapshot and HGB checkpoint-pair references; public features,
  linear contributions/intercept when supplied, explicit unavailable HGB attribution.
- Context, conditional uncalibrated score, configured thresholds, original decision
  checks/rule/reason codes and factual entry/exit condition explanations.
- Account at decision, admission and reservation outcomes, order ID, fill/rejection
  receipts, source event clocks, fees/taxes/slippage and evidence hashes.
- Validated acquisition/sale fill links, position episodes, open/full-exit status,
  calendar duration, verified-session duration only with evidence, reentry links.
- Per-ticker realized attribution from existing Ledger deltas; episode realized
  P&L and valuations from existing Position History/Performance. No new P&L formula.
- Account cash/reservations/positions/equity at existing observation points,
  terminal orders and unsupported valuations with explicit reason/coverage.

Insufficient-cash risk decisions may already be WAIT with zero requested quantity;
the report preserves that fact and still lists the failed admission intent. It
does not invent a BUY order. Missing/stale marks preserve null aggregate valuation
rather than treating a partial portfolio as fully valued. Initial acquisitions
without fill evidence have unknown times. Partial-fill/full-fill behavior follows
the selected existing execution policy; no new partial-fill engine is added.

## Scope and limitations

Genuine public inference tests use deterministic synthetic training/input fixtures;
their observed outcomes are reported without changing scores. Separate, clearly
labelled `controlled_mechanics_test_double` signals exercise BUY/SELL, cash
competition, reentry and releases. Their profits are mechanics evidence, not model
performance or real historical investment results.

Retrospective research is not strict PIT or proven OOS. Model training cutoffs,
revision histories, liquidity, listing/halt status and corporate actions are not
newly approved. No forced liquidation, new exit rule, allocation strategy, live
acquisition, brokerage, Operations/publication or iOS/Web changes are introduced.
Corporate Actions Coverage remains 0/993; Corporate Actions, Runner Tail, Run Input
Freeze and Strict PIT remain blocked without independent evidence.

Bounds constrain dates/opportunities, not source artifact byte sizes. Shared input
avoids per-opportunity model/training copies, but accepted-order receipts and
journals still embed source evidence. Trade-heavy result size and Python/native
memory can be large; no measured peak or unlimited-scale guarantee is claimed.
See the measured companion report for actual bounded fixture counts and timings.
