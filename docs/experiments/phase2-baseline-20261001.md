# Phase 2 real KOSPI baseline — 2026-10-01

## Outcome

Actual fitting completed using the approved real dataset and unchanged production TrainingService. Both directions are `baseline_not_beaten`: their validation AP improvements do not reach the required 0.02 margin. This is a completed research baseline, not validation qualification or permission to trade. No final-test-driven retuning occurred.

## Repository and execution

- Branch: `feat/phase2-training`; base: `62c939171e0ec5c251e3af6286ba260e051ac446`.
- Python: repository `.venv/bin/python` (3.12.14); current repository source imports verified.
- No production code, dependencies, observability settings or schema changes.
- Execution: public `capture(...)` → `AnalysisStore.put("snapshot", ...)` → `TrainingService.train(snapshot_id, split_configuration=...)`.
- The local driver subclasses TrainingService only to time calls to `super()._direction`; it overrides no fitting, selection, metric or persistence calculation.
- Existing CLI automatic splitting was not used. No separate EvaluationService evaluation was run after training, so saved final-test metrics were read without recomputing them.
- The service processes independent directions in order: Up fit/validation/freeze/test, then Down fit/validation/freeze/test. Selection rules were fixed before either direction; each selected model is frozen before its own test. There is no joint two-model locking transaction. No test result enters either selection path.

## Dataset and split

- Source: `.data/market.sqlite3`; 993 tickers, 808,449 bars, 2022-01-03–2025-06-30.
- All bars: `krx / KOSPI / unadjusted`; duplicate keys 0, integrity/malformed issues 0.
- ResearchCalendarPolicy excludes 2024-03-28 and 2025-03-21 for every ticker; these are incomplete trading dates, not holidays. Exactly 2 stored Samsung bars are removed from research input, leaving 808,447 bars. Source rows remain untouched.
- Snapshot cutoff: 2026-09-30T00:00:00+00:00, matching the readiness audit.

| Partition | Inclusive Seoul dates | Research bars | Labeled rows after purge |
|---|---|---:|---:|
| Training | 2022-01-03–2023-12-28 | 463,699 | 417,411 |
| Validation | 2024-01-02–2024-06-28 | 114,392 | 101,087 |
| Final Test | 2024-07-01–2025-06-30 | 230,356 | 207,625 |

- Purged: Training 4,358; Validation 4,365; Final Test 0 labeled boundary violations. Another 4,482 tail samples have immature labels and never enter evaluated populations.
- Purge retains Training `label_end < validation.start`, Validation `label_end < final_test.start`, Final Test `label_end <= final_test.end`.
- 988 tickers have valid feature samples across 841 dates. Five have no eligible feature samples: 007630, 012600, 015350, 015540, 101060. No manual ticker selection was performed.

| Direction | Partition | Eligible | Positive | Negative | Dates | Tickers |
|---|---|---:|---:|---:|---:|---:|
| up | train | 222,675 | 76,286 | 146,389 | 476 | 965 |
| up | validation | 55,081 | 17,683 | 37,398 | 115 | 945 |
| up | test | 107,855 | 36,659 | 71,196 | 235 | 952 |
| down | train | 194,736 | 66,170 | 128,566 | 476 | 964 |
| down | validation | 46,006 | 14,620 | 31,386 | 115 | 943 |
| down | test | 99,770 | 31,388 | 68,382 | 235 | 952 |

All production minimums passed. Quality exclusions remain unchanged:

```json
{
  "ambiguous": 5235,
  "immature_label": 4482,
  "insufficient_history": 9930,
  "no_context": 15220,
  "no_trade_bar": 16650,
  "suspected_discontinuity": 22083,
  "constant_volatility": 1
}
```

## Fixed protocol and selection

- Eight features, `ohlcv_value_v1`: `return_1`, `return_3`, `return_5`, `close_to_sma10`, `volatility_10`, `range_to_close`, `volume_change_10`, `trading_value_change_10`.
- `reversal_barrier_v1`, H=5, width=max(0.02, 2 × sample standard deviation of last 10 log returns); `statistics.stdev` uses n−1.
- Up only return_5 < 0 (prior_decline); Down only return_5 > 0 (prior_rise). Outside context remains null, never a fabricated negative label or zero score.
- Weighted StandardScaler + LogisticRegression; lbfgs, L2, max_iter=2000, balanced training classes; overlap/date sample weights from training only.
- Seed 42; numeric threads 1. C candidates 0.1, 1.0, 10.0; choose highest validation AP, exact ties lower C. Threshold fixed at 0.5; no threshold optimization.
- Both selected C=10.0. No fit on validation/test and no refit after selection.
- Final Test was evaluated once per selected direction plus the existing fixed baselines. Test metrics were not used to select anything.

| Direction | C | Validation AP |
|---|---:|---:|
| up | 0.1 | 0.352272195832 |
| up | 1.0 | 0.352272210004 |
| up | 10.0 | 0.352272267332 |
| down | 0.1 | 0.353692487640 |
| down | 1.0 | 0.353702795594 |
| down | 10.0 | 0.353704561183 |

## Validation results

