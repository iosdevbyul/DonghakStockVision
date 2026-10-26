# Forward signals to deterministic PAPER execution

This offline operator workflow connects the original prospective signal journal to the
existing Risk Engine and `DeterministicPaperBroker`. It is a functional synthetic
integration for KOSPI 005930 / KRW, not a live brokerage interface, a retrospective
backtest, a profitability claim, or operational authorization.

For the additive v2 path using independently reviewed provider captures, see
[Authorized KRX forward observations to PAPER](authorized-krx-forward-paper.md).
The restrictions below describe the original v1 synthetic workflow.

## Reused components and boundaries

`storage.forward_paper` composes the original forward capture verifier, signal
semantic verifier, Trading Core instruments/orders/accounts, broker preflight and
broker fills. Infrastructure owns persistence; strategy and trading domain modules
have no new storage, CLI, HTTP, or database dependencies. Original Risk Engine,
Paper Broker, SMA, retrospective analysis and journal contracts remain unchanged.

Forward signal v1 explicitly has `execution_authorized=false`. This adapter does not
change that flag. A separately pinned `PAPER_SYNTHETIC` account/action authorizes only
an offline fixture exercise. Real-provider signals remain blocked by the original
independent provider-authorization requirement. Retrospective historical SMA outputs
are never accepted as forward decisions or as warm-up inputs.

## Run the complete synthetic example

Use a new private directory. Existing directories are refused.

```sh
dsv paper-trade offline-demo --workspace /tmp/dsv-paper-example > /tmp/paper-demo.json
```

The example uses the original capture interfaces with `httpx.MockTransport`: displayed
KRX URLs are mocked requests, not network calls. It retains raw bytes, original capture
manifests, signal requests and the append-only signal journals. Trading dates are July
2025 synthetic scenario labels; current local capture times do not prove historical
availability. All persisted instants are UTC, independent of those date labels.

The first captured sequence is `100, 80, 120, 80`. With fast=1, slow=2, the first three
observations produce BUY; adding the fourth produces SELL. Both original decisions
are journaled and pinned before paper processing. BUY consumes only its declared
three observations. A separate constant-price capture produces HOLD. The example
executes BUY, retries it idempotently, executes SELL and records HOLD without an order.

Inspect these actual synthetic settlement figures:

| Event | Quantity | Reference | Fill price | Fee | Tax | Available cash | Position |
|---|---:|---:|---:|---:|---:|---:|---:|
| Opening | 0 | unavailable | unavailable | 0 | 0 | 10000 | 0 |
| BUY | 2 | 120 | 121.20 | 0.25 | 0 | 9757.35 | 2 |
| SELL | 2 | 80 | 79.20 | 0.16 | 0.32 | 9915.27 | 0 |
| HOLD | 0 | no new execution | unavailable | 0 | 0 | 9915.27 | 0 |

The original broker rounds fixture prices/costs, reserves whole-share purchase funding,
releases reservations and enforces cash/share conservation. Fees total 0.41 KRW and
taxes total 0.32 KRW. Slippage is embedded in fill price, not deducted a second time.
These are deliberately tiny synthetic prices, not Samsung market prices. The cash
change is not exposed as an investment return.

The directory contains `account.json`, `buy-action.json`, `sell-action.json`,
`hold-action.json`, `paper.sqlite3`, and the source capture/journal files. Pins in the
JSON output are specific to this locally executed example; retain them independently.
Do not reuse example identifiers for real observations.

```sh
ACCOUNT_PIN=$(python -c 'import json; print(json.load(open("/tmp/paper-demo.json"))["account_pin"])')
HEAD_PIN=$(python -c 'import json; print(json.load(open("/tmp/paper-demo.json"))["journal_head_pin"])')
dsv paper-trade history --account /tmp/dsv-paper-example/account.json \
  --account-pin "$ACCOUNT_PIN" --journal /tmp/dsv-paper-example/paper.sqlite3 \
  --journal-pin "$HEAD_PIN"
dsv paper-trade portfolio --account /tmp/dsv-paper-example/account.json \
  --account-pin "$ACCOUNT_PIN" --journal /tmp/dsv-paper-example/paper.sqlite3 \
  --journal-pin "$HEAD_PIN"
dsv paper-trade verify --account /tmp/dsv-paper-example/account.json \
  --account-pin "$ACCOUNT_PIN" --journal /tmp/dsv-paper-example/paper.sqlite3 \
  --journal-pin "$HEAD_PIN"
```

All three commands perform original-source verification and broker semantic replay.
Replay happens only in disposable in-memory brokers: reads never append orders or
fills to persistent state. No HTTP API, scheduler, provider request, or model inference
is involved.

## Explicit account, sizing and risk configuration

`forward_paper_account_v1` requires an account ID, initial cash and canonical UTC
initial time. The initial account is cash-only, single-currency KRW. Existing Trading
Core types validate it; unknown initial position cost basis is not invented.

The account document requires every RiskPolicy field exposed by this workflow:
`policy_id`, `max_order_notional`, `max_position_fraction`, `max_exposure_fraction`,
`max_daily_loss`, all four maximum evidence ages, and `risk_timezone`. Markets/currency
are fixed to KR/KRW. Fixture mechanics require explicit price/cost quantums, buy/sell
fee rates, sell tax rate, slippage, session age and command limit. Decimal values must
be strings. There is no permissive configuration fallback. The example specifies
5000 KRW maximum order notional, 0.5 concentration, 0.8 exposure, 1000 daily loss,
60-second evidence ages, 0.001 fees, 0.002 sell tax and 0.01 slippage.

