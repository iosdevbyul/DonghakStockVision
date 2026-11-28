# H1 matched-mask diagnostic, OOF design and R5 fixed ensembles

## H1 Hist15 Legacy matched-mask result

The approved single-slot run completed on 2026-11-29 KST. Hist15 Legacy found four more correct directions than Hist15 Weekly on the same 680 development rows. It reduced false positives by four, had the same number of true positives, and had a higher Brier score. Neither Hist15 result qualified. RF Legacy remained the control, and LR Weekly stayed the strongest of these four models by accuracy, BA and AP.

| H1 model | Correct/incorrect | Accuracy | BA | AP | Brier | FPR | FDR | Up recall | Down recall | TP/FP/TN/FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RF Legacy | 327/353 | .48088 | .48054 | .50654 | .24973 | 48.10% | 52.55% | 44.21% | 51.90% | 149/165/178/188 |
| LR C=10 Weekly | 367/313 | .53971 | .54049 | .56098 | .24868 | 54.81% | 47.00% | 62.91% | 45.19% | 212/188/155/125 |
| Hist15 Weekly | 334/346 | .49118 | .49160 | .48688 | .26393 | 55.69% | 51.21% | 54.01% | 44.31% | 182/191/152/155 |
| Hist15 Legacy | 338/342 | .49706 | .49743 | .49400 | .26579 | 54.52% | 50.68% | 54.01% | 45.48% | 182/187/156/155 |

Weekly alone was correct on 166 rows, Legacy alone on 170, both on 168 and neither on 176. The stronger Brier of the Weekly mask and the stronger classification of the Legacy mask on this exposed sample leave no unqualified mask preference. The two nonoverlapping time blocks had 16 and 17 anchors against the minimum 20. Fourteen rows spanning the block boundary were eligible for full-fold metrics and excluded from block metrics.

| Group | Rows | Hist15 Weekly correct | Hist15 Legacy correct |
| --- | ---: | ---: | ---: |
| 025530 | 86 | 37 | 44 |
| 036530 | 74 | 30 | 33 |
| 097950 | 79 | 44 | 39 |
| 105560 | 85 | 44 | 37 |
| 112610 | 91 | 49 | 47 |
| 138930 | 82 | 35 | 39 |
| 2023-08 | 140 | 60 | 71 |
| 2023-09 | 136 | 69 | 61 |
| 2023-10 | 137 | 72 | 71 |
| 2023-11 | 156 | 78 | 77 |
| 2023-12 | 111 | 55 | 58 |
| 241590 | 92 | 47 | 52 |
| 402340 | 91 | 48 | 47 |
| down_trend | 148 | 70 | 72 |
| high_volatility | 126 | 63 | 68 |
| low_volatility | 103 | 45 | 44 |
| normal_volatility | 451 | 226 | 226 |
| normal_volume | 632 | 310 | 312 |
| sideways | 378 | 189 | 185 |
| time_block_1 | 306 | 144 | 145 |
| time_block_2 | 360 | 183 | 188 |
| up_trend | 154 | 75 | 81 |
| volume_surge | 48 | 24 | 26 |

The 16 training tickers contributed 4,245 examples from 2022-01-03 to 2023-06-30. Eight held-out development tickers contributed 680 eligible examples from 2023-08-01 to 2023-12-28. Training and evaluation population digests, all sample IDs, labels, reference and target prices, causal feature contract, threshold .5 and neutral band 20 bps match the Weekly reference. The only configuration difference was the mask. Both used HistGradientBoosting value 15, learning rate .1, L2 .5, 160 iterations and seed 42. The retained datasets are the original R3/R6 sources, bound by Source, Dataset and Plan PINs.

| Retained identity | ID |
| --- | --- |
| Legacy catalog candidate | `a768a08bdecc941086b799a86c581e3e0dcff1c2264e001965d4a1c173ee4254` |
| Legacy Experiment | `ea999a13c6185365046cf3fad991c1f02a64e79f0a1cec509a9d119babd8ce27` |
| Legacy Model/checkpoint | `af9b49055db56760601340bf977395b42867a7effc166945a2b2cf2d0deb2649` |
| Weekly Experiment | `e740938f85d1529a6fa6e89444cc25202e0d81a2568ec57207e658c6f81f2d6f` |
| Weekly Model/checkpoint | `dee62a6c450fa9caa9d8b63d194437abea0671d1e97f66b9150ae9d5dc796c10` |
| Legacy Run | `8c28e93a4848391c82d249605b281c41bea7c710f747ea7ba79b09b95ca8d1f3` |
| Legacy Plan | `bf5a414b10c2ea9afa5f4c184499bb957ff7e70b533c1bea6a4e19f78e89ea19` |
| Actual execution revision | `2ca15da65b775613869b94605839d24c5419c37a` |
| R2 feedback | `884c46b73ade394f7bf52f40c83bdc68911646dea4dbc472b457108973a7f887` |