| Direction | Precision | Recall | F1* | AP | Brier | TN / FP / FN / TP |
|---|---:|---:|---:|---:|---:|---|
| up | 0.346884 | 0.744105 | 0.473182 | 0.352272 | 0.252761 | 12,624 / 24,774 / 4,525 / 13,158 |
| down | 0.356686 | 0.720520 | 0.477159 | 0.353705 | 0.249946 | 12,387 / 18,999 / 4,086 / 10,534 |

| Direction | Prior AP | Reversal-strength AP | Model AP minus strongest baseline |
|---|---:|---:|---:|
| up | 0.321036 | 0.352241 | +0.000031 |
| down | 0.317785 | 0.344023 | +0.009681 |

## Final Test results

| Direction | Precision | Recall | F1* | AP | Brier | TN / FP / FN / TP |
|---|---:|---:|---:|---:|---:|---|
| up | 0.376250 | 0.674241 | 0.482980 | 0.383461 | 0.247475 | 30,220 / 40,976 / 11,942 / 24,717 |
| down | 0.391104 | 0.683796 | 0.497600 | 0.393070 | 0.236933 | 34,967 / 33,415 / 9,925 / 21,463 |

| Direction | Prior AP | Reversal-strength AP | Model AP minus strongest baseline |
|---|---:|---:|---:|
| up | 0.339892 | 0.388405 | -0.004944 |
| down | 0.314604 | 0.343322 | +0.049748 |

*F1 is derived only from the stored confusion matrix. ROC-AUC is not part of the existing report and was not added. AP is noninterpolated Average Precision, not trapezoidal PR-AUC. Calibration remains none; scores are not validated rise/fall probabilities.

No configuration was changed after Final Test inspection. The stronger Down test AP does not override failed validation qualification. Up/Down scores are independent conditional outputs, not complementary probabilities or a combined ranking.

## Timing and artifacts

Total service duration: 1621.374 seconds (dataset construction, fitting, evaluation and persistence).

| Direction | Start (Asia/Seoul) | End (Asia/Seoul) | Seconds |
|---|---|---|---:|
| up | 2026-10-01T00:34:16+09:00 | 2026-10-01T00:41:17+09:00 | 420.477 |
| down | 2026-10-01T00:41:17+09:00 | 2026-10-01T00:47:07+09:00 | 350.473 |

Direction durations include all three candidate fits, validation selection and final evaluation; isolated estimator.fit durations were not instrumented.

Artifacts are persisted together under the existing `.data/analysis.sqlite3` contract; one model bundle contains both independent directions.

- Snapshot: `f1c5fac2ac5865d57dfc96501ab6927b41e1deff6360e194e582e7b1508363c1`
- Dataset: `aa2f1eb0ef044c44cba4cd82b4e6706fdbb0f9022b365b092beb2c09a4cef02f`
- Model: `35539448e57d12f81c85494f71c62b8d2f00ce80d633031664d9ec6a104a9e40`
- Explicit split configuration hash: `6d4fa516227f13640cdb785f029359134d51b074468be543654c8873f9296150`
- Calendar policy hash: `77949e2385b91c340d54996451220f5f4d2d0f5c16dfcd561aaf3e00b14874a6`
- Protocol hash: `d7e3dbc521b1e81aa141c09835b989c1c8d8868bfd1801a19e401810c4789610`

Local audit directory (Git ignored): `.data/phase2-training-20261001/`. It contains the preflight and one-shot fitting drivers, commands, locked protocol, population report, full model JSON, summarized metrics, timestamps and validation logs. `fit.py` refuses a repeated run when training-started.json already exists. Do not remove the guard to tune against this already-inspected test period.

```json
{
  "git_sha": "62c939171e0ec5c251e3af6286ba260e051ac446",
  "implementation_hash": "9341d804e4fbb2236818bffe794b9f2ad481d9e4ab7672bf15988f21ca3825c7",
  "numeric_tolerance": 1e-10,
  "seed": 42,
  "threads": 1,
  "versions": {
    "donghak-stock-vision": "2.0.0a3",
    "joblib": "1.6.0",
    "numpy": "2.5.3",
    "scikit-learn": "1.9.1",
    "scipy": "1.18.1",
    "threadpoolctl": "3.7.0"
  }
}
```

## Integrity, validation and limits

Market DB SHA-256 before and after: `4cf4c19638eacb80ca6a215f0def827258f1d61c327f83d928f37327fc5a2a3e`.
- Analysis DB intentionally adds snapshot/dataset/model artifacts. Existing artifacts and registry are preserved; no schema migration.
- Existing offline suite: 692 passed, 1 live test deselected. Ruff check, Ruff format check, mypy and git diff --check passed.
- No production Phase 3 run, backtest, KRX call, trading, or observability modification. Existing regression tests use their own fixtures.
- This is retrospective historical research: revision history, corporate actions, exact exchange calendar and investable universe are not fully verified. It is not verified PIT/OOS evidence or a profitability claim.
- The research exclusion quality flag is preserved; no Phase 3 allow-list change or operational registration was made.
- No commit, push, PR or merge was performed. Only this report is a tracked-file candidate; DBs and local logs remain ignored.
