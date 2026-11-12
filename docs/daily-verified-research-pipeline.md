# Daily verified KRX research pipeline

This integrates existing collection completions with score-only research. It does
not collect during offline analysis, train a replacement model, publish stock
recommendations, write a prospective prediction, or enable a scheduler. Private
policies, trusted pins, archives and operational SQLite remain source owners.

## Executable lineage and reused owners

1. Existing `krx_scheduler` / `krx_backlog` complete the original capture and atomic
   `operational_capture.ingest_capture`. Their state and verified-completion registry
   retain the original plan, policy, capture and batch references, including older
   policy references after renewal.
2. `daily_research.run_daily` reads those references through `_records` and reuses
   `verify_stored_capture`: original authorization at capture time, HTTP response,
   receipt, manifest, normalized batch and independently retained owner pins are
   checked. A summary date/count alone never permits analysis.
3. `OperationalMarketStore` opens read-only. Its existing validator checks the store;
   selected batches are checked again inside the same read transaction used to
   select source-linked OHLCV. Only requested tickers and verified batches enter
   the bounded input. No copying into another market DB or historical DB occurs.
4. `load_model` / `ReadOnlyAnalysisStore` load an explicitly selected content-addressed
   historical-research model with its original snapshot/dataset references.
   `feature_vector`, `context` and `LinearInference` reuse the existing eight-feature
   and conditional logistic scoring implementation. No fitting or pickle loading.
5. The existing immutable `AnalysisStore` saves `daily_research_job` and
   `daily_research_result` artifacts in an explicitly selected, separate research
   output file. This uses the existing artifact schema, not a new journal/database
   schema. A job freezes the day, ticker universe, model version, config pin,
   lookback, original source references, input hash and implementation hash.
6. `daily-research status` reads results back with content checks and a single SQLite
   read snapshot. It distinguishes collected/stored evidence verified at analysis
   time from completed, blocked, failed and pending ticker work. It reports input
   age/freshness and the last fully successful analysis day.

A historical training-snapshot replay through `SignalService.research` deliberately
requires that model's original snapshot. This integration does not change that
contract or pretend new observations belong to that snapshot. It uses the existing
score-only model API for a separate, explicitly research-only application.
`EvaluationService`, prospective prediction workflows and prediction journals are
unchanged. New-data scores are not evaluated predictions or calibrated probabilities.

## Supported offline run and recovery

Run after an existing authorized collection/ingestion has finished and original
completion references have been registered through the existing scheduler workflow.
The following variables refer to operator-reviewed existing paths/pins. Use a new,
private output path; never select the model source, market DB or protected analysis
DB as the output. Commands below are instructions, not operations performed by
this change.

```sh
dsv daily-research run \
  --config "$CONFIG" --config-pin "$CONFIG_PIN" \
  --model-db "$MODEL_SOURCE_DB" --model-version "$MODEL_VERSION" \
  --output-db "$RESEARCH_OUTPUT_DB" --day "$COMPLETED_TRADING_DATE" \
  --tickers 005930 --lookback-days 60

dsv daily-research status \
  --output-db "$RESEARCH_OUTPUT_DB" --day "$COMPLETED_TRADING_DATE"
```

`MODEL_VERSION` is the separately selected existing model artifact identifier, not
an inferred "latest" model. A compatible real-data historical-research logistic
model is required for real KRX source bytes. Models trained on synthetic data
cannot score real captures. Mock captures require both explicit
`--allow-synthetic-fixture` and a synthetic model; their reports permanently say
`synthetic_test_only`. Mixed real/mock source windows are rejected. No fallback to
fixtures, training, an unpinned model, or an old research database is provided.
The offline CLI does not load `.env` or consult credentials.

Limits: 1–100 explicitly selected numeric ticker codes; 1–90 calendar days of
history, default 60; existing 366-completion registry bound; at most 20 retained
attempts per ticker/job. Successful scores need at least 11 observed bars, the
requested anchor bar, no missing dates in the retained source policies' permitted
observation scope, and the existing quality checks. Zero-volume bars, suspected
gaps/discontinuities and unavailable/future inputs are blocked. Inputs older than
seven calendar days cannot start new scoring. A complete retained historical
analysis may still be read/replayed later; current age/freshness is reported
separately. Policy date allowlists do not authenticate a market calendar; unknown
sessions and corporate actions stay explicitly unverified.

The day/model/universe/source binding is immutable within an output file. Repeating
an identical successful job writes no artifacts or execution rows and does not
rescore successful tickers. Conflicting model, universe, source or code identities
for that day fail closed. A separately reviewed changed experiment needs an explicit
separate output file; do not overwrite the previous job.

