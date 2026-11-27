# DonghakStockVision — official prediction-performance roadmap

User-approved direction, 2026-11-25. This is the active roadmap; the earlier root checklist is historical and is preserved below. Existing Phase A–D documents are historical contracts/results, not active next-step instructions. Read [AGENTS.md](AGENTS.md) for mandatory scope and evidence rules.

## Current checkpoint

- #252 merged: Phase C 15 evaluations, 10 new fits, 5 exact cache adoptions, 11 Gemma calls, 2 rejected proposals; 18 formal comparisons insufficient.
- #253 merged. Phase D completed; 30 dispatches, 24 Gemma inferences, 99.5 minutes, 12 evaluations (11 new fits, 1 cache), 18 rejected proposals. Compact vs Verified: 67.7% fewer reported input tokens, 63.7% lower inference time; model quality advantage not established; all 60 comparisons insufficient, 108 evaluation slots incomplete. Do not automatically execute them. [Execution record](docs/gemma-evidence-grounded-study.md).
- #254 merged after the three required Python checks. **R1 Prediction Performance Audit remains PARTIAL**. It linked 2,657 retained model–row records to actual prices across 34 checkpoints. Original development-fold scores and independent future performance remain unavailable. [R1 audit](docs/prediction-performance-audit.md).
- The user approved **R2 Prediction Failure Feedback**. The internal H5 follow-up fitted five models and two calibrators, evaluated 914 rows and preserved the feedback. The calibration hypothesis was rejected. The Daily/Legacy comparison remains insufficient under the volatility-group anchor criteria. [R2 results](docs/prediction-failure-feedback.md).
- **R3 Predictive Model Research remains PARTIAL.** A bounded internal study completed 34 trials (31 fits, 3 exact R2 reuses), on 8/16/24-stock training panels across H1/H5/H20. No selected model demonstrated a consistent advantage over the matched Legacy controls. [R3 results](docs/predictive-model-research.md).
- **R4 Autonomous Research Strategy remains PARTIAL.** B and C retained Legacy under insufficient block evidence. In the exposed H1 fixed cohorts, Legacy made 22 and 4 more correct predictions than A’s retained R3 challenger. H5 selected Legacy across A/B/C. Six distinct configurations were fitted, none met the improvement criteria, and the one local Gemma inference was rejected on timeout. [R4 results](docs/autonomous-research-strategy.md).
- **R5 Independent Future Validation is PARTIAL and first recording is blocked.** The R4 H1/H5 Legacy and descriptive R3 H20 checkpoints were registered. The 2026-11-27 readiness check used recovered 2026-11-20 input (33/35 stored sessions); the H1/H5 cutoff had passed and the H20 calendar was incomplete. New forecasts, outcomes and independently qualified anchors were all zero. [R5 validation](docs/independent-future-validation.md).
- **R6 Supervised Long-running Research is PARTIAL.** Three bounded pilot windows completed four fits and evaluations, one exact checkpoint resume and one operator stop. Both follow-up candidate IDs changed with new results; no candidate passed stability. PR #260 merged. The separately approved 240-second window completed 12 new fits and 12 evaluations in 190.25 seconds, leaving 49.75 seconds. Later results changed 9 of 10 eligible choices. All 12 retained Legacy under existing stability gates. [R6 pilot and diagnosis](docs/supervised-long-running-research.md).
- **KRX recovery:** The single authorized KOSPI capture on 2026-11-27 recovered the 2026-11-20 session (HTTP 200, 935 rows). The verified store held 30,981 rows across 33 sessions, with zero duplicate ticker–date keys. The recovery authorization expired. R5 remains blocked.
- Existing assets: #240–245 actual KRX research including 72 historical fits across six study configurations; 46 causal features, 7 masks, 14 profiles, H1/H5/H20; Logistic Regression, Random Forest, HistGradientBoosting; Experiment Memory, exact cache, #246–247 Research Lab API, #250 prospective Shadow, #251–253 research agents. Check actual current local coverage before use.

## Fixed stages and completion gates

| Stage | Work | Completion evidence |
| --- | --- | --- |
| R1 — Prediction Performance Audit | Reuse existing checkpoints, evaluation and actual stored predictions/outcomes; permitted historical replay only when necessary. | Trace real ticker/model/date/price/direction/score/outcome/correctness, exclusions, denominators and failures; comparable baseline and metrics; next testable hypotheses. |
| R2 — Prediction Failure Feedback | Connect ticker/period/regime/class/feature failure patterns to hypotheses and subsequent actual fitting. | Original evidence IDs → hypothesis → new fit → same-contract before/after evaluation; improvement, regression or unclear result recorded. |
| R3 — Predictive Model Research | Study price/volume/volatility/day-week-month/regime features, feature selection, parameters, models and justified ensembles. | Actual repeated fits and preregistered comparisons with baselines, error change, cost and limitations. |
| R4 — Autonomous Research Strategy | Python-led search with optional Gemma hypotheses; reuse Phase D validated catalog/compact evidence. | Compare research yield, model quality and cost under matched budgets; keep rejected arms intact. |
| R5 — Independent Future Validation (may accumulate in parallel) | Reuse #250 H1/H5/H20 Shadow and authorized observation resolution. | Pre-outcome immutable predictions linked to real later prices, complete denominator, existing independence/sample gates; insufficient remains pending/blocked. |
| R6 — Supervised Long-running Research | Reuse locks, guards, recovery and daily runner; separately approved finite supervision. | Actual stability/recovery and meaningful research output; unattended 24/7 separately approved. |
| R7 — Research Lab Visibility | Reuse existing read APIs and separate TrisMarketLens clients. | Actual predictions, outcomes, failures, improvement history and comparable denominators visible; backend and UI E2E verified separately. |

