# DonghakStockVision 2.0

**Language:** English | [한국어](README.ko.md)

A Python engine for collecting, validating, and storing Korean equity prices, volumes, and trading values, with research-oriented market signals and decision simulations. It is designed for independent use by iOS, web, and other applications. The Phase 1 market-data system, Phase 2 conditional-signal research, and Phase 3 virtual research decisions are implemented. **Research results are not approved trading advice or authorized operational inference.**

| Phase | Objective | Status |
| --- | --- | --- |
| 1 | Bootstrap, collect, validate, and store market data | Implemented; live KRX verification requires approved API access |
| 2 | Research upward/downward signals | Implemented; live-market performance and operational PIT verification incomplete |
| 3 | Buy/hold/sell/wait decisions | Research-only virtual decisions; operational execution blocked |
| 4 | Backtesting and paper trading | Research backtesting components exist; no fully validated operational system |
| 5 | Continuous operation | Not an authorized always-on trading service |

The repository retains legacy root-level Python/JSON experiments and models. They are **legacy artifacts** and are not imported or executed by the 2.0 package. Installation, linting, and build targets use `src/` and `tests/`; legacy targets, strategies, and models were not reused. Additional PIT, provenance, governance, and backtesting contracts can exist without implying production readiness.

## Installation

Python **3.12+** is required. Run from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,learning]'
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate`. `python -m donghak_stock_vision` has the same entry point as `dsv`; `python -m build` produces wheel and sdist packages. The core runtime uses httpx for HTTP and python-dotenv for environment configuration; Windows also installs tzdata. SQLite is in the Python standard library. Optional `learning` dependencies include scikit-learn >=1.9.1,<1.10 and NumPy >=2.3,<3. Model files store numeric JSON instead of loading pickle. Training records installed library versions, random seed, and code hash.

## Run without credentials

The Fake Provider supplies **fixed synthetic data, not real market prices**, for tickers `005930`, `000660` and dates 2024-01-02/03/04/05/08. It does not invent other tickers or dates. Always use a separate database from real market data.

```bash
dsv --db .data/demo.sqlite3 collect --provider fake --tickers 005930 000660 --start 2024-01-02 --end 2024-01-04
dsv --db .data/demo.sqlite3 update --provider fake --tickers 005930 000660 --start 2024-01-02 --end 2024-01-08
dsv --db .data/demo.sqlite3 query --tickers 005930 --start 2024-01-02 --end 2024-01-08
dsv --db .data/demo.sqlite3 validate
```

The first collect inserts six rows; the following update inserts four more. Repeating the same command yields `changed: 0`. Standard output is JSON, and standard error contains structured logs with UTC timestamps (`Z`). For ticker collection, `succeeded`, `failed`, and `empty` count tickers; `changed` counts inserted or corrected rows. `empty` is included in `succeeded`, but does not establish dataset coverage.

Exit codes: 0 for success, 1 for partial collection failures or integrity failures, and 2 for configuration, input, or storage failures. One ticker failing does not prevent the others from being processed.

## KRX configuration and collection

The default source is the official **KRX Open API**. The original Phase 1 live test was not verified without an approved API key and API permissions. No automatic fallback to unofficial sites or synthetic data occurs.

1. Obtain an approved API key via the [KRX API guide](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp).
2. Apply for the relevant daily stock-market API for each market.
3. Set `KRX_API_KEY` in `.env`. It is sent only in the `AUTH_KEY` header.
4. Execute the commands below. The current directory supplies `.env`; existing environment variables take precedence.

```bash
dsv collect --tickers 005930 000660 --start 2024-01-02 --end 2024-01-31
dsv update --tickers 005930 000660 --start 2024-01-02 --end 2024-02-15
dsv collect --market KOSDAQ --tickers 035900 --start 2024-01-02 --end 2024-01-31
dsv query --tickers 005930 --start 2024-01-02 --end 2024-02-15
dsv validate
```

Date bounds are inclusive. The final date must be **before today in Asia/Seoul**. KOSPI and KOSDAQ are supported from 2010-01-04; KONEX from 2013-07-01. Weekend requests are skipped, but weekday holidays are determined by responses rather than an invented calendar. No missing-day prices are generated.

### Whole-market KRX collection

In addition to specific ticker collection, `collect --all` is supported only with `--provider krx`. `--all` and `--tickers` are mutually exclusive.

```bash
.venv/bin/dsv --db .data/market.sqlite3 collect --all \
  --start 2022-01-01 --end 2025-06-30 --provider krx --market KOSPI
