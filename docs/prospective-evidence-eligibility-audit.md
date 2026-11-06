# Read-only prospective evidence eligibility audit

## Scope and trust boundary

`prospective_evidence_eligibility_audit_v1` is an evidence-structure inspection,
not a performance evaluator. The audit accepts original journal/capture owners
and separately retained pins, reconstructs each binding through the existing
`prediction_outcome_binding_v1` implementation, and projects its result into four
states. It neither accepts status summaries nor creates another evidence store.
It is independent of the unmerged prospective evaluation policy work.

| Audit status | Reason and meaning |
| --- | --- |
| `eligible` | `verified_offline_pair_structure_only`: original prediction and future capture replayed successfully under the frozen window. |
| `pending` | Original binding's reason, such as `frozen_evaluation_gate_not_elapsed`; future capture is not loaded. |
| `unavailable` | Original binding's reason, such as `future_observation_missing`, `outcome_deadline_elapsed` or `observation_precedes_frozen_gate`. |
| `invalid` | Original binding's integrity, identity or scope reason; no pair is ready. |

Eligibility is **structure readiness only**. All current eligible pairs are
synthetic/offline, not genuine prospective market evidence. Every report retains
`genuine_forward_evidence=false`, `prospective_performance_approved=false`,
`pit_qualified=false` and `execution_authorized=false`; each case also retains
`prospective_evaluation_eligible=false`. A policy or local timestamp cannot
establish independent original prediction creation time. No accuracy, return,
baseline, confidence interval, economic result or investment approval is emitted.
The existing binding owns its original directional rule; the audit only projects
verification status and the binding identifier, without exposing outcome metrics.

## Inputs and deterministic behavior

The Python API accepts a tuple of 1–32 `EvidenceCase` values. Each contains the
existing `PredictionSource` and optionally `FutureSource`; it does not accept a
caller-supplied binding artifact or status flag. Duplicate prediction IDs reject
the complete request before source replay, preventing double counting. Cases and
original requests are bounded before any replay: 256 KiB per request, 2 MiB in
aggregate. Reports are limited to 256 KiB. Existing journal, capture and parser
limits remain authoritative. These are cooperative bounds, not process isolation.

Counts include all four states, including zeros. Reason counts use stable reason
codes and canonical serialization. Case order follows input order. The denominator
is `total_supplied_cases`, not a predeclared evaluation cohort; this is not coverage
approval. Missing and invalid cases remain explicit, never zero outcomes.

With fixed owners and `as_of`, repeated/restarted audits return identical JSON and
content identifiers. `verify_audit(report, trusted_pin, cases, as_of)` checks the
separately retained report pin and independently reconstructs the audit from the
original owners. A self-rehashed altered report fails semantic verification.
Source pins must come from original independently retained references, not solely
from the report under verification. Local hashes do not authenticate publication.

## CLI

Use the read-only module command:

```sh
python -m donghak_stock_vision.prospective_evidence_audit_cli \
  --input /absolute/private/audit-input.json \
  --input-pin "$AUDIT_INPUT_PIN" \
  --as-of 2025-07-12T01:00:03+00:00
```

The illustrative time is synthetic. Use an explicit evaluation instant for the
actual retained offline scenario. Instants must be timezone-aware; output is UTC.
The command does not fetch provider data or write a journal, source database,
receipt, manifest, report or pin. It prints canonical JSON. Invalid cases produce
JSON and exit 2; pending/unavailable-only audits exit 0. Malformed/budget-exceeding
input prints `audit_input_invalid` to stderr and exits 2, without private paths.

Input wire shape (replace references with original owners, not fabricated values):

```json
{
  "version": "prospective_evidence_audit_input_v1",
  "cases": [{
    "prediction": {
      "journal": "/absolute/private/original-journal.sqlite3",
      "journal_head": "<original trusted head>",
      "entry_pin": "<original entry pin>",
      "prediction_id": "<original prediction ID>",
      "capture_root": "/absolute/private/original-captures",
      "request": {},
      "request_pin": "<original request pin>",
      "policy_pin": "<original prediction policy pin>"
    },
    "future": null
  }]
}
```

`request` must contain the complete **original** frozen signal request, not the
empty illustrative object. For a future owner, replace null with exactly
`{"capture_root": "/absolute/private/future-captures", "request": {},
"request_pin": "<original future request pin>"}`, again using the complete
original request. Paths must be absolute; unknown fields, including supplied
eligibility flags, are rejected. The input file is bounded to 2 MiB and symlinks
are rejected. References are an audit request, not a new evidence registry.

Use existing canonical serialization to retain its pin separately:

```python
from pathlib import Path
from donghak_stock_vision.backtest.data import FrozenJSON

owner = FrozenJSON(Path("/absolute/private/audit-input.json").read_text())
# Retain owner.identifier separately as AUDIT_INPUT_PIN before use.
```

Original v1 journal entries remain readable by their existing reader; without
predeclared v2 prediction evidence they are `invalid` for this audit. No legacy
entry is rewritten or retroactively promoted. Historical/PIT, OOS, final-test and
holdout guards remain in the original source-verification path before capture
loading. Real-provider prospective evidence and dependent evaluation-policy
integration remain outside this PR.