Stages cannot be changed without explicit user approval. R5 can accumulate in parallel within existing authorization; newly authorized operations are not implicit. Minimal visibility changes necessary to R1–R3 are allowed, but do not rebuild the UI or framework.

## R1 detailed task boundary

1. Inspect current main, existing AnalysisStore, experiments, retained models, evaluators, Shadow and publication paths. Identify actual available and unavailable evidence; do not assume all local artifacts exist.
2. Reuse stored per-row predictions and outcomes first. If necessary replay only approved development folds with original model, features, label, cutoff and threshold. Respect sealed Final Test and protected Q3 restrictions. Label as Historical Replay, never new prospective evidence. Train only if diagnosis requires it and authorization covers it.
3. Preserve the actual delayed-direction contract, including input date, reference session/price, target observation, horizon and original neutral band (e.g. 20 bps where the contract defines it). Never replace the existing label with an arbitrary return-sign rule.
4. For H1/H5/H20 and each comparable cohort, provide actual model/ticker IDs, prediction and realized direction/return, correct/incorrect, denominator and exclusion reasons, accuracy, BA, AP, Brier, class counts, naive baselines, confusion matrix, calendar anchors and ticker/period/regime failure examples. Label evidence exposure and distinguish unavailable from zero.
5. Derive testable R2 hypotheses grounded in the observed failures; no speculative causal claims. Reuse existing reporting or minimal read-only extensions. No new collector, DB, learner, large audit framework, Gemma prompt-only project, five-arm rerun or automatic advancement.
6. Completion requires actual traceable data/results, not merely a new CLI or passing tests. Record exact blockers and partial real evidence if full diagnosis is impossible.

## Research scorecard

| Observation | Current evidence |
| --- | --- |
| Existing historical research | 72 experiments across six studies; no independent superiority established |
| Phase B (#251) | H1 6 evaluations (4 new fits, 2 cache), pilot separate; Memory AP .481286 vs Rule .473657, Brier worse |
| Phase C (#252) | 15 evaluations / 10 fits / 5 cache; 18 insufficient comparisons |
| Phase D (#253) | 12 evaluations / 11 fits / 1 cache, 6/24 accepted Gemma proposals; 60 insufficient comparisons |
| Per-ticker prediction and actual outcome | R1 retained 2,657 model–row outcomes (1,159 correct, 1,498 incorrect). R2 retained 914 matched H5 rows across two cohort populations and 23 nonoverlapping dates each. |
| H5 failure feedback | Intercept calibration increased FPR by 1.36 and 27.37 percentage points. The Daily mask increased high-volatility error rates by 2.11 and 1.99 points, with insufficient group anchors for a final comparison. |
| R3 matched model comparison | H5 24-stock Legacy changed fixed accuracy by +0.72 and −1.10 percentage points across the two cohorts. H1 development gains did not transfer. H20 fixed comparison had 7 nonoverlapping anchors per cohort. |
| R4 strategy comparison | H1 stability abstention avoided 22 and 4 net errors against A across the two previously exposed cohorts. Development minima were unmet for both H1 time blocks and all H5 blocks. Six new fits, 0 qualifying improvements, and 1 Gemma inference timeout. |
| R6 supervised pilot | 4 new fits, 4 development evaluations, 1 exact checkpoint resume, 2 changed follow-up selections, 0 qualifying improvements. 65.28s across three supervised windows, maximum RSS 273,912 KiB. |
| R6 approved execution (#260) | 12 fits and evaluations, 0 exact cache, 9/10 later-result choice changes, 0 qualified improvements, 190.25s wall (49.75s remaining). All 12 retained Legacy. |
| KRX one-shot recovery | 2026-11-20 KOSPI session recovered on 2026-11-27, +935 rows (32 → 33 sessions, 30,046 → 30,981 rows). |
| Repeated model improvement and independent future generalization | Not established |
| #250 Shadow and R5 initial validation | R5 verified 30,046 stored operational rows across 32 sessions through 2026-11-19. Three research checkpoints registered. Eight separate legacy pending and 10 historical replays (5 evaluated, 5 pending). New forecasts, outcomes and independently qualified anchors were zero. |
| Phase D inference cost | Compact 403.59s vs Verified 1112.30s across six calls each; not quality superiority |

Update scorecard after actual research with comparable model/data/contract identifiers, counts, correctness, metrics/baselines, failures, subsequent hypotheses, cost and independence status. Do not track only best scores. GitHub PR/CI and study outcomes are separate.

## Management references (ChatGPT only)

- Notion: https://app.notion.com/p/41125c4cccdd816aa464cce29f3320ab
- Trello: https://trello.com/b/1fvZhcgk/donghak-vision
- R1 card: https://trello.com/c/ocscXBS5

Codex uses this repository file and task instructions, not direct Notion/Trello access.

## Historical root checklist (superseded; not an active plan)

```text
앞으로 할 내용 정리

✔ 데이터 수집

✔ Dataset 생성

✔ RandomForest Baseline

✔ Hyperparameter Tuning

✔ Feature Engineering #1
    거래량비율
    MA20
    MA60
    MA120
    RSI
    HIGH20
    Volatility20
    Momentum20
    HIGH252

────────────────────────────

□ Feature Engineering #2  
    ✔ 데이터 수집
    ✔ 백테스트
    ✔ Feature Engineering
    ✔ RandomForest
    ✔ Hyperparameter Tuning

□ XGBoost

□ LightGBM

□ Ensemble

□ 실제 투자 백테스트

□ 종목 추천 시스템

□ 웹 대시보드
```
