# Experimental skill study

Opt-in only. Use a new private root named skill-experiment-*, a new pin directory,
and a verified memory_guided_session_v1 request using the existing real KRX
source and matching historical memory. The existing prepare function verifies
source ownership and creates separate study directories. Do not point at an
existing root. The gemma_memory arm is the baseline and the otherwise unused
gemma_blind slot is explicitly repurposed for skills in this experiment only.
Do not use ordinary memory-guided reports to interpret that slot.

Run from this branch with the existing Python environment:

    python -m donghak_stock_vision.experimental_skill_study --request REQUEST.json --root /absolute/skill-experiment-01 --pins /absolute/new-pins

The host validates candidate indices, enforces an inference budget of three
per selection and delegates actual training to the existing bounded runner.
Dataset skill reports scope metadata only; evaluation is internal history,
not independent confirmation. Model release is required before training.
The comparison reports internal AP, worst balanced accuracy, completed
experiments, and inference/training costs. No final test or promotion.
Actual real-data results require the local pinned KRX source and Ollama.
