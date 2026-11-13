# Frozen real KRX model diagnostics

`real-model-evaluation` is a read-only consumer of PR #231's frozen real dataset
and safe JSON model. It does not train, calibrate, select thresholds, register
models, request market data, or change any existing approval. Existing Phase 2
metric/report bytes and identifiers remain unchanged.

## Existing owners reused

The consumer uses `prepare_dataset` for original source/receipt/PIN replay and
exact frozen snapshot/manifest comparison. Original Training and Validation roles
are preserved; the sealed outer Final Test is neither selected nor replayed.
`load_model`, `validate_real_daily_model` and read-only `EvaluationService` verify
the original model, split and independently regenerated dataset/metrics.
`baseline_scores`, the existing metric implementation, `score`, feature engine,
labels and chronological purge contracts are reused. No new model or database
schema is introduced.

Additional Accuracy, Balanced Accuracy, F1 and tied-rank ROC-AUC live in a separate
diagnostic module. PR-AUC means the existing noninterpolated Average Precision,
not trapezoidal PR area. Empty/single-class AP and ROC-AUC stay null with reasons;
undefined precision/F1/recall are explicit. Binary scores are uncalibrated
conditional reversal-barrier research scores, **not price rise/fall probabilities**.
The positive fraction means the in-context five-observation barrier-event rate,
not unconditional next-day share-price direction.

## Fixed comparisons and grouping

Prior and reversal-strength are the existing Phase 2 baselines. The additional
majority predictor is fixed using the original Training prior; it is not selected
using diagnostic outcomes and is not a new approval standard. Training is
resubstitution, Validation was used for model selection, and the post-Final-Test
window is explicitly retrospective development diagnosis. An AP margin comparison
on this window grants no independent holdout, prospective or performance approval.

The report contains pooled Training/Validation/diagnostic metrics; per-ticker
metrics, FP/FN feature associations, observed feature mean/std changes; every
month/quarter, both directions, pooled and per ticker. Empty groups are retained.
Existing real-data holdout minimums annotate limited interpretation without
suppressing descriptive metrics. Labels may mature in the next month: temporal
grouping is by anchor, within the unchanged diagnostic split. This is not a
monthly refit or a new monthly qualification test. Overlapping labels and small
buckets are not independent evidence of significance.

Causal past feature history may cross Training into Validation under the original
contract. Labels may not cross their partition boundary. Protected and known
missing-session gaps reset feature/label eligibility. October 2 stays missing;
no holiday classification, imputation or synthetic substitute is introduced.

Input-history hashes, label boundaries and forbidden dates are checked again.
The trace counts locally retained inputs and model creation after nominal
historical anchors. Local collected/created/evaluated timestamps are not signed
chronology or publication authority. Corporate actions, historical revisions,
verified calendar continuity and PIT remain unresolved. No verified market-index
series is bound to this dataset, so market regimes are not invented. Feature
shifts and score changes are descriptive associations, not proven causes.

## Offline operator procedure

Create a separate private request and independent canonical-JSON request PIN.
The request version is `real_model_evaluation_request_v1`; fields are:

- `dataset_request`, `dataset_request_pin_file`: exact original PR #231 references;
- `model_database`, `model_database_pin`, `model_version`: frozen model owners;
- `source_pins_root`: original separately retained source/manifest/snapshot PINs;
- `evaluated_at`: actual local execution reference, not independent chronology.

No credential is loaded. Original sources must still be quiescent and match their
pinned versions (or be restored to separately retained exact copies). Appended
market history changes a whole-file PIN; do not repin/relax the old owner to force
replay. Reads use transactions and before/after hashes, not a multi-file atomic
snapshot claim. All existing source-size/response bounds stay in effect.

```bash
dsv real-model-evaluation run \
  --request "$EVALUATION_REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --output "$NEW_EVALUATION_DIRECTORY" --pins-root "$NEW_EVALUATION_PINS"

dsv real-model-evaluation verify \
  --request "$EVALUATION_REQUEST" --request-pin-file "$REQUEST_PIN_FILE" \
  --output "$EVALUATION_DIRECTORY" --pins-root "$EVALUATION_PINS"
```

New private outputs are exclusive. Retain all six files:
`evaluation-summary.json`, `evaluation-manifest.json`, `per-ticker-metrics.csv`,
`temporal-metrics.csv`, `confusion-matrices.json`, `evaluation-report.md`.
The manifest binds source/model/dataset identities, parameter digest, original
and diagnostic splits, code/runtime provenance, baseline identities and exact
output-byte SHA-256 values. Its PIN is retained separately. Model hash means
canonical AnalysisStore model JSON; the entire model DB is pinned separately.

Two deterministic diagnoses must match before export, and written bytes are read
back exactly. `verify` reconstructs sources and files and rejects tampered output
or PINs. A squash merge may change the Git revision; verification preserves the
recorded revision only if implementation bytes and dependency versions match.
An interrupted export can leave a partial new directory without a completed
manifest/PIN pair; preserve it and use a separate new output, never overwrite it.
Original source/model stores are always opened read-only.

## Validation

```bash
pytest -q tests/learning/test_diagnostics.py \
  tests/integration/test_real_model_evaluation.py tests/learning/test_metric_integrity.py \
  tests/integration/test_real_market_dataset.py tests/architecture
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Metric tests compare tied scores against the existing learning dependency's
independent metrics. Integration tests use explicit KRX-schema fixtures with
poisoned protected originals, real cryptographic/source validators, blocked network
and no fitting during evaluation. Actual operator data is assessed separately.
`performance_approved`, `pit_qualified`, independent publication authentication and
execution authorization remain false. Approval-level performance metrics stay
null; diagnostic measurements remain available separately. No operational model
registration, automatic prediction publication or trading is enabled.
