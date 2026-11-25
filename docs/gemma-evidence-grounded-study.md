# Phase D controlled research: execution record

## Research intent and baseline

This study tests evidence validity, inference cost and candidate quality as
separate questions. It reuses the Phase B/C learner, original data owners,
Experiment Memory, exact-cache adoption and resource guards. It does not train
Gemma, introduce market collection, or activate a research scheduler.

Phase C completed 15 candidate evaluations: ten successful new fits, five exact
cache reuses, eleven Gemma inferences and two rejected proposals. Its 18 formal
comparisons remained insufficient. Those observations are development history,
not new independent evidence for Phase D.

## Frozen design

Execution code is commit `8739fc7cdcdc3b44c8f2bc4a4d2447bf17eff344`.
Two existing historical cohorts, H1/H5/H20 and five arms form 30 comparison cells.
Four evaluations per cell/arm were registered (120 slots). The separately approved
first window allows at most 10,800 seconds, 24 Gemma inferences and 30 dispatches,
including three calibration dispatches. Missing slots remain unavailable.

The arms are Rule, the original Phase C memory agent, Verified Evidence,
Compact Memory and Value Ranking. The latter three use the same strict alias
contract and 128-token output cap; the original Phase C arm keeps its 512-token
cap. The distinction is a declared intervention, not a same-token-budget claim.
All use the same source, candidate space, Python learner, folds, baseline, seed,
original event contracts and decision thresholds.

The host's existing bounded candidate projection is retained. First-round
allowed-view sizes are 3/8/8 for cohort 1 H1/H5/H20 and 8/7/6 for cohort 2. Agents
do not inspect unselected peer-cache performance. Cache adoption occurs only
after independently validated selection. No criteria, prompts or candidate
space are changed in response to window outcomes.

## Development smoke, separate from the comparison

One real local development inference was accepted before the study window.
Input/output token counts returned by Ollama were 1,012/90; observed load,
inference and unload durations were approximately 11.11/57.03/0.57 seconds.
Maximum sampled Ollama RSS was 2,489,792 KiB. Citation/parent mapping and unload
checks passed. No Python training or KRX request was made by this smoke.

The smoke used code `139dd8c`; subsequent changes preserve the response contract
and add explicit original execution lineage and full dispatch-cost reporting.
Its source/session and retained code wheel were separately matched after the
observation, recorded as local consistency rather than authenticated chronology.
It is not an ablation evaluation or evidence of generalization superiority.

## Execution results

All 30 dispatches and 24 study inferences completed within the approved window.
The final end was 5,969.89 seconds after start (99.5 minutes); summed dispatch
wall time was 5,560.21 seconds (92.7 minutes). There were 12 completed candidate
evaluations: eleven successful new fits and one exact cache adoption. Eighteen
Gemma proposals were rejected. No extra KRX request, paid LLM, scheduler change
or model promotion was performed. The development smoke is separate.

| Arm | Valid proposals | Rejected | Evaluations | New fits | Exact cache | Gemma calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 6 | 0 | 6 | 6 | 0 | 0 |
| phase_c | 1 | 5 | 1 | 1 | 0 | 6 |
| verified | 1 | 5 | 1 | 0 | 1 | 6 |
| compact | 2 | 4 | 2 | 2 | 0 | 6 |
| value_ranked | 2 | 4 | 2 | 2 | 0 | 6 |

The frozen 120-slot matrix still has 108 uncompleted evaluation slots. Every one
of the 60 pairwise cell comparisons is `INSUFFICIENT_EVIDENCE`; no cell reaches
four evaluations per arm. There is no demonstrated independent generalization
or overall candidate-quality superiority.

### Actual per-cell model results

Missing cells are **UNAVAILABLE**, not zero performance. The table includes
every completed model; each listed arm/cell has exactly one evaluation.

| Cohort | Horizon | Arm | AP | BA | Brier | Worst fold BA |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| historical-cohort1 | 1 | rule | 0.475709 | 0.509338 | 0.251143 | 0.478956 |
| historical-cohort1 | 5 | rule | 0.505342 | 0.508554 | 0.251132 | 0.475957 |
| historical-cohort1 | 5 | compact | 0.497101 | 0.510497 | 0.251285 | 0.476678 |
| historical-cohort1 | 20 | rule | 0.456667 | 0.550462 | 0.247127 | 0.541096 |
| historical-cohort2 | 1 | rule | 0.499914 | 0.498196 | 0.251988 | 0.478908 |
| historical-cohort2 | 1 | compact | 0.482789 | 0.484757 | 0.258667 | 0.476302 |
| historical-cohort2 | 1 | value_ranked | 0.493498 | 0.498961 | 0.253245 | 0.476074 |
| historical-cohort2 | 5 | rule | 0.533573 | 0.511879 | 0.251441 | 0.470781 |
| historical-cohort2 | 5 | phase_c | 0.534549 | 0.513897 | 0.255643 | 0.469884 |
| historical-cohort2 | 5 | verified | 0.533573 | 0.511879 | 0.251441 | 0.470781 |
| historical-cohort2 | 5 | value_ranked | 0.532068 | 0.517186 | 0.252425 | 0.486104 |
| historical-cohort2 | 20 | rule | 0.520657 | 0.522016 | 0.249742 | 0.497237 |

