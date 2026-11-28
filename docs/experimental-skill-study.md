# Experimental skill study

Opt-in only. Use a new private root named skill-experiment-*, a new pin directory,
and a verified memory_guided_session_v1 request using the existing real KRX
source and matching historical memory. The existing prepare function verifies
source ownership and creates separate study directories. Do not point at an
existing root. The gemma_memory arm is the baseline and the otherwise unused
gemma_blind slot is explicitly repurposed for skills in this experiment only.
Do not use ordinary memory-guided reports to interpret that slot.

Run from this branch with the existing Python environment:

    python -m donghak_stock_vision.experimental_skill_study_cli --request REQUEST.json --root /absolute/skill-experiment-01 --pins /absolute/new-pins

The host validates candidate indices, enforces an inference budget of three
per selection and delegates actual training to the existing bounded runner.
Dataset skill reports scope metadata only; evaluation is internal history,
not independent confirmation. Model release is required before training.
The comparison reports internal AP, worst balanced accuracy, completed
experiments, and inference/training costs. No final test or promotion.
Actual real-data results require the local pinned KRX source and Ollama.

## Bounded correction and audit

Each selection has at most three provider generations, including malformed or
invalid proposals. The final generation must select a host-approved candidate.
Provider/resource failures stop immediately rather than retrying. Private audits
retain bounded responses, valid/invalid action events, observations and error
messages. Provider inference counts and peak RSS use provider metadata; the
legacy inference_seconds field remains total proposal wall time. The new field
provider_inference_seconds is actual provider inference time. Never publish
private tool events. New sessions only; do not resume or overwrite old arms.

## Grounded hypotheses

The active agent requires a memory observation before selecting a candidate.
The model selects the observation alias, measured metric, and a configuration
change. The host verifies the metric and changed field and binds the exact
reference ID/value; it no longer counts every memory read as a citation.
The host defines the direction of improvement by metric, not by model prose.
The next proposal sees prior hypotheses and observed internal metric deltas.
This is hypothesis feedback, not causal proof or LLM weight training. Multiple
configuration changes remain visible. Unmeasured outcomes are not successes.

Grounded mode uses separate memory and hypothesis schemas. A model-selected
memory is the fixed reference for that proposal; the model selects the candidate
and measured metric next. No evidence sentinel or second reference index is
needed. All configuration changes are recorded, with multiple changes marked
explicitly. The three-generation cap includes corrections.

## Adaptive strategy experiment

Each grounded hypothesis now records both configurations and all four reference
metrics. Feedback includes observed metric deltas and a separate predictive
quality classification. A drop greater than 0.002 in any of AP, worst BA or
baseline AP gain is a regression; improvement requires at least one increase
above 0.002 and no such regression. These are development heuristics, not
statistical significance or independent validation.

Within a fresh session the host accumulates measured transition outcomes and
alternates exploration and exploitation. Exploitation restricts choices only
when an observed transition score separates available candidates; exploration
prefers previously unseen transitions when available. Gemma still chooses the
reference, permitted candidate and metric. Decisions and scores are audited.
An optional --grounded-control disables candidate restriction while retaining
the same grounded feedback. The V1 budget remains at most four per arm. A
baseline failure does not establish performance superiority. Cross-session
strategy carryover, independent evaluation and continuous execution are not
implemented by this change.