```

For normal responses, the collector performs one full-market request per weekday rather than a separate request for every ticker. It archives each original response once, validates all rows, and saves each day's valid bars in one transaction. Existing retry, rate-limit, and request-budget protections remain in place. Failed dates are recorded and processing continues to the next date; the existing database is not reset.

For whole-market results, `succeeded`, `failed`, and `empty` count **dates**, `rows` counts validated bars, and `changed` counts inserted or corrected bars. Specific-ticker output retains its original meanings. `update --all` is not supported; use an explicit `collect --all` range again. See [whole-market collection and raw linkage](docs/market-collection.md) (Korean).

For `update`, the default overlap is seven days before the most recently stored date within the requested range (`--overlap-days N`). New tickers use the complete requested period. Collect older missing or corrected intervals explicitly with `collect`. Full historical back-adjustment of corporate actions is not supported.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KRX_API_KEY` | None | Approved KRX key; unnecessary for Fake |
| `DSV_DB_PATH` | `.data/market.sqlite3` | Raw, cleaned, audit, and request-limit store; overridden by `--db` |
| `DSV_KRX_MARKET` | `KOSPI` | `KOSPI`, `KOSDAQ`, or `KONEX`; overridden by `--market` |
| `DSV_REQUEST_INTERVAL` | `1.0` | Minimum seconds between HTTP attempts, >=1 |
| `DSV_DAILY_REQUEST_LIMIT` | `9500` | Per-Seoul-day limit shared through the same DB, 1–9500 |

The local default is 9,500 attempts to allow headroom below KRX's stated 10,000 requests per key/day. Retries count against the limit. **Collection processes sharing one key must use the same database** to coordinate the local counter; requests made by other apps/databases cannot be counted and may require a lower limit. HTTP 429, temporary 5xx and network failures permit up to three attempts with exponential backoff and Retry-After. Server-directed waits above 60 seconds fail rather than immediately retry.

Real-data displays must attribute “한국거래소 통계정보” (KRX statistics). Check KRX noncommercial-use and third-party redistribution restrictions. Do not commit original responses or database contents to Git. Commercial use and redistribution may require separate rights. See [provider evaluation](docs/providers.md) (Korean).

## Phase 2: Learning, evaluation, and analysis

The `reversal_barrier_v1` label represents the **first short-term opposite-direction barrier hit within the following five sessions in the context of a preceding opposite trend**. It is not a sustained trend-reversal prediction, validated probability of rising prices, profit expectation, or five-day holding instruction. Approved initial research hypotheses fix H=5, a minimum 2% barrier, volatility multiplier 2, and score threshold 0.5. Upward models use only contexts with negative preceding five-observation log returns; downward models use positive ones. Scores outside the applicable context are `null/context_mismatch`; flat context produces `no_context`.

Select one of two explicit modes:

| Mode | Inputs and timing | Restriction |
| --- | --- | --- |
| `historical-research` | Frozen snapshot at actual collection cutoff; historic Seoul 16:00 is a simulated end-of-day anchor | `research_only`; retrospective revisions, corporate actions, and survivorship limitations |
| `point-in-time` | Real `collected_at <= as_of`, verified quality manifest, and an eligible model existing at the time | `pit_review_required`; unavailable to operations without independent review and registration |

Historical bulk-collected data is never silently upgraded to PIT. A research snapshot freezes prices, quantities, turnover, metadata, and a SHA-256 `snapshot_id`. Subsequent deletion/modification of the original market DB cannot alter snapshot-based feature, label, evaluation, or research-inference data. A new dataset requires a new snapshot/model or an explicitly PIT-qualified analysis. Cross-snapshot inference with a research model is rejected.

**Minimum real training data:** five tickers and 504 eligible anchor dates overall. Per direction, train requires 1,000 rows / 252 dates / at least 50 positives and negatives; validation and test each require 200 rows / 63 dates / at least 20 positives and negatives. Features require at least 11 eligible daily bars. Insufficient data yields explicit counts and `insufficient_data`, never fabricated scores. The five-day Fake demo cannot train a model.

The dates and ticker IDs below are examples only: the local database must actually contain the requested data. `--snapshot-as-of` must include a timezone and correspond to when the data was truly available. Missing research quality evidence requires explicit acknowledgement; known invalid ranges remain excluded.

```bash
dsv --db .data/market.sqlite3 train --mode historical-research \
  --tickers 005930 000660 035420 035720 005380 \
  --start 2019-01-02 --end 2024-12-30 \
  --snapshot-as-of 2026-09-23T09:00:00+09:00 \
  --acknowledge-research-limitations --analysis-db .data/analysis.sqlite3

# Set the shell variables below from train JSON output.
MODEL_VERSION='<model_version>'
SNAPSHOT_ID='<snapshot_id>'
dsv evaluate --mode historical-research --model-version "$MODEL_VERSION" \
  --analysis-db .data/analysis.sqlite3
dsv infer --mode historical-research --model-version "$MODEL_VERSION" \
  --snapshot-id "$SNAPSHOT_ID" --anchor-date 2024-12-27 --tickers 005930 \
  --analysis-db .data/analysis.sqlite3
dsv signals --scope research --model-version "$MODEL_VERSION" \
  --snapshot-id "$SNAPSHOT_ID" --direction up --analysis-db .data/analysis.sqlite3
```

