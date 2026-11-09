# Offline prediction-to-future-observation E2E rehearsal

This read-only operator entry point composes existing original-source custody
verification, immutable prediction-journal replay, future observation binding,
evidence audit, and the v1 prospective evaluation consumer. It adds **no** new
evaluator, journal, receipt authority, market data provider or signing key.

## Input and run

Retain the original source captures and journal, the separately created future
capture root, all original requests, and every independent owner pin. The input
is a JSON object with the exact fields shown here. The pairs `owner` and
`pin` contain each owner JSON object and its separately retained SHA-256 pin,
not a convenient replacement pin calculated from an untrusted document.

```json
{
  "version": "prediction_outcome_e2e_input_v1",
  "cases": {"owner": {"version": "prospective_evidence_audit_input_v1", "cases": []}, "pin": "<retained hex digest>"},
  "prediction_policy": {"owner": {}, "pin": "<retained hex digest>"},
  "workflow": {"owner": {}, "pin": "<retained hex digest>"},
  "evaluation_policy": {"owner": {}, "pin": "<retained hex digest>"},
  "cohort": {"owner": {}, "pin": "<retained hex digest>"},
  "horizon": {"owner": {}, "pin": "<retained hex digest>"}
}
```

The `cases.owner` payload is the **existing**
`prospective_evidence_audit_input_v1` contract (one case for this workflow).
Its prediction object requires absolute journal/capture-root paths, original
journal head, entry/prediction identifiers, request object, request pin and
policy pin. Its future object is either `null` (pending/unavailable) or the
separately retained capture-root path, future request object and request pin.
The horizon owner must equal the prediction policy and retain the same pin.
The evaluation policy must declare exactly this prediction and its original
cohort and horizon.

After obtaining an independently retained SHA-256 pin of the entire manifest:

```sh
python -m donghak_stock_vision.prediction_outcome_e2e_cli \
  --input /offline/evidence/e2e-input.json \
  --input-pin "$RETAINED_INPUT_PIN" \
  --as-of 2025-07-12T01:00:03+00:00 > /offline/reports/outcome.json
```

Retain the canonical output and its pin separately. A report verification call
reconstructs the entire E2E chain from original owners and compares all fields:

```sh
python -m donghak_stock_vision.prediction_outcome_e2e_cli \
  --input /offline/evidence/e2e-input.json \
  --input-pin "$RETAINED_INPUT_PIN" \
  --as-of 2025-07-12T01:00:03+00:00 \
  --verify-report /offline/reports/outcome.json \
  --report-pin "$RETAINED_REPORT_PIN"
```

These paths, pins, and timestamps are illustrative, not authentic observations.
The `--input-pin` and `--report-pin` are explicit; deriving either from the
supplied untrusted file gives no independent assurance. The CLI itself creates
no report file, database entries, network requests, or custody records.
The shell redirection above is an operator-controlled report export.

## Evidence and status semantics

The custody bundle is replayed against the original captured bytes and frozen
journal. The separately archived future observation uses the existing resolver,
frozen market/date/window and identity checks. The existing evaluation consumer
then recomputes the v1 report. Binding `pending` and `unavailable` remain
separate, and an invalid binding fails closed with exit code 2. A locally
verified pair has binding status `verified` and may be structurally
`evaluated` in the **existing v1 result vocabulary only**; this is *not*
a genuine-forward outcome approval. All metrics remain null.

The E2E output links custody, journal, binding and evaluation document pins.
It explicitly reports `independent_chronology_verified=false`,
`independent_external_custody_verified=false`,
`genuine_forward_evidence=false`, `pit_qualified=false`,
`performance_approved=false`, `execution_authorized=false`, and
`performance_metrics=null`. Local synthetic replay, even with a later
captured fixture or correctly verified Ed25519 signature, never establishes
independently witnessed future market data or genuine predictive performance.

## Limitations and prerequisites

This integration is intentionally one-prediction and offline. Cohort
reconciliation (#216), trust-anchor governance (#217), and cohort replay
optimization (#218) retain their **existing separate contracts**. They are not
silently converted into external time/custody authentication. An independent
predeclaration, historically authenticated prediction and observation custody,
source clock and issuer history, approved point-in-time research and performance
methodology are still required before any genuine-forward claims.

Do not perform provider requests, network uploads, brokerage orders, paid
external validation, or modify original journals. Keep the v1 evaluation result
and legacy report formats intact. The architecture boundary tests, all existing
regression suites, Python 3.12/3.13/3.14 CI and package tests remain gates.