The output has an exclusive nonblocking process lock. Each ticker artifact and its
existing AnalysisStore audit entry commit atomically. A crash during a ticker
transaction rolls it back; earlier committed tickers remain complete and the rest
are pending. Rerun the same `daily-research run` command after resolving the cause;
only blocked, failed or pending tickers run again. No automatic retry loop exists.
A source/model failure before job creation produces a nonzero CLI exit and no
apparently completed job. Analysis never deletes or retries a collection.

A successful complete analysis exits 0, incomplete research exits 1, and invalid
source/config/storage errors use the existing CLI exit 2. Local check/audit times
are operational timestamps, never authenticated existence/publication times.

## Optional post-collection hook and existing status API

The existing `krx-scheduler run-due` and `run-once` commands accept these optional
post-collection arguments, only with their already separately authorized execution:

```text
--research-output-db PATH
--research-model-db PATH
--research-model-version ID
--research-tickers 005930
--research-lookback-days 60
```

This does not install/activate a LaunchAgent, alter a scheduler config, or supply
network permission. The existing persistent consent, scope, quota and source guards
still govern collection. Without these options the original reports/exit behavior
are unchanged. With them, `krx_daily_pipeline_v1` contains the original `collection`
report unchanged plus separate per-day `research` reports. Only successful collection
days are selected, then originals are independently replayed by `run_daily`.
Collection failure is not relabelled an analysis failure, and analysis failure does
not relabel a successful collection or cause another request. The scheduler command's
exit code continues to describe collection; inspect `research` separately and use
the offline run command for analysis recovery. Supplying partial post-step arguments
is rejected before collection. No installed job or existing config is updated here.

The existing `GET /api/v1/operations/krx-scheduler/diagnostics` can be extended by
explicitly injecting `daily_research_report` into `create_app`, for example a callback
returning `daily_status(output, day, current_utc).to_dict()`. It adds `dailyResearch`
with collection/storage status, analysis state/counts, input age, model version,
last successful day and the prediction blocker. Without that callback the original
v1 response is unchanged. The callback must remain read-only; no dashboard or
background analysis task is introduced. Status is retained evidence as checked at
analysis, not a claim of fresh archive verification or provider readiness.

## Evidence and authorization limits

Keep original archives, authorization packages, separately retained policy/config/
plan/capture/batch pins, scheduler completion references, model source artifacts,
and research output. Removing originals prevents a new run even when old research
results remain readable. A missing completion can only be restored through the
existing original-source registration procedure, not by inventing a date/count.

Cross-file source replay and operational/model reads are not one atomic snapshot.
The selected OHLCV and its pinned stored batches share an SQLite read transaction;
operators must keep original archive/policy/model evidence quiescent during the run.
The existing source verifier is invoked once per completion per run before
selecting all requested tickers, rather than once per ticker; its internal replay
checks remain unchanged. Raw HTTP bodies are not retained in a new cache. This is local
integrity, not an authenticated custodian, provider signature or chronology service.

All reports keep `future_prediction_recording_available`, genuine-forward, PIT,
performance, signal, PAPER and execution approval false; performance metrics are
null. Existing prospective workflows still require `offline_fixture` and do not
admit provider-backed v3 operational captures. No prediction/outcome journal is
written and no new-data performance comparison is claimed. Actual provider-backed
prospective recording, independently authenticated chronology/custody, approved
models/publication and later verified outcomes remain separate evidence dependencies.
The existing operational policy's signal/PAPER prohibitions are preserved.

## Offline verification

```sh
pytest -q tests/integration/test_daily_research.py
pytest -q tests/integration/test_krx_scheduler.py tests/integration/test_krx_backlog.py \
  tests/integration/test_operational_capture.py tests/integration/test_operational_market_storage.py \
  tests/learning tests/integration/test_prospective_prediction_workflow.py \
  tests/api/test_krx_scheduler_diagnostics.py tests/storage/test_krx_operations_diagnostics.py
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

The new tests execute original MockTransport capture, ingestion, receipt/batch replay,
existing model training/scoring and result readback, with real sockets forbidden.
They cover idempotence, insufficient/missing/stale inputs, failure and interruption
recovery, original-byte damage, policy/batch substitution, unsupported versions,
model damage, conflicting jobs, source/output alias refusal, credentials-free CLI
and the existing diagnostics API. Synthetic fixture results never demonstrate actual
KRX acquisition, production model readiness, external authenticity or performance.

## Real-market model source binding

Real daily models additionally need the frozen original training source binding
from [real-market model readiness](real-market-model-readiness.md). The generic
Phase 2 model loader is unchanged; an unlinked existing real model is blocked by
the daily consumer rather than silently promoted. This bridge does not train on
operational SQLite, unseal the original Final Test, or change any collection policy.
