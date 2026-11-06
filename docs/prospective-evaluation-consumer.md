# Read-only prospective evaluation preparation

## Scope

`storage.prospective_evaluation_consumer` composes the merged policy, original
prediction binding, evidence audit and result-integrity contracts. It does not
calculate predictive performance, approve evidence, create a store or perform
provider/broker requests. Its public interface is `EvaluationSources`,
`prepare_evaluation` and `verify_preparation`.

```python
from donghak_stock_vision.storage.prospective_evaluation_consumer import (
    EvaluationSources, prepare_evaluation, verify_preparation,
)

# All owners and pins come from original retained files/references.
source = EvaluationSources(
    evaluation_policy, evaluation_policy_pin,
    original_split, original_split_pin,
    original_prediction_policy, original_prediction_policy_pin,
    original_evidence_cases,
)
report = prepare_evaluation(source, explicit_utc_as_of)
# Retain report.identifier and report.to_dict()['references_pin'] separately.
verify_preparation(report, retained_report_pin, retained_references_pin,
                   source, explicit_utc_as_of)
```

No data is loaded from arbitrary summary JSON. Cases are the existing immutable
`EvidenceCase`/`PredictionSource`/`FutureSource` owner references. Inputs are
read-only; the library writes no journal, capture, report, analysis database or
pin. The operator supplies owners through this explicit Python API; no new public
CLI/API contract is introduced.

## Narrow semantic association

The policy v1 stores opaque cohort and horizon pins but does not define their
owners. This integration deliberately supports only the existing original
`ExplicitResearchSplit` owner as cohort and the existing frozen
`forward_prediction_policy_v1` owner as horizon. Their independent retained pins
must match the evaluation policy and every original source. All cases must share
one authorized partition and instrument. Cohort membership is additionally fixed
by the policy's complete sorted prediction-ID list.

The shared horizon is exact: original target date, evaluation gate, deadline and
recording-delay policy must match. The original binding resolver enforces future
source membership, capture/ingestion chronology, source pins and protected
partition restrictions. There is no generalized rolling-horizon inference.
Unsupported cohort/horizon owner kinds remain blocked rather than being guessed.

The local declaration instant must not follow original prediction recording.
This check is not independent timestamp custody: `genuine_prospective` mode is
blocked with `independent_prospective_policy_custody_unavailable`. Genuine policy
predeclaration and original creation-time authentication are not established by
hashes or local clocks. Synthetic inputs remain explicitly synthetic.

## Verification sequence and limits

1. Verify the original evaluation policy, scope owner pins and horizon contract.
2. Require exact declared prediction membership. Missing/extra cases, duplicate
   IDs and more than 32 cases fail before replay. A case with an original
   prediction but no future observation is valid input and remains unavailable.
3. Apply existing request/partition and source-cohort checks before capture loading.
4. Run the existing audit, including its request and aggregate byte budgets, and
   independently replay each original prediction to recover authoritative metadata.
5. For structurally eligible pairs, replay the binding again and compare its pin
   with the audit's pin. A source change between reads is rejected.
6. Construct and validate the existing result v1 and reference document. The
   preparation report is bounded to 256 KiB; no result restriction is relaxed.

The strictest count bound is the audit's 32 cases, although policy/result v1 each
allow 256. Existing 256 KiB per-request and 2 MiB aggregate-request limits remain
in force. Replay is sequential and may verify the same original owner multiple
times; there is no cross-document trust cache. Limits are cooperative, not hard
memory/process isolation or crash-recovery guarantees.

## Result and diagnostic semantics

The `prospective_evaluation_preparation_v1` wrapper contains the unmodified
`prospective_evaluation_result_v1`, its pin, its original-source reference document
and pin, the audit pin, horizon pin and preparation diagnostics.

Audit eligible maps to result evaluated **as a structural declaration only**.
Pending, unavailable and invalid future evidence retain their existing reason
codes. The result's deterministic state precedence remains invalid, pending,
unavailable, evaluated. Result provenance stays `verification=not_performed`:
that contract explicitly means independent authenticity, which this local replay
does not establish. Successful local replay is not presented as independent
market-evidence custody.

Diagnostics report the complete declared case count and structurally ready count,
plus minimum-count and minimum-structure-coverage threshold booleans. Coverage
comparison uses exact integer cross multiplication. Missing, pending and invalid
cases remain in the declared denominator. Result coverage, accuracy, baseline and
uncertainty metrics all remain null, even if thresholds pass. No return or
predictive-performance statistic is exposed. Every PIT/genuine-evidence,
performance-approval and execution-authorization flag remains false.

`verify_preparation` requires a separately retained report pin and reference pin,
rebuilds the complete preparation from original owners and policies, and invokes
the result-integrity validator. Rehashing altered counts, metrics, source references
or diagnostics cannot replace original-source replay. Deriving all trusted pins
from a received report alone is not an independent trust root.

## Existing contract incompatibilities and safe boundaries

- An unreadable/invalid original prediction has no authoritative recording instant,
  while result v1 requires one for every case. The consumer rejects that complete
  request rather than inventing a timestamp or emitting an apparently valid result.
  Invalid future evidence for a verified original prediction is accounted as invalid.
  A future nullable-provenance error envelope would need separate review.
- Result v1 requires unique outcome IDs and pins. Two predictions sharing one
  original outcome are rejected by the existing result validator. No per-pair hash
  disguises that shared observation. A separately versioned dependent-sample
  contract would be the minimal safe follow-up for such cohorts; v1 is unchanged.
- Policy v1 has no authentic independent predeclaration receipt. Genuine market
  preparation remains blocked. This consumer supports bounded offline synthetic
  preparation only and grants no policy, OOS, PIT or operational approval.

These are explicit scope restrictions, not reasons to weaken the merged contracts.
No genuine predictive results or production-readiness claims follow from this PR.