The execution used the separately archived source revision with the same numerical package versions as the #262 wheel, and the same fitting, feature, prediction and evaluation code. The authorized source inventory had no exact retained candidate. The completed ledger contains one new fit, one development evaluation, zero cache reuses, zero failures and zero recoveries. Fit took 7.75 s, the worker took 28.25 s, and the supervisor took 28.57 s of 90 s. The peak worker RSS was 230,396 KiB with one thread. The remaining 61.43 s carries no remaining evaluation slot. The report, store, code archive, paired readback and checkpoint hashes are bound by the private frozen manifest. The existing R7 evidence projector read the new run in isolation.

The opt-in single H1 diagnostic pins one prior completed experiment and only permits an exact configuration match apart from the mask. The worker compares the actual train and evaluation population hashes to the prior experiment and control before fitting. A finished slot cannot be reused to start a new candidate. A signed exact checkpoint from a checkpoint-only pause remains eligible for existing recovery. The previous two-slot and general supervisor plans retain their original contracts.

## OOF registration design

The retained R3/R4/R6 development predictions support paired comparisons and descriptive ensemble evaluation. R3 retains 34 development evaluations, R4 six, and R6 retains four pilot, twelve prior approved, two #262 diagnostic and one matched-mask evaluation. These 59 model evaluations store 40,464 model–row scores. They represent repeated use of development outcomes, with no retained proof of per-sample held-out fold assignment, per-fold training membership, purge/embargo, or actual at-input prediction availability. **Zero retained rows qualify as strict OOF meta-training input.** Protected fixed panels were excluded from this inspection. Some base models fit on distinct historical tickers and dates from their development rows. That individual split does not establish the fold-level contract or remove repeated selection bias.

Register future research with `oof_research_plan_v1` before constructing folds. The plan is a canonical manifest with a separately retained PIN. It references the existing AnalysisStore, model and prediction owners.

| Record | Required binding |
| --- | --- |
| Study plan | Code and package provenance, Source/Dataset/Plan PINs, at-registration time, eligible dates/tickers, excluded periods, candidate configurations, horizons, label/feature contracts, fold and embargo rules, budgets, approval and sealed outputs |
| Base configuration/fold model | Candidate ID, fitted Experiment ID, Model ID and checkpoint SHA, fold ID, seed, implementation, fit start/end, source receipt and availability clocks, training sample and ticker digests, maximum outcome target and label availability in the fit |
| OOF score | Run and fold ID, Experiment/Model ID, Source/Dataset/Plan PINs, sample ID, ticker, horizon, input/reference/target sessions, feature/label schema, pinned input cutoff and input availability, fit and generation timestamps, upward score, exposure kind and outcome receipt reference |
| Meta arm | Sorted base role order, fixed rule or meta candidate and seed, meta-training sample digest, train cutoff, outer holdout digest, checkpoint and Model ID where fitted, configuration hash and freeze timestamp |
| Outcome and rejection | Original price sources, observation and receipt timestamps, label or exclusion reason, and a link to the prior immutable OOF score. Keep missing/error/pending distinct from a valid score or neutral outcome. |

A score is unique within a run for the tuple of horizon, ticker, input/reference/target sessions, sample and base role. Compare only identical population digests and feature/label schemas. Join on those keys and require one complete vector per eligible row. Missing member scores exclude the vector with a recorded reason. Repeated attempts cannot replace a frozen score. A base checkpoint must exclude the validation sample and must prove its chronological training subset. A coherent out-of-time simulation with reconstructed availability is marked separately from strict OOF evidence and independent prospective evidence.

Use an expanding Purged Walk-Forward with symbol partitions pinned ahead of time. Reject training label intervals that intersect validation intervals across tickers. Require training target close and its availability to precede the fold's cutoff, purge the closed input-to-target intervals, and leave an embargo after a holdout before later folds can admit neighbouring rows. If sharing fits or cutoff schedules across H1/H5/H20, apply the union of each sample's closed outcome intervals and the longest applicable embargo. Never use a shorter horizon's partial label when a longer sibling interval crosses the boundary. Validate actual input availability before generation and freeze score rows before the target can be observed. An archived, late-received KRX feature can support labelled historical simulation with a nominal clock when approved. It cannot establish actual historical availability or PIT.

