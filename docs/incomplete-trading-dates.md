# Known incomplete trading dates in historical research

## Meaning and first research configuration

A market holiday is a date on which the exchange was closed.
A known incomplete trading date is an actual trading day whose market-wide input
is incomplete. Excluding it is a retrospective research decision, not a statement
that the exchange was closed, and not PIT revision reconstruction.

Save this explicit configuration as a caller-owned JSON file:

```json
{
  "version": 1,
  "reason": "known_incomplete_trading_date",
  "action": "exclude_research_session",
  "incomplete_dates": ["2024-03-28", "2025-03-21"]
}
```

Neither date is a library constant or an implicit default. Those sessions failed
whole-market atomic ingestion because one source row had zero OHL with nonzero
volume/value. The surviving Samsung rows must also be excluded from this research.
No market row is deleted, repaired, downloaded or reclassified as a holiday.

## Contract and configuration entry points

`data.research_calendar.ResearchCalendarPolicy` is a frozen dataclass with a tuple
of plain dates. It normalizes duplicates/order, validates version/reason/action,
serializes through `to_dict/from_dict`, and exposes canonical SHA-256 `identifier`.
It is historical-research-only. Omitting it preserves legacy behavior and hashes.

Phase 2 public API:
`capture(..., research_calendar=policy)`.
The existing train CLI accepts `--research-calendar /path/to/research-calendar.json`.
This is additional to the existing required market/analysis paths, snapshot cutoff,
tickers, dates and research acknowledgement; no training is run by this document.
Explicit splitting remains available through
`TrainingService.train(snapshot_id, split_configuration=...)` and the Python API.
Initial capital is not part of either research data policy.

Historical APIs:
`build_historical_input(..., research_calendar=policy)`.
Both `dsv backtest-input --config ...` and the existing historical backtest config
accept an optional top-level `research_calendar` object with the same JSON content.
No new execution mode, price/cost policy or live route is introduced.

## Session construction and propagation

Before this policy, Phase 2 obtained sessions from per-ticker snapshot rows;
its verified-calendar quality check also used the quality manifest sessions.
Historical input separately read Phase 1 rows and constructed an ordered tape.
Execution traversed that tape; performance obtained marks and event frames from it.

Now both input boundaries use the same explicit policy:

* Phase 2 freezes only retained rows for every ticker. snapshot_bars defensively
  filters imported snapshots as well.
* Feature windows count retained bars. Direct feature input containing an excluded
  bar is rejected. Verified-session comparisons subtract the research exclusions
  from their expected list without rewriting the original exchange calendar.
  The unverified seven-day gap heuristic discounts explicitly excluded dates;
  other missing dates and corporate-action exclusions keep their existing checks.
* Labels count the next five retained observations. label_end can consequently
  move later. Explicit split purge is unchanged: train ends before validation.start,
  validation ends before final_test.start, final labels end on/before final_test.end.
* Direct research inference on an excluded anchor raises
  known_incomplete_trading_date before creating a signal artifact.
* Historical input filters all tickers before sequence assignment, source copying,
  Phase 2 analysis calls and decision-point creation. No artificial HOLD/WAIT or
  synthetic price is generated for an excluded session.
* FrozenTape rejects an event belonging to an excluded session.
  Execution therefore uses the next retained eligible session. Reservation,
  slippage, fees, taxes, same-event protection and full-fill assumptions are unchanged.
* Performance sees no excluded-session marks or event frames. Direct event valuation
  for an excluded daily-session publication point is rejected. Start/end on an
  excluded trading date are rejected explicitly rather than silently moved.
  Initial/final boundary accounting remains distinct from session-event valuation.

### Trading date versus publication timestamp

Exclusions refer to the bar's Asia/Seoul **trading_date**, not the calendar date of
artifact creation or bar publication. Existing historical bars become public at
D+1 00:00 Seoul. A valid March 20 bar can therefore be published on March 21;
this does not reintroduce the excluded March 21 trading session.

Example: March 20 decision anchor -> March 21 bar omitted -> March 24 retained
bar OPEN as execution reference. The simulated fill becomes known only when that
complete March 24 bar is published on March 25 at 00:00, as before.
This policy does not invent an intraday open-publication feed.
There is no March 21-session decision, execution or valuation mark.

## Reproduction and fail-closed checks

Phase 2 stores the canonical policy inside the immutable snapshot. Dataset
snapshot_id and model snapshot_id/dataset_id/split_hash already bind this content.
Two policies therefore produce different research identities even if their dates
have no rows. Evaluation rebuilds from the same frozen snapshot and policy.
No schema migration or model-score meaning changes are needed.

Historical input carries the canonical policy inside its quality envelope, leaving
the original sessions list intact. The envelope hash is bound by the manifest,
tape and run; it is also retained in source_metadata and the top-level service request.
The extra limitation research_sessions_excluded_not_holidays is reported.
The builder checks snapshot policy equality before any research analysis call.
Missing/different policy yields research_calendar_mismatch and no analyses.
Replay validation checks each frozen analysis snapshot's policy again.

Legacy artifacts without a policy continue using their original observed/verified
sessions. They cannot be silently reused with a new exclusion calendar. Models
for the new study must be built from its policy-bearing snapshot.

## Remaining real-study requirements

The two original sessions remain incomplete in the physical market database.
This choice deliberately changes the research calendar, potentially changing
feature returns, horizon lengths and results; disclose it with the experiment.
It does not verify corporate actions, universe survival, revision history, OOS
model availability or real liquidity. Previously observed missing days not in
the explicit configuration are not automatically excluded.

Before real Phase 2 training, explicitly select this policy, the approved date
split and snapshot coverage, and review other quality constraints. Then freeze
the model/policies before Final Test. This change neither trains actual KRX
models nor executes a real-data decision/backtest.

The snapshot exposes `research_sessions_excluded_not_holidays` as an analysis
quality flag. Phase 3's existing `allowed_quality_flags` remains fail-closed:
any future research policy must explicitly acknowledge this flag. This feature
does not silently grant that permission or change the decision engine.