Evaluation replays validation/test for the fixed train-fitted model and snapshot. A shared 60/20/20 chronological split purges labels whose future intervals cross split boundaries; PIT also isolates label reception times. Standardization and weights use train data per direction only. Hyperparameter C=0.1/1/10 is selected using validation average precision, never reselected on final test. Reports include conditional frequency and simple reversal baselines, precision, recall, AP, confusion matrix, Brier score, and uncalibrated range diagnostics. Undefined metrics return null with a reason. Repeating experiments against the same holdout is not independent validation.

PIT requires reviewed quality evidence and preserved collection times. The manifest follows the [Phase 2 data contract](docs/data-contract.md) (Korean). Research acknowledgements cannot bypass missing PIT quality.

```bash
dsv --db .data/market.sqlite3 train --mode point-in-time \
  --tickers 005930 000660 035420 035720 005380 \
  --start 2019-01-02 --end 2024-12-30 --as-of 2026-09-23T09:00:00+09:00 \
  --quality-manifest .data/quality.json --analysis-db .data/analysis.sqlite3
# PIT_MODEL is the model_version from the PIT training output.
dsv --db .data/market.sqlite3 infer --mode point-in-time --model-version "$PIT_MODEL" \
  --tickers 005930 --as-of 2026-09-23T10:00:00+09:00 \
  --quality-manifest .data/quality.json --analysis-db .data/analysis.sqlite3
dsv signals --scope analysis --mode point-in-time --model-version "$PIT_MODEL" \
  --as-of 2026-09-23T10:00:00+09:00 --analysis-db .data/analysis.sqlite3
dsv signals --scope operational --direction up --as-of 2026-09-23T10:00:00+09:00 \
  --analysis-db .data/analysis.sqlite3
```

Every model starts as `unregistered`. `validation_qualified` (AP at least 0.02 above the better of two baselines) **does not qualify a model for production**. Without independent evidence of real data quality, PIT, locked final test, and approvals, operational queries return `no_registered_model` and an empty list. No forced registration command or automatic promotion exists. Research and synthetic models are ineligible for operational registration.

New commands return JSON on stdout. Exit code 0 means success or a valid empty query, 1 means insufficient data/model, incompatible mode, or unavailable analysis, and 2 means argument/file/storage failure. Model inference never loads pickle. Results retain actual analysis/model timestamps, market date, input/context status, uncalibrated score, and linear contributions; contributions are not causal explanations. Market and analysis DB paths must differ. Optional `DSV_CODE_REVISION` pins deployment SHA; otherwise a Git SHA or `unknown` is recorded along with source hash.

`train --synthetic-test` relaxes sample limits only for wholly Fake offline fixtures, permanently tagging output `synthetic_test_only`. It cannot support real ticker recommendations or performance claims.

## Validation and CI

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
python -m build
```

Regular tests run without credentials or network access and reject real TCP connections. Mock HTTP tests cover KRX headers, field conversion, and retries; regular integration tests use local SQLite and mocks.

GitHub Actions checks PRs targeting `main`, pushes to `main`, and manual runs on Python 3.12/3.13/3.14, with Ruff, mypy, pytest, packaging, and isolated wheel smoke tests. After installing the learning extra, the wheel tests synthetic training → evaluation → market DB deletion → inference → queries. No secrets are required; live tests are excluded by default.

Only after obtaining approved access, run live API tests manually with the key securely exported in the environment (never paste credentials into commits or chats). Live tests do not automatically read `.env`.

```bash
# After securely exporting KRX_API_KEY to the environment
DSV_RUN_LIVE=1 pytest -m live tests/integration/test_live_krx.py
```

The live test checks collection/requery idempotency for Samsung Electronics (`005930`, KOSPI) on 2024-01-02. Temporary test directories hold fetched data, which must not be committed.

## Architecture and policies

- [Architecture](docs/architecture.md) — boundaries and SQLite rationale (Korean)
- [Phase 2 design](docs/phase2-design.md) — label, samples, chronology, model registration (Korean)
- [Data contract](docs/data-contract.md) — time, raw data, missing values, halted trading, duplicates, adjustments (Korean)
- [Providers](docs/providers.md) — official provider evaluation and KRX mapping (Korean)

`validate` checks SQLite integrity, schema, raw checksums and reference linkage. Without an authoritative historical exchange calendar and security-lifecycle master, it cannot guarantee complete coverage of every trading session; current constituents are not retroactively used to erase delisted securities.

## Phase 3: Virtual research decisions

The [Phase 3 design](docs/phase3-design.md) and [decision contract](docs/decision-contract.md) (Korean) require an exact Phase 2 `analysis_id` plus explicit JSON thresholds, budgets, sell rules, caps, TTL, costs and reentry policy. Omission yields WAIT/blocked; there are no trading-number defaults. Stop-loss, take-profit and maximum holding duration must each remain `disabled` with `parameters=null`. Decision, query and replay require only base dependencies; training new models requires the learning extra.

The following paths refer to user-provided immutable virtual snapshots and explicit research policies. Match IDs, dates, and policies to a single research scenario; consult contract documents for schemas and synthetic fixtures.

```bash
dsv decide --scope research --ticker 005930 --as-of '2024-01-08T16:00:00+09:00' \
  --analysis-id ANALYSIS_ID --analysis-db .data/analysis.sqlite3 \
  --decision-db .data/decisions.sqlite3 --policy research-policy.json \
  --account virtual-account.json --orders virtual-orders.json --market-context virtual-market.json