Freeze the base configurations before score generation. Use early OOF folds for estimating stacking coefficients and any predeclared error-correlation selection statistic. Keep the final time and ticker holdout sealed from tuning, including weight choice and threshold. Compare plain calibrated or uncalibrated scores only under the same stated output contract. The first study can compare the RF reference, fixed weighted mean, one regularized LR stack, and a predeclared correlation-based model selection rule trained on meta-training folds. Retain prespecified failed arms and run the same evaluation gate for every arm. A separate future R5 validation would require a distinct registration and independent sessions with no outcome access at prediction time.

### Proposed budget for a separately approved study

Start with two fixed base configurations and one horizon. Use three chronological meta-training folds and one later sealed holdout, with base fits scoped separately per fold. Start only after a no-fit feasibility check proves each fold's population, chronology, minimum 20 disjoint anchors and both class minima, as well as existing study-level row and panel gates. Pin the meta-holdout tickers separately from meta training. A practical first ceiling is eight base fits, one LR meta fit, four base fold-scoring passes per configuration, and one sealed comparison of the prespecified arms per horizon. Fixed weights and the correlation-selection rule use the shared OOF rows and add zero fits. Use one thread and 1 GiB RSS, a 60 s worker guard and a proposed 300 s supervised window per separately approved horizon. On the current machine the H1 Hist15 worker took 28.25 s. A 300 s window is a ceiling, and a future study must stop if its measured feasibility or resource checks fail.

H1 is the initial feasibility target. H5 would add up to nine fits and one comparison in a separate approval only if eligible cohorts meet the same minima. Four H5 folds with 20 disjoint input-through-outcome anchors need on the order of 560 sessions before extra history, purge and embargo. The original 2022–2023 train window alone cannot meet that bound. H20 is outside this proposal. This work spent zero fits on OOF or meta-learning.

## R5 fixed ensemble registration

The versioned `direction_shadow_registration_request_v4` uses the existing three-horizon list, with exactly one fixed ensemble standing in for one horizon and legacy single-model references for the other two. A fixed ensemble has two or three positive integer weights in basis points totalling 10,000. Component order is canonical by original Experiment ID. Each `direction_fixed_ensemble_v1` member binds an `owner` of `r3` or `r6`, retained `run_id`, original `experiment_id`, private `root`, `database_sha256` and `plan_pin_file`. Membership must be completed with a verified checkpoint, supported upward-score feature contract, matching horizon, identical training and evaluation populations and the same Dataset/Source owners. The protected intervals must agree. Completed records and checksums come from existing read-only owners. Runtime compatibility and checkpoint constructors use the existing allowlisted verifier.

The immutable score configuration hashes the weighting rule, label/feature contract, member Experiment and Model/checkpoint identities, weights and lineage. The Ensemble ID is derived from the version and configuration hash independently of order and registration time. The registration document pins that identity and its actual timestamp. Use one independently retained private PIN directory for this registration series, and pass the exact `<ensemble_id>.pin` filename to the existing CLI. A second registration of that score configuration in the same custody directory is rejected atomically. Re-reading independently revalidates the member stores, checkpoints, configuration and timestamp. It also rejects a scored input on or before a component's maximum exposed date.

The existing direction score dispatch evaluates each component with its original mask and checkpoint, then applies the pinned mean and the existing .5 threshold. Existing Shadow audit, replay validation and publication use the Ensemble ID where they previously used the experiment ID and the configuration hash where they used the model SHA. Member details stay in the private registration and audit. Existing v3 documents and event envelopes retain their versions and identities. No stored live journal or publication was changed.

The offline v4 registration used H5 RF Legacy from R3 and LR C=10 Legacy from #262 at 5,000 basis points each. Ensemble ID `259cccb79b224d0bc535bada882bd39e61ad63286b19ee8589966cb2cdcd0545` and configuration SHA `527d2e4342e336d6effdb095b92cd59108d0f9d0b197584f136242f300fd5136` revalidated. The separately retained 720 development scores recombined to 399 correct, matching the earlier diagnostic. The offline publication contains zero forecasts. No R5 future score was generated, recorded or resolved. No KRX requests, Gemma calls, protected outcome reads, scheduler changes or model promotions occurred. PIT and Performance Approval remain false.
