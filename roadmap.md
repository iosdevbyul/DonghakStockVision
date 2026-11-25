# DonghakStockVision — official prediction-performance roadmap

User-approved direction, 2026-11-25. This is the active roadmap; the earlier root checklist is historical and is preserved below. Existing Phase A–D documents are historical contracts/results, not active next-step instructions. Read [AGENTS.md](AGENTS.md) for mandatory scope and evidence rules.

## Current checkpoint

- #252 merged: Phase C 15 evaluations, 10 new fits, 5 exact cache adoptions, 11 Gemma calls, 2 rejected proposals; 18 formal comparisons insufficient.
- #253 Phase D: study completed, PR pending CI repair at this checkpoint; 30 dispatches, 24 Gemma inferences, 99.5 minutes, 12 evaluations (11 new fits, 1 cache), 18 rejected proposals. Compact vs Verified: 67.7% fewer reported input tokens, 63.7% lower inference time; model quality advantage not established; all 60 comparisons insufficient, 108 evaluation slots incomplete. Do not automatically execute them. [Execution record](docs/gemma-evidence-grounded-study.md).
- Next core work: **R1 Prediction Performance Audit**, only after #253 is confirmed merged and latest main inspected. R1 is not started by this CI repair. No Phase E Gemma-only detour.
- Existing assets: #240–245 actual KRX research including 72 historical fits across six study configurations; 46 causal features, 7 masks, 14 profiles, H1/H5/H20; Logistic Regression, Random Forest, HistGradientBoosting; Experiment Memory, exact cache, #246–247 Research Lab API, #250 prospective Shadow, #251–253 research agents. #253 assets remain conditional on merge. Check actual current local coverage before use.

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
| Per-ticker prediction/actual/correctness and overall accuracy | R1 must inspect; unknown, never invent |
| Repeated model improvement and independent future generalization | Not established |
| #250 Shadow as of that study | 8 legacy pending, new H1/H5/H20 predictions/evaluations 0; recheck current local state |
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