Each `forward_paper_action_v1` requires:

- `mode=PAPER_SYNTHETIC`, capture root, original request and its independently retained pin;
- sealed signal journal path/head pin and an explicit signal ID;
- whole-share `quantity` from 1 through 10000 (no implicit sizing or rounding);
- canonical UTC `at` and `fill_at`, with decision/journaling before `at` and
  `at < fill_at <= at + 60 seconds`;
- explicit synthetic `session_open`, `trading_enabled`, and signed `daily_pnl`.

The daily P&L input is an operator-declared synthetic **risk scenario**, not a computed
performance result. Original Risk Engine checks it; this workflow does not infer it
from cash. Account equity supplied to that check is derived exactly from current
native cash plus current positions at the explicit synthetic close reference. No
real quote freshness, calendar opening or independent price publication is asserted.

BUY/SELL create an order only after broker preflight allows it. Broker submission and
fill each independently enforce risk again. Insufficient cash/shares, concentration,
exposure, notional and daily-loss failures preserve structured reasons. Preflight
rejection and HOLD/INSUFFICIENT_DATA retain exactly the prior broker state. The action
log still records these outcomes. A closed synthetic market produces a pending order,
not a fictional fill; reservations remain visible.

## Dry-run and execution

Use `dry-run` and `execute` with the same explicit account, action and current trusted
paper head. The genesis pin is available as `storage.forward_paper_journal.GENESIS`.
A fresh journal must start at that pin.

```sh
dsv paper-trade dry-run --account ACCOUNT.json --account-pin ACCOUNT_SHA \
  --journal NEW_PAPER.sqlite3 --journal-pin GENESIS_SHA --action-file BUY_ACTION.json
dsv paper-trade execute --account ACCOUNT.json --account-pin ACCOUNT_SHA \
  --journal NEW_PAPER.sqlite3 --journal-pin GENESIS_SHA --action-file BUY_ACTION.json
```

Dry-run does not create or change the journal. Execute returns the new head pin;
retain it before the next action. Output is canonical `forward_paper_processing_v1`
JSON with the original signal/decision/input identities, risk, order, submission,
execution receipt, before/after account and broker state identities. Original receipt
fees/taxes/slippage and fill times remain authoritative. Signals and fills are linked
through order/source IDs. Explicit mark-based equity/exposure are descriptive
synthetic values. Average entry cost and realized P&L are null because the broker
snapshot has no authoritative basis/P&L contract. No extra P&L ledger is introduced.

## Idempotency, verification and interrupted operations

One signal ID appears at most once in this explicit paper-account journal. An identical
retry with its current trusted head returns the original result without changing bytes.
Changed quantity, clock, source pin or assumptions for the same ID is a conflict. A
changed account/policy pin is rejected. Distinct journals are explicit isolated paper
experiments, not a global account service.

The log uses SQLite `BEGIN IMMEDIATE`, FULL synchronization, immutable UPDATE/DELETE
triggers, unique signal IDs, canonical hash chaining, a pinned account configuration
and independent semantic replay. Triggers alone are not evidence authentication.
Changed original raw receipts, manifests, signal records or rehashed forged account
results fail verification. The head pin detects truncation and substitution only when
retained independently. It is not independent custody.

An interrupted transaction rolls back; brokers exist only in memory. If commit succeeds
but the response is lost, retrying the old head fails closed. Reconcile against the
**previous independently retained head and exact intended action**, not a hash copied
from an untrusted database:

```sh
dsv paper-trade reconcile --account ACCOUNT.json --account-pin ACCOUNT_SHA \
  --journal PAPER.sqlite3 --journal-pin PREVIOUS_TRUSTED_HEAD --action-file INTENDED_ACTION.json
```

Reconciliation independently replays sources/broker history and accepts only the exact
intended successor (or the unchanged previous head). It writes nothing. An unexpected
additional action or altered intent is rejected. Retain the reconciled head before
retrying. Empty/interrupted initialization and unknown databases are refused, not
silently repaired. Back up output files and pins explicitly; no new backup/custody
service or process-crash durability beyond SQLite's local transactions is claimed.

## Limits and real-data blockers

- At most 32 actions per journal, 128 broker commands, 256 KiB per action, 8 KiB account
  config, 384 KiB entry and 16 MiB SQLite file. Capacity errors roll back. Full replay
  on reads is bounded but not a scalable continuous execution service.
- Source journals and capture DB snapshots must remain sealed at their original pins.
  Appending to those sources requires a future versioned source-snapshot workflow;
  old pins are never silently replaced. Keep all original input paths available.
- Only KOSPI 005930, KRW, fixed whole-share market orders, explicit full-fill fixture
  events and cash-only opening accounts are supported here. Pending orders are retained;
  this CLI does not provide later cancellation/fill management. Broker contracts remain
  available for a future explicit lifecycle adapter.
- No supported cost basis, realized P&L, independently authenticated publication time,
  independent custody, real session status or actual exchange execution is invented.
- Existing provider authorization remains unverified. Authorized real market inputs
  require separately approved capture and independent evidence before this boundary
  can be extended. Historical OHLCV/SMA, frozen OOS, final-test and holdout inputs are
  not substitutes. Protected-partition checks run before source file access.
- This work does not activate trading or implement the next real-provider milestone.
