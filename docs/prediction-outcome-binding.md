# Frozen prediction-to-outcome binding

`prediction_outcome_binding_v1` connects an original v2 forward journal entry to
one independently replayed future capture. It adds no store and changes neither
the original prediction nor the observation. The current implementation admits
only explicitly synthetic offline evidence. A successful local replay does not
prove when a prediction was independently recorded or a price publicly available.

## Original owners and frozen constraints

Supply the original journal, separately retained journal-head and entry pins,
prediction ID, original capture root, signal request and its pin, and the original
prediction-policy pin. Pins must come from the owner's independently retained
references, not from the report being verified. Journal chain verification
reconstructs prediction metadata. Original signal replay verifies the implementation,
parameters and capture/raw-receipt lineage. Legacy v1 entries remain readable by
the existing journal but cannot acquire a predeclared prediction retroactively.

Only the entry's frozen target date, evaluation gate, deadline and directional
outcome rule control resolution. The future request must name exactly that date,
instrument, original research-split pin and authorized partition. The existing
future-observation resolver performs source, database and capture verification.
Protected-partition checks precede capture loading. No historical warm-up or
provider authorization is inferred from timestamps.

Binding v1 explicitly adds `capture_started_and_ingested_within_frozen_window_v1`:
request start, local capture and ingestion must all be at or after the frozen
evaluation gate, and ingestion must be no later than the frozen deadline. This
is a stricter pair-eligibility condition than the standalone observation resolver's
read gate. It does not rewrite existing horizon policies or journal entries.
A timely retained observation may be processed after the deadline.

## Status and economic meaning

| Status | Meaning |
| --- | --- |
| `pending` | Original prediction verified; gate not reached. Future evidence is not loaded. |
| `verified` | Original prediction and exact future observation replayed and bound. |
| `unavailable` | Original prediction valid, but future evidence missing or outside the allowed window. |
| `invalid` | Original or supplied future evidence fails integrity, identity or scope checks. |

Unavailable and invalid outcomes are null, never zero returns or incorrect
predictions. A verified pair compares target close with the original reference
close: strictly higher is up, lower is down, equal is flat. Flat does not satisfy
an up/down prediction. Price change is represented as the exact integer fraction
`(target_close - reference_close) / reference_close`. It is not portfolio P&L,
net-of-cost performance or evidence of investment profitability.

All current outputs retain `genuine_forward_evidence=false`,
`independent_creation_time_verified=false`,
`prospective_evaluation_eligible=false` and `execution_authorized=false`.
Only verified pairs have `offline_evaluation_eligible=true`. Genuine prospective
claims require independent original prediction-time evidence, unavailable here.

## Read-only CLI

Use original files and independently retained pins; the variables below are
operator-provided references, not example values to copy into real evidence.

```sh
python -m donghak_stock_vision.prediction_outcome_cli \
  --journal "$JOURNAL" --journal-head "$HEAD_PIN" \
  --entry-pin "$ENTRY_PIN" --prediction-id "$PREDICTION_ID" \
  --capture-root "$ORIGINAL_ROOT" --request "$ORIGINAL_REQUEST" \
  --request-pin "$ORIGINAL_REQUEST_PIN" --policy-pin "$POLICY_PIN" \
  --as-of 2025-07-12T01:00:03+00:00 \
  --future-capture-root "$FUTURE_ROOT" --future-request "$FUTURE_REQUEST" \
  --future-request-pin "$FUTURE_REQUEST_PIN"
```

Omit all future flags to inspect pending/missing status. Instants require timezone
information and are normalized to UTC. The command prints canonical JSON and
returns 2 for invalid evidence or malformed input. It writes no journal, capture,
market database, analysis record or order, and makes no network requests.

`verify_binding` checks a separately trusted result pin and reconstructs the
entire binding from original owners. A self-rehashed altered report fails semantic
verification. There is no cache granting trust to subsequently supplied documents.
Repeated calls with unchanged owners and evaluation time are deterministic.
The existing journal's 256-entry and capture/request limits still apply; this is
bounded local research functionality, not an unbounded production evaluator.
