# Historical trading report v1

This is a factual projection of single-ticker research execution. It does not add
trading rules, score calibration, model qualification, execution prices or P&L
formulas. Synthetic mechanics remain `synthetic_test_only`; historical research
remains `research_only`, with PIT/OOS/operational flags false.

## Entry points and compatibility

`backtest.trading_report.report_backtest(runner, valuation_policy)` returns an
immutable `ReportedBacktest(run, performance, details)`. It calls the existing
Runner and `calculate_performance` once, including their existing ledger/history
validation, then projects the validated records. There is no caller-controlled
"skip validation" flag and no DB access or model inference in the projector.

`validate_report(details, runner, valuation_policy)` explicitly replays those
same engines and compares canonical report contents. This verifies reproducibility
and integrity, not independent market-data truth or PIT qualification. Missing
source evidence is rejected by the existing validators, not replaced by report
fields or inferred timestamps.

The existing `run_backtest(...) -> (run, performance, text)` signature is retained.
`run_backtest_with_report(...) -> ReportedBacktest` exposes the new projection.
The existing `HistoricalBacktestResult.document` keeps version 1 and its original
run/performance/aggregate fields, adding `detailed_report` and
`detailed_report_hash`. Both backtest CLI input paths include these additional
JSON fields and append factual detail to text output. `format_report(performance)`
still renders the original aggregate view; its optional `details` argument adds
the timeline. The underlying run/performance hashes and economics are unchanged
for identical frozen inputs. The outer result/text necessarily include new detail.

The detailed path is deliberately single ticker. It does not enable general
multi-ticker execution, multiple simultaneous reservations or a new ledger.

## Schema and references

`schema_version = historical_trading_report_v1`. The report hash is
`FrozenJSON.identifier`: canonical JSON containing source references and projected
facts, not rendered text. No current timestamp, random ID or renderer runtime is
added. Static explanation templates are versioned with this schema. Fresh source
audit records can have different identities; replay of the same frozen evidence
is deterministic.

| Field | Evidence / semantics |
|---|---|
| `sources` | run, Performance, tape and original Runner config hashes |
| `timeline` | one row per recorded decision opportunity, plus unavailable historical-input points |
| `positions` | existing verified history episodes, acquisition/sale fill refs and existing Performance values |
| `terminal_orders` | accepted ledger orders, current status/reservations and confirmed execution attempts |
| `unaccepted_order_requests` | proposed BUY/SELL requests never accepted as ledger orders; not fabricated rejected orders |
| `account_timeline` | original Performance equity curve, cash including reservations and position observations |
| `final_account` | original final cash/reservations/quantities/ledger realized P&L |
| `totals` | unchanged Performance realized/unrealized P&L, costs and final equity |
| `limitations` | original limitations plus explicit reporting evidence boundaries |

Each timeline row includes ticker, trading date, opportunity ID/sequence, analysis,
model and snapshot IDs, input hash, context/applicable direction/raw score,
configured thresholds, available linear contributions/intercept, action/status,
selected rule, validation checks and source reason codes. It also includes proposed
quantity/costs, observed account state, candidate admission, reservation receipt,
accepted order, execution attempts and any explicitly linked position IDs.

The compact projection carries no model parameters, training dataset or full
snapshot per timeline row. Existing run evidence remains the source of truth.
Run/performance/historical-document hashes and order indices are computed once;
no source DB or repeated per-row snapshot loading is introduced. Output size is
bounded by the supplied run's opportunities, events and fills, without generated
calendar days or background collection.

A `source` object refers to the run event/decision input/policy/checkpoint, verified
position history, Performance or frozen historical input as applicable.
`historical_input_document_hash` is the canonical **full frozen document** hash;
it is distinct from the builder's semantic identity that excludes signal audit
creation time. Every `null` timeline fact has a stable `unavailable` code. An empty
position-ID link means no explicit acquisition/exit receipt links that decision;
HOLD is not assigned a position by guessing from date overlap.

## Times and execution outcomes

- `opportunity_cutoff`: recorded Runner event cutoff, or the historical builder's
  explicitly labeled release assumption for an unavailable requested opportunity.
- `decision_cutoff`: actual Decision Engine timestamp, null if it did not run.
- `analysis_anchor_at`, `analysis_available_at`, `model_created_at` and
  `latest_source_collected_at`: separate original evidence fields.
