# Prospective performance evaluator: predeclared policy and integration design

## Implemented scope

`storage.prospective_evaluation_policy` validates a bounded, immutable evaluation
policy and emits planning requirements. This component is independent of the
prediction/outcome binding PR. It does not read predictions, observations or
performance summaries, and does not implement an evaluator or grant eligibility.
No new store, database or custody authority is introduced.

The separately retained policy pin fixes the complete expected prediction-ID
membership, cohort and horizon-definition references, evidence mode, minimum
sample count, exact coverage threshold, metric, baselines and uncertainty policy.
IDs must be unique and canonically sorted; at most 256 cases and 32 KiB are
accepted. The coverage fraction uses bounded integers; booleans and floats are
not integer policy values. Missing fields, unknown versions, permissive metrics,
and unsupported uncertainty assumptions fail closed.

`declared_at` is canonical UTC local intent, **not** independent evidence that a
policy existed before outcomes. A consumer must separately verify temporal
predeclaration and original cohort/horizon owners. A new self-rehashed policy
must not replace the independently retained original pin.

```python
from donghak_stock_vision.storage.prospective_evaluation_policy import requirements

# owner is the original FrozenJSON policy; trusted_pin is retained separately.
plan = requirements(owner, trusted_pin)
assert plan.to_dict()["performance_available"] is False
```

## Future evaluator algorithm (not yet implemented here)

1. Verify the original policy and independently established predeclaration time.
   Resolve its original cohort and horizon-definition owners. A hash-shaped string
   alone cannot establish either meaning or authenticity.
2. Load every predeclared prediction ID in canonical order, including missing
   cases. Reject duplicate membership and undeclared additions; do not filter the
   denominator to the successful cases.
3. Reconstruct each original prediction/outcome binding using the original journal,
   source owners and trusted pins. Never accept arbitrary summary JSON or accept
   eligibility flags merely because a document's own hash matches.
4. Enforce exactly one mode. `offline_synthetic` requires replayed offline-eligible
   pairs and remains explicitly synthetic. `genuine_prospective` additionally
   requires independently verified original creation time and prospective
   eligibility. Existing synthetic pairs cannot satisfy that requirement.
5. Verify common cohort, instrument, strategy/parameter version, horizon semantics
   and directional outcome rule. Reject incompatible mixtures rather than pooling
   them into a seemingly larger sample. Distinct target dates do not establish
   equal horizons: the independently owned horizon definition must establish this.
6. Retain pending, unavailable, invalid and ineligible cases with exact exclusions.
   Only verified eligible pairs enter metrics. Coverage is eligible / all expected;
   threshold comparison uses integer cross multiplication. Missing outcomes never
   become zero return, flat outcomes or incorrect predictions.
7. Require both minimum count and coverage. Report descriptive strict-directional
   accuracy as exact correct / eligible count. Flat observations count as incorrect
   for up/down predictions. Compute always-up and always-down baselines on exactly
   the same eligible cases. No strategy winner, investment score, portfolio return
   or activation decision follows from these metrics.
8. Statistical uncertainty remains unavailable until dependence and sampling
   methodology are independently reviewed. Repeated instruments, overlapping
   horizons and selective samples invalidate an automatic IID confidence interval.
9. Produce a versioned report that includes every source binding pin, exclusions,
   policy and horizon references. Verification must replay original owners rather
   than trusting a rehashed report. Preserve all journal/capture bytes.

## Trust and research limitations

The policy contract intentionally admits both evidence modes as declarations,
without enabling either. Its requirements report always has null metrics,
`pair_evidence_verified=false`, `independent_declaration_time_verified=false`,
`performance_available=false` and `execution_authorized=false`.

This foundation does not prove real predictive performance, historical PIT safety,
independent custody, out-of-sample qualification or strategy profitability.
Original partition guards, frozen holdouts and provider authorization remain
unchanged. The next integration must reuse original binding verification and
establish authentic predeclaration rather than inferring it from local timestamps.