dsv decisions --decision-db .data/decisions.sqlite3 --scope research --ticker 005930
dsv decisions --decision-db .data/decisions.sqlite3 --scope research --decision-id DECISION_ID --replay
```

Successful research decisions remain virtual (exit 0), blocked ones return WAIT/blocked (exit 1), and malformed inputs/files return exit 2. The default decisions query scope is operational and must never be silently replaced with research results. Operational `decide` is blocked; every outcome has `operational_eligible/executable=false`. No real orders or account balances are changed. HOLD does not certify safety. When exit models do not apply in a falling context, diagnostics preserve this along with disabled risk rules.

An independent Decision SQLite store atomically persists inputs, policies and outputs. It does not modify market or analysis databases; replay uses the original bundle even if later files change or are deleted. Synthetic tests do not prove investment performance. Intraday daily bars, operational registration/revocation histories and real risk exits require later approval.

## Phase 4: Research performance reports

`dsv backtest --input` runs Runner → Ledger → Performance → Report using an explicitly configured policy and **frozen synthetic historical-research input**. It never automatically constructs real market data or models. See [backtesting performance contract](docs/backtesting-performance.md).

```bash
dsv backtest --input synthetic-request.json \
  --start 2026-01-05T00:00:00Z --end 2026-01-12T02:00:00Z \
  --initial-cash 10000 --tickers 005930 --format text
```

Tickers and cash must match the fixed input; timestamps require timezone offsets. Marking price, TTL and rounding policies must be explicit; insufficient marks result in null equity/returns. Open positions are not forcibly liquidated and outputs are **not** verified real PIT/out-of-sample investment results.

`dsv backtest-input` is a separate command to freeze actual stored daily bars into a research input, subject to explicit manifests, policy and snapshot limits documented in [historical backtesting](docs/historical-backtesting.md). Generated inputs alone remain `execution_status=blocked` until historical execution policy and evidence are validated. Synthetic fill policy is not automatically applied to real data.

The historical DB end-to-end path uses `dsv backtest --market-db ... --analysis-db ... --model-id ... --config ... --start YYYY-MM-DD --end YYYY-MM-DD --initial-cash ... --ticker 005930`. It requires explicit historical execution/valuation policies and a matching model snapshot; simulations are currently restricted to one ticker. See the [historical backtest E2E contract](docs/historical-backtest-e2e.md).

## Local observability

Run a standalone Docker Compose stack with Prometheus and Grafana OSS. It currently collects only Prometheus self-metrics and is **not** integrated with application, training, or DB telemetry. See [observability setup and cleanup](docs/observability.md).

## Read-only web API

An optional FastAPI transport is available behind the `api` extra. It serves a validated immutable research snapshot and does not run inference, signals, evidence collection, or backtests.

```bash
python -m pip install -e '.[api]'
dsv-api-export --root . --market-db .data/market.sqlite3 \
  --output .data/research-api-snapshot.json
dsv-api --snapshot .data/research-api-snapshot.json
```

The deterministic read-only exporter validates the market database, official evidence fingerprints, reviewed model-publication metadata and universe identity before creating a new snapshot. It does not overwrite an existing snapshot.

See [research API contracts](docs/research-api.md) for endpoints, exporting, and research integrity.

## Safety and operational readiness

Recent PIT inference-input, real-OHLCV feature-adapter, snapshot-provenance, source-revision-ledger and source-collection-evidence components are **validation contracts**, not certificates of historical truth or authority to run a production model. An apparently consistent SHA-256 chain does not establish who collected data or when they first possessed it. Unverified quality flags must not be cleared automatically. No unapproved research HGB model is promoted; `registration_authorized` and `inference_authorized` remain false without the required independent authority and evidence.
