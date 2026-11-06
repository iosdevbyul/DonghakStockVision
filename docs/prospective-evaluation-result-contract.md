# Prospective evaluation result contract and integrity validator

## Implemented boundary

`storage.prospective_evaluation_result` defines the strict, bounded
`prospective_evaluation_result_v1` transport contract. It validates declarations,
reference consistency and retained content pins. It does **not** replay prediction
or outcome evidence, validate evaluation policies, perform an eligibility audit,
calculate performance, or approve genuine prospective evidence. It is independent
of the unmerged policy and evidence-audit modules.

The public API is deliberately small:

```python
from donghak_stock_vision.storage.prospective_evaluation_result import (
    validate_structure, verify_integrity,
)

# result and references are original FrozenJSON values, not untrusted status flags.
validate_structure(result)
verify_integrity(result, retained_result_pin, references, retained_references_pin)
```

Both functions return None on success and raise the existing ValueError-compatible
contract error on failure. They perform no file, database, provider or broker I/O.
They create no store or journal and modify no original input. Canonical JSON and
content identifiers reuse `FrozenJSON`; no alternative serialization is introduced.

## Result wire fields

Every field is required; unknown fields at any defined level are rejected.

| Field | Contract |
| --- | --- |
| `version` | Exactly `prospective_evaluation_result_v1`. |
| `state` | `pending`, `unavailable`, `invalid`, or `evaluated`. |
| `evaluated_at` | Canonical timezone-aware UTC string, using `+00:00`. It is a declared timestamp, not independently authenticated custody. |
| `policy_id`, `policy_pin` | Explicit original evaluation-policy identity and content reference; no policy validation here. |
| `cohort_id`, `cohort_pin` | Explicit original evaluation-cohort identity and content reference. |
| `provenance` | Exactly `evidence_class` and `verification`; class is `synthetic_offline` or `unverified_prospective`, verification is always `not_performed`. |
| `counts` | Exact integer case accounting described below; booleans and floats are rejected. |
| `cases` | 1–256 ordered case declarations, unique by prediction IDs/pins and non-null outcome IDs/pins. |
| `metrics` | Exact reserved fields, all null in this foundation. |
| `genuine_forward_evidence`, `pit_qualified`, `performance_approved`, `execution_authorized` | Exactly false, including for `evaluated` declarations. |

A case contains exactly `prediction_id`, `prediction_evidence_pin`, `outcome_id`,
`outcome_evidence_pin`, `source_id`, `prediction_recorded_at`,
`outcome_observed_at`, `state` and `reason`. References use lowercase 64-character
hexadecimal content identifiers. `source_id` preserves a provenance reference; it
is not proof of provider identity. Reasons are bounded lowercase machine codes.
Cases must be sorted by prediction ID. All instants must be canonical UTC.

An outcome's ID, evidence pin and observation instant must all be present or all
null. When present, its declared instant must be strictly after the prediction's
recorded instant and no later than `evaluated_at`. Prediction recording may not
be after evaluation. These are chronology consistency checks, not PIT proof.
An evaluated case requires an outcome reference; a pending case must not contain
one. Unavailable/invalid cases may retain a rejected outcome reference when its
structure and declared chronology are still well formed.

The strict unique-outcome rule prevents repeated outcomes counting as independent
samples in this initial format. Repeated-outcome multi-model comparisons would
need a separately reviewed contract rather than silently changing v1 semantics.

## States and counts

Counts contain exactly `total_declared_predictions`, `evaluated_samples`,
`pending_samples`, `unavailable_samples` and `invalid_samples`. They must match
the actual case declarations, with the total equal to the number of cases.
Missing/unavailable cases remain in that total. No missing case becomes a zero
return or an incorrect prediction.

Aggregate state has deterministic precedence: any invalid case means `invalid`;
otherwise any pending case means `pending`; otherwise any unavailable case means
`unavailable`; only an entirely evaluated declaration is `evaluated`.

**Evaluated is a supplied lifecycle declaration, not the validator's assertion
that evaluation actually occurred.** Even a structurally valid evaluated result
has unverified provenance and no metrics. Counts describe declared states only;
they are not independently verified sample counts or approved coverage.

## Metrics remain locked

`metrics` contains exactly:

```json
{
  "evaluation_coverage": null,
  "directional_accuracy": null,
  "always_up_baseline": null,
  "always_down_baseline": null,
  "uncertainty": null
}
```

No boolean, supplied verification flag, self-rehashed document, mode or result
state can unlock these fields. Zero, false and nested objects are also rejected.
This module has no prerequisites verifier and therefore no path to non-null
performance. A later separately reviewed integration must establish original
prediction/outcome replay, policy predeclaration, cohort compatibility, genuine
creation-time assurance and valid uncertainty methodology before implementing
performance reporting. The current contract must not be silently relaxed.

## Reference integrity versus authenticity

`verify_integrity` first performs structural validation and checks the separately
retained result pin. It also compares against a separately pinned original
reference document with the exact wire shape:

- Version `prospective_evaluation_result_references_v1`.
- Original `policy_id`, `policy_pin`, `cohort_id`, `cohort_pin`, `evaluated_at`
  and `provenance` copied from the original owner.
- `cases` in the same canonical order, containing only `prediction_id`,
  `prediction_evidence_pin`, `outcome_id`, `outcome_evidence_pin`, `source_id`,
  `prediction_recorded_at` and `outcome_observed_at`.

Altered references fail even if an attacker supplies a new result hash while the
original reference pin remains fixed. Altered reference documents fail against
the original retained reference pin. References with unknown/missing fields do
not compare equal. The reference document is an in-memory transport dependency,
not a new registry or evidence store.

This comparison proves consistency with retained values, **not** authentic
publication, independently verified outcome evidence, model validity or genuine
prospective performance. If an attacker can replace both original documents and
all trusted pins, these checks cannot establish independent provenance. Never
create the trusted reference solely from the received result under examination.
Original owners and independent custody/replay remain required outside this PR.

## Bounds and compatibility

Results and reference documents are each limited to 256 KiB. The existing
`FrozenJSON` parser rejects duplicate JSON keys and produces detached deterministic
values. Callers must bound raw input before constructing a FrozenJSON; these
limits do not provide hard process-memory isolation.

No existing journal, prediction binding, policy, performance calculation, public
CLI/API, database format or research approval is changed. This foundation offers
no evaluation consumer and no policy/audit integration. Synthetic fixtures remain
synthetic; historical timestamps cannot establish forward/PIT eligibility.
