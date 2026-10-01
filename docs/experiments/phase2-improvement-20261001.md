# Phase 2 Model Improvement Experiment — 2026-10-01

## Result and limits

Both locked candidates passed this experiment’s Validation AP improvement gate. They remain research_only / unregistered. This does not establish calibrated probabilities, profitability, PIT/OOS validity, or production inference compatibility. No Final Test data, targets, predictions or metrics were queried or computed in this experiment. No previous Final Test report/model artifact was loaded.

## Predeclared protocol

Base main: `cf9809361b975dfa8a2c3e56b3bb926c67d83343`; branch: `feat/phase2-model-improvement`.
- Source database unchanged: 993 tickers, 808,449 bars, 2022-01-03–2025-06-30, krx/KOSPI/unadjusted. Whole-file SHA and aggregate identity metadata are checked; this does not expose final-test targets or compute final-test features.
- Research exclusions remain 2024-03-28 and 2025-03-21; not exchange holidays.
- Development: 2022-01-03–2023-12-28. Qualification Validation: 2024-01-02–2024-06-28.
- 2024-07-01–2025-06-30 remains sealed. Source read callbacks reject any range later than the currently permitted end. No old full-range model/dataset artifact is loaded.
- ohlcv_value_v1: the unchanged eight existing features. No feature search.
- reversal_barrier_v1, H=5, max(0.02, 2 × last-10-log-return sample standard deviation), unchanged.
- Up: return_5 < 0; Down: return_5 > 0. Outside-context samples are not negative labels. No combined/complementary score or null-to-zero conversion.
- Threshold 0.5, seed 42 and numeric thread limit 1 throughout.

| Candidate | Configuration |
|---|---|
| linear_c01 / linear_c1 / linear_c10 | Existing weighted StandardScaler + LogisticRegression; C=0.1/1/10, lbfgs, L2, max_iter=2000, balanced classes |
| hist_leaves7 | HistGradientBoostingClassifier, max_leaf_nodes=7 |
| hist_leaves15 | HistGradientBoostingClassifier, max_leaf_nodes=15 |

HGB common settings: learning_rate=0.05, max_iter=150, min_samples_leaf=100, l2_regularization=1.0, max_bins=255, class_weight=balanced, **early_stopping=False, random_state=42**. Existing overlap/date sample weights are calculated from each fold’s training population. No internal random validation split, new dependency or new features were introduced. HGB is available in the existing scikit-learn learning extra; XGBoost/LightGBM/CatBoost were not added.

## Training-only evaluation

| Fold | Expanding training | Evaluation |
|---|---|---|
| 1 | 2022-01-03–2023-03-31 | 2023-04-01–2023-06-30 |
| 2 | 2022-01-03–2023-06-30 | 2023-07-01–2023-09-30 |
| 3 | 2022-01-03–2023-09-30 | 2023-10-01–2023-12-28 |

Calendar endpoints are inclusive; no weekend/holiday bars are generated. Training labels must end before fold evaluation.start. Evaluation labels must end by evaluation.end. The final development boundary is 2023-12-28.

Internal fold guards require at least 252 training dates, 40 evaluation dates, 200 rows and both classes ≥20. These are internal-fold guards, not a relaxation of the full production dataset requirements. The actual full Training populations exactly match the approved baseline (Up 222,675 / Down 194,736). Qualification retains 200 rows / 63 dates / both classes ≥20.

Five configurations per direction × three folds = 30 internal fits, then two full-Training selected fits. The predeclared selection criterion is **mean fold AP − population standard deviation of fold AP**, followed by mean AP and fixed candidate order for exact ties. This fold variability calculation is separate from the unchanged sample standard deviation in the label contract.

| Direction | Candidate | Fold 1 AP | Fold 2 AP | Fold 3 AP | Mean AP | Std AP | Selection score |
|---|---|---:|---:|---:|---:|---:|---:|
| up | linear_c01 | 0.398860 | 0.367852 | 0.422480 | 0.396397 | 0.022370 | 0.374028 |
| up | linear_c1 | 0.398876 | 0.367860 | 0.422482 | 0.396406 | 0.022367 | 0.374039 |
| up | linear_c10 | 0.398878 | 0.367861 | 0.422482 | 0.396407 | 0.022367 | 0.374040 |
| up | hist_leaves7 | 0.424908 | 0.362544 | 0.448101 | 0.411851 | 0.036128 | 0.375723 |
| up | hist_leaves15 | 0.420796 | 0.363532 | 0.447182 | 0.410503 | 0.034917 | 0.375586 |
| down | linear_c01 | 0.409622 | 0.439786 | 0.317306 | 0.388905 | 0.052104 | 0.336801 |
| down | linear_c1 | 0.408701 | 0.439778 | 0.316746 | 0.388408 | 0.052237 | 0.336171 |
| down | linear_c10 | 0.408426 | 0.439778 | 0.316718 | 0.388307 | 0.052214 | 0.336093 |
| down | hist_leaves7 | 0.449785 | 0.488372 | 0.361815 | 0.433324 | 0.052962 | 0.380362 |
| down | hist_leaves15 | 0.446042 | 0.485468 | 0.363832 | 0.431781 | 0.050672 | 0.381109 |

Up selected hist_leaves7; Down selected hist_leaves15. Up’s improvement is not uniform: its second fold AP is below the linear candidates. Down’s selected HGB exceeds the linear candidates across all three folds. Fold variation and repeated reuse of a known Validation period limit confidence; passing the AP gate is not a statistical significance claim.

