# DonghakStockVision — repository instructions

## Non-negotiable objective

Build a research system that improves **verifiable stock-direction prediction** through actual KRX data, model fitting, prediction/outcome comparison, failure analysis and subsequent experiments. Improvement is an empirical question, never guaranteed. Code volume, PR count, test count, reports and LLM proposal acceptance are not research success.

The user-approved development direction of 2026-11-25 supersedes earlier future-work plans. Read [roadmap.md](roadmap.md) before any task. Historical runbooks, Phase A–D studies and legacy files are retained evidence, not permission to resume or change scope.

## Scope and roles

- ChatGPT manages research direction, PR/CI review, Notion and Trello. Codex implements scoped code, actual research, tests and PRs; **do not access or modify Notion or Trello**. Use the repository roadmap and the supplied task instead. Only the user can approve a roadmap/goal change.
- Before coding, identify the active R-stage, research question, existing assets, permitted changes, prohibited changes, measurable completion criteria and resource/approval limits. Inspect current main and existing implementation first. If a discovered problem does not block the active goal, record it for later; do not silently expand scope. A necessary CI repair is not a new research stage.
- Python models and automated search are the primary research path. Gemma is optional support for failure analysis, hypotheses and prioritization. Python research must not depend on every Gemma response succeeding. In controlled comparisons, preserve rejected arms as rejected; never disguise Rule fallback as a Gemma success.
- Use already authorized historical data even when new KRX ingestion is zero. Reuse existing collectors, datasets, feature engineering, learners, stores, exact cache, evaluation, Shadow and Research Lab APIs. No parallel framework for functionality already present.
- Priority guide: model research 40%, outcome verification/failure-driven improvement 30%, Gemma support 15%, quality 10%, documentation 5%. Required checks and safety boundaries are not optional.

## Evidence and research boundaries

- Preserve original raw databases, checkpoints, immutable journals, Source/Dataset/Plan PINs, existing results, sealed Final Test, Q3 restrictions, liblinear quarantine, time/ticker splits and leakage controls. Never treat repeated exposed folds as independent evidence or replay as prospective prediction.
- Keep exact original IDs, label definitions, horizon, threshold, sample population and baseline. Distinguish missing/neutral/excluded/pending/invalid, actual fits/cache reuse/replays, and historical/independent future evidence. Never invent metrics, chronology, approvals or outcomes.
- Do not weaken existing evaluation minima, validators, architecture rules, debt allowlists, CI or resource guards to pass a check. No unauthorized KRX requests, paid/remote LLMs, external deployment, model promotion, trading, 24/7 activation, scheduler changes or stopping other projects' Ollama workloads. Long research windows require separate explicit approval. PIT and performance approval remain false until independently qualified.
- Frozen study execution code and artifacts are immutable. Code changes after a study do not retroactively change its results; preserve retained execution bundles and lineage.

## Development and delivery

- Work on a task branch, never commit directly to main. Keep one cohesive feature PR; repair confirmed CI failures on its existing branch. Do not create tiny follow-up PRs or repeat full regression runs unnecessarily.
- Run focused tests while implementing. **Run the existing architecture suite (`python -m pytest tests/architecture -q`) before push when changing Python dependencies/imports.** Run relevant regressions, Ruff lint/format, strict Mypy, build and appropriate integration checks once before PR. Keep required Python 3.12/3.13/3.14 CI intact. Report checks actually executed, not assumed.
- ChatGPT direction review precedes enabling protected squash auto-merge for future PRs; do not bypass required checks or review. Existing PR #253's auto-merge setting is unchanged by this policy. CI green does not establish research success.
- Final report: stage/goal, changed files, actual data and model IDs, fit/cache/replay counts, predictions and actual outcomes, correct/incorrect and denominators, accuracy/AP/BA/Brier and baseline comparisons, failure patterns, before/after results, cost and evidence limits as applicable. State blocked/unavailable items, test status, PR URL/head, and next stage without claiming unverified completion.
- Source of truth: GitHub for code and PR/CI state, [roadmap.md](roadmap.md) for repository direction, Notion/Trello for ChatGPT-managed current stage and operational tracking. Do not independently change these policies.