Compact H5 in cohort 1 had lower AP than Rule (0.497101 vs 0.505342), slightly
higher BA and slightly worse Brier. Compact H1 in cohort 2 was worse on AP, BA
and Brier. Value Ranking was below Rule AP in both completed cells and had
worse Brier. Phase C H5 in cohort 2 had a tiny AP increase but worse Brier.
Verified H5 in cohort 2 independently selected the Rule configuration and reused
its exact result. These are mixed/negative development observations, not
improvement or statistical performance approval.

### Observed cost and resource use

| Arm | Prompt bytes | Provider input tokens | Output tokens | Model inference seconds | Dispatch seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| rule | 0 | 0 | 0 | 0.00 | 452.61 |
| phase_c | 40938 | UNAVAILABLE | UNAVAILABLE | 1316.22 | 1775.32 |
| verified | 43369 | 23462 | 493 | 1112.30 | 1546.70 |
| compact | 16934 | 7584 | 536 | 403.59 | 857.12 |
| value_ranked | 18736 | 8677 | 750 | 473.18 | 928.46 |

For the six C/D requests, Compact used 61.0% fewer prompt bytes and 67.7% fewer
reported input tokens than Verified. Its total model inference time was 63.7%
lower. This is an observed software/protocol difference with only six requests
per arm, not proof of general model intelligence or quality improvement. Full
Evidence also encountered context-headroom problems; the cost comparison must
not hide its failures. Phase C aggregate token totals are unavailable because
one timeout did not return counts. Missing counts were not estimated.

Maximum sampled Ollama RSS was 4,875,660 KiB (about 4.65 GiB), Python worker RSS
357,900 KiB, and dispatch-parent RSS 763,816 KiB. Maximum sampled Ollama CPU
was 375.5%, within the existing 450% guard. CPU execution, one configured model
thread, 7 GiB model RSS limit and all existing power/thermal/disk guards remained
in force. All owned models were verified unloaded per completed proposal audit.
One Rule model passed the unchanged legacy internal criterion; no Gemma model
did. Cost per such Gemma pass is unavailable, not zero.

### Evidence quality and failure analysis

All five accepted grounded proposals had locally verified citation/parent
mapping. This selected subset is not a 100% accuracy claim across all attempts.
Phase C accepted 1/6 proposals; Verified 1/6; Compact 2/6; Value Ranking 2/6.
The grounded arms rejected eight invalid contrasts against the cited parent
configuration and five malformed JSON responses. A `memory_grounded_parent`
failure here can mean a false mask/family/parameter contrast; it does not mean
the alias resolved to a fabricated original ID. Unknown partial-validation
booleans remain null.

| Arm | Observed rejection reasons |
| --- | --- |
| phase_c | gemma_configuration_substitution: 1, llm_inference_timeout: 1, memory_parent_evidence: 3 |
| verified | memory_grounded_parent: 2, llm_proposal_rejected: 3 |
| compact | memory_grounded_parent: 4 |
| value_ranked | memory_grounded_parent: 2, llm_proposal_rejected: 2 |

The three malformed Verified responses reported input counts 4,015/4,016/4,015
and output counts 80/78/81 in a 4,096-token context. They could not reserve the
configured 128 output tokens. Two Value Ranking malformed responses reached
the 128-token output cap despite ample input headroom. These observations
separate context/output-contract limitations from intrinsic reasoning ability;
causality is not independently established.

After the frozen window, the provider gained a fail-closed guard against
observed input counts that cannot reserve the configured output plus one safety
token. Unknown counts stay unknown; no limit was increased. Boundary/failure
tests cover this change. It was not silently applied to the frozen study or
empirically re-run with extra inference. Study results and the retained code
bundle remain tied to commit `8739fc7`.

No duplicate-request or low-value rejection occurred in this one-round window.
Therefore duplicate/low-value gates are implemented and regression-tested but
their empirical benefit was not demonstrated. Evidence summaries retain parent
family/mask and aggregate metrics; full parent parameter profiles remain in
the catalog/host rather than the rendered summary. This presentation limit and
the bounded host view can affect counterfactual selection. A future preregistered
study should test explicit parent profiles/allowed contrasts and output-headroom
allocation before attributing failures to the model itself.

### Reproducibility and retained boundaries

The final report was reconstructed exactly with the retained study-code wheel,
and its implementation hash matched all six pinned sessions. Local preview of
the actual publication returned HTTP 200 for list/detail, 405 for writes and
403 for nonlocal requests. No external service was deployed. All 1,792 protected
files matched their original SHA-256 values after the window. Actual raw data,
pins, original prompts, code wheels, model checkpoints and full audit reports
remain in separate private storage.

Existing related regressions passed (20 checks), with subsequent focused
coverage for lineage/context/cache/provider changes. Strict Mypy, Ruff, builds
and installed-wheel checks were performed; no required CI policy was weakened.


## Interpretation boundaries

A correct citation is not a causal finding. A schema rejection is not a zero
model score. A valid hypothesis is not proof that its selected Python model is
better. Unequal completion counts cannot support an unqualified mean ranking.
The existing minimum four evaluations, twenty calendar anchors and ten examples
per class are unchanged. Correlated candidates and shared market dates do not
create independent samples; confidence intervals are unavailable under this
study's descriptive comparison policy.

All `pit_qualified`, `performance_approved`, promotion and investment execution
permissions remain false. Final Test, Q3 diagnostics, original data/PINs, Phase
B/C results, existing shadow records and LaunchAgent configuration remain
protected. Raw data, private prompts, models and custody/source PINs are not
included in this repository.