Full fold Precision/Recall/F1/Brier, class counts and purge counts are in the immutable experiment_internal artifact and local internal-results.json.

## Lock then one Validation gate

- Both selected candidates were fitted on their respective full Training populations before either Validation read.
- Their configuration and estimator fingerprints were persisted together as experiment_lock.
- qualify_locked verifies both fingerprints, consumes a validation-start record, then invokes the bounded Validation loader. A repeated call for that lock is rejected, including after a failed attempt.
- The actual script is a single-process research execution; this is not a distributed concurrency/transaction service.
- Existing TrainingService.train/EvaluationService were deliberately not invoked because the former also evaluates Final Test.
- Qualification references are the user-approved six-decimal **Logistic Regression Validation AP** values. They are not the weaker prior/reversal-strength baselines and were not obtained by loading an old artifact containing final-test results.

| Direction | Model | Validation AP | Approved baseline AP | Improvement | Gate |
|---|---|---:|---:|---:|---|
| up | hist_leaves7 | 0.374859667 | 0.352272 | +0.022587667 | validation_gate_passed |
| down | hist_leaves15 | 0.398381565 | 0.353705 | +0.044676565 | validation_gate_passed |

Gate: candidate AP − approved Logistic Regression AP ≥ 0.02. No model, threshold, feature, exclusion, split, or selection rule was changed after this gate.

| Direction | Rows | Positive / negative | Precision | Recall | F1 | Brier |
|---|---:|---|---:|---:|---:|---:|
| up | 55,081 | 17,683 / 37,398 | 0.348164 | 0.828423 | 0.490277 | 0.264718 |
| down | 46,006 | 14,620 / 31,386 | 0.355472 | 0.863953 | 0.503699 | 0.269693 |

Confusion matrices (TN / FP / FN / TP):

- up: 9,972 / 27,426 / 3,034 / 14,649.
- down: 8,484 / 22,902 / 1,989 / 12,631.

AP is the existing noninterpolated Average Precision. F1 is derived from its confusion matrix. Calibration remains none. Precision at threshold 0.5 is about 0.35 and false positives are numerous; ranking AP success is not an assurance of probability calibration or trading safety. No calibration/threshold adjustment was made.

## Artifacts and reproducibility

Uses existing AnalysisStore content-addressed JSON kinds in a separate local experiment DB: `.data/phase2-improvement-20261001/analysis.sqlite3`. Existing `.data/analysis.sqlite3` was not opened. No schema or existing production model codec change.
- Lock ID: `5d1d04e9607926ed5b89c70b71a36d4852f43d73723fab70e895db4830bb821d`
- Internal report ID: `cafc58def53ab2ed75d15ab69a18b2ad4aff7493be0ec883d41ec92f09f83440`
- Training snapshot ID: `2ab6202fde7a867d4602a7473aa0e79b6a6340d6524730c785cffc8e0bc9635f`
- Validation snapshot hash: `9ff50484434f6780ec69c6bab892d7b75a0567778d4b724c0e6e6c9d78271b7b`
- Qualification report ID: `164748197f6616057040d4f8ae2044ff35b5471d29b2d96512a2efa680ec17ed`
- Market SHA-256: `4cf4c19638eacb80ca6a215f0def827258f1d61c327f83d928f37327fc5a2a3e`

Local run driver: `.data/phase2-improvement-20261001/run.py`. A started.json guard refuses automatic repeat runs. plan.json was written before Training-only data generation; lock.json before the first Validation capture. audit.json verifies DB checksums, checkpoint hashes and event order.

Local `up-research-only.pkl` and `down-research-only.pkl` retain the version-bound fitted research objects. They are fingerprinted in the lock; **no pickle load API or production SignalService support is added**. Do not load untrusted pickle files. These are not operational model registrations or drop-in replacements for the existing safe JSON linear-model format. A production inference adapter would require separate work.

Execution command (already run once; do not rerun against this gate to tune):

```bash
LOKY_MAX_CPU_COUNT=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
  .data/phase2-improvement-20261001/run.py
```

```json
{
  "versions": {
    "scikit-learn": "1.9.1",
    "numpy": "2.5.3",
    "scipy": "1.18.1",
    "joblib": "1.6.0",
    "threadpoolctl": "3.7.0",
    "donghak-stock-vision": "2.0.0a3"
  },
  "git_sha": "cf9809361b975dfa8a2c3e56b3bb926c67d83343",
  "implementation_hash": "0c81f8e6ab294142cf2ee2abb0199e41872b1ef954382f3eed99e51ed50eda49",
  "seed": 42,
  "threads": 1,
  "numeric_tolerance": 1e-10
}
```

## Verification and scope

- New experiment tests: 6 passed. Full offline tests: 698 passed, 1 live test deselected.
- Ruff, formatting check, mypy, git diff --check passed.
- sdist/wheel build passed with declared hatchling in build isolation. A no-isolation attempt initially failed because hatchling was absent from the application venv; no modeling dependency was added or upgraded.
- Fresh venv wheel install/import/contract smoke passed. That smoke did not refit production models or rerun Validation.
- Source market checksum unchanged; no KRX calls or data edits. No Final Test scoring, Phase 3 run, backtest, observability change, operational registration or real trading.
- Unadjusted latest-known historical prices, retrospective revisions, corporate-action/calendar uncertainty, conditional populations and incomplete investment-universe/survivorship evidence remain limitations. This is historical research, not strict PIT/OOS evidence.
- No commit, push, PR or merge. Local artifacts remain Git-ignored.