- `submitted_at`, reservation acceptance `at`, execution `attempt_at`, fill
  `fill_at` and `fill_known_at`: their respective original records.

Unknown timestamps remain null. A missing bar's planned release assumption is not
represented as an observed decision or market event. Historical execution can use
a later session's OPEN reference while the recorded fill becomes known only after
complete-bar publication. The report does not invent a 09:00 exchange fill time.

Admission (`candidates`) and reservation (`admissions`) are different existing
stages. A BUY decision can fail admission or reservation. Rejected execution can
leave an accepted order pending; it is never promoted to a fill or exit.
`no_later_market_event_in_run` means no later event for that ticker exists inside
the selected run, not that exchange-calendar completeness has been verified.
Other pending orders retain `accepted_not_filled` and their actual rejection
attempts. No terminal liquidation is invented.

## Explanations and no-buy/no-sell outcomes

Templates describe only recorded rules/checks or conditions implied by a validated
virtual decision: score met/missed threshold, existing position, inapplicable
entry/exit context, insufficient cash, quality failure, pending order and stage
rejection. If an earlier validation blocked a decision, later conditions are
`not_evaluated`, not retrospectively guessed. Unknown source codes remain visible
rather than being replaced by a generic WAIT or a fictional narrative.

Scores are conditional and uncalibrated, not guaranteed returns or complementary
probabilities. Linear feature values, standardized values, intercept and logit
contributions are shown only when public signal evidence supplies them. These
are statistical contributions, not established economic causation. Missing
contributions receive `not_present_in_public_signal`. No LLM is involved.

## Positions, durations and P&L

Position states are the existing `still_open`, `partial_exit` and
`confirmed_full_exit`. A SELL decision alone cannot confirm exit. Acquisitions,
sales and reentry links come from verified receipts. Initial account holdings
have unknown acquisition timestamps/BUY prices unless actually recorded.

Calendar duration uses recorded fill timestamps: elapsed microseconds and Seoul
calendar-date difference. Partial sales also show duration from acquisition to
that sale. Open episodes have no exit duration; `observed_duration_to_run_end`
is explicitly an observation span, not a liquidation. Unknown initial acquisition
time keeps both spans unavailable.

Verified trading-session counts are unavailable without a suitable calendar
contract. Synthetic fixtures may expose a count from explicit virtual
`calendar_verified`/sessions/coverage evidence, labeled
`synthetic_virtual_calendar_contract`; basis is strictly after entry through exit.
Research-calendar exclusions are not official holidays. Real research records
remain `independent_calendar_attestation_unavailable` in v1, even if a virtual
boolean says true. This PR does not qualify an independent real calendar or PIT.

Completed net P&L is copied from Performance trade episodes. For the sole open
single-ticker episode, partial-sale realized P&L is copied from
`open_episode_realized_pnl`; unrealized P&L/mark quality comes from the final
position valuation. No new P&L/cost formula or second economic ledger exists.
Fees, taxes and slippage are the original fill values; aggregate costs remain
Performance totals. Slippage is already embedded in prices and is not deducted
again. Missing/stale marks keep unrealized results unavailable.

Performance account observations contain total cash and positions, not each
post-event available/reserved-cash split. The report exposes exact available and
reserved cash from decision snapshots and final ledger state and states this
coverage limitation instead of inventing intermediate values.

## Example and tests

A synthetic fixture reports `BUY -> SELL -> BUY`, three confirmed later fills,
one completed episode and one terminal open episode. Its original Performance
reports completed net P&L 170.55 KRW and open unrealized P&L -10.82 KRW. These are
mechanics fixture values, not investment performance.

`tests/backtest/test_trading_report.py` covers the lifecycle, HOLD/WAIT, context
and threshold reasons, cash/admission/reservation/execution distinctions, partial
exit/reentry, missing marks/timestamps, initial acquisition unknown, actual public
linear feature contributions, deterministic JSON/hash/replay and tampered/missing
source evidence. Existing historical contract tests supply storage fixtures;
provider metadata in those tests is not independent real-market evidence.

Corporate Actions Coverage remains 0/993. Corporate Actions, Runner Tail,
Run Input Freeze and strict PIT qualification remain blocked. No DART/KRX/live
collection, operational registration, new risk exits or brokerage execution.
