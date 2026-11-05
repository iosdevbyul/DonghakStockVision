# Read-only future observation resolution

This independent component verifies a single exact-date captured observation
against a caller-supplied temporal window. It reuses the existing forward request,
original archive/receipt/manifest verification and local ingestion audit. It does
not duplicate a collector, journal, custody system or price parser.

It deliberately does **not** claim to match an original prediction: no original
journal owner is accepted here and `prediction_binding_verified=false`. Its input
window must later be supplied by a verified immutable prediction record, with
matching instrument, research cohort and policy. This separation lets source
verification ship independently of an unmerged journal extension.

## CLI

The installed package exposes a standalone offline module:

```bash
python -m donghak_stock_vision.future_observation_cli \
  --recorded-at 2025-07-02T00:00:00Z --target 2025-07-04 \
  --not-before 2025-07-05T00:00:00Z \
  --available-by 2025-07-12T00:00:00Z --as-of 2025-07-03T00:00:00Z
```

This deterministic synthetic-clock example returns `pending` and no observation.
It does not claim that a prediction existed on July 2. Before `not-before`, neither
the future request file nor a capture archive is read, even if paths were supplied.
No output database or signal journal is created.

For a mature lookup, add original separately pinned references:

```bash
python -m donghak_stock_vision.future_observation_cli \
  --recorded-at ORIGINAL_RECORDING_UTC --target DECLARED_TARGET_DATE \
  --not-before ORIGINAL_EVALUATION_GATE_UTC \
  --available-by ORIGINAL_DEADLINE_UTC --as-of EXPLICIT_EVALUATION_UTC \
  --capture-root /path/to/frozen-future-capture \
  --request /path/to/original-future-request.json --request-pin ORIGINAL_REQUEST_PIN
```

The request is the existing `forward_signal_request_v1` format, scoped to exactly
one target-date source. Its archive DB, plan, capture and raw-receipt pins remain
mandatory. Only `offline_fixture` is accepted in this initial component; no real
provider request or newly self-issued authorization is supported. There is no
network opt-in, model fitting, order action or source-write mode.

## Chronology and evidence checks

- The complete target date must follow the recording date in Seoul and precede
  the explicit evaluation gate's Seoul date. Instants are normalized to UTC;
  trading dates remain calendar dates.
- Recording precedes the gate, the gate precedes the deadline, and `as-of` cannot
  precede recording. The post-gate wait is at most seven days and the complete
  window at most 31 days.
- The gate is a caller-declared clock rule, not a fabricated exchange publication
  time. No official calendar, session close or first-observed time is inferred.
- Original protected partition guards run before capture access. A mismatched
  target, extra observations, unknown owner pin, wrong scope or corrupt bytes fail
  closed. Historical research databases are not a fallback.
- Source verification time cannot exceed `as-of`. Local capture must be strictly
  after recording; ingestion must follow capture and be no later than `as-of`.
- A valid result requires ingestion no later than the declared arrival deadline.
  Later processing of an originally timely capture is allowed. A late capture
  remains unavailable rather than receiving an invented earlier arrival time.

The original verifier's size limits, source shape, timestamp checks and explicit
unadjusted OHLCV semantics remain authoritative. It computes a temporary
INSUFFICIENT_DATA SMA projection for one bar solely to reuse source verification;
that projection is never journaled or used as a new forecast.

## Result and replay

`future_observation_resolution_v1` returns:

- `pending` before the gate, with null observation and null request pin.
- `unavailable` for absent or untimely evidence, with an explicit reason and null
  observation. No close, label, return or probability is fabricated.
- `available` with the original verified observation, local timestamps, scoped
  OHLCV and receipt/capture identities.

The window is marked `caller_supplied_not_original_prediction_proof`. Prediction
binding, independent timestamp authentication, genuine forward evidence and
execution authorization all remain false. `available` means local source and
chronology consistency, not qualified investment evidence or independent custody.

`verify_resolution(result, external_pin, original_sources, original_window...)`
independently reruns resolution and compares the complete immutable result.
Rehashing changed OHLCV or changing an input window in the result alone cannot
replace original source/parameter verification. Externally retained original pins
are required; a newly received report is not its own trust root. No cache suppresses
verification of newly supplied or changed inputs.

## Executed offline scope

Tests reuse the original synthetic capture fixture and actual parser/readback. A
single scoped bar for fixture ticker 005930 on 2025-07-04 has close 120, capture at
2025-07-10 00:00:00 UTC and ingestion one second later. With the explicitly supplied
July 2 recording context and July 12 deadline, the source is available. This does
**not** prove that a real July 2 prediction existed. With a deadline at capture time
before ingestion completes, the observation stays unavailable.

```bash
pytest -q tests/integration/test_future_observation.py
```

Tests cover pending source-I/O exclusion, exact target selection, explicit deadlines,
late processing, unavailable data, invalid clocks, protected partitions, substituted
source IDs, archive corruption, rehashed report forgery, stable replay, CLI output
and source-byte preservation. CI requires no private files or provider credentials.

## Next integration

Bind this resolver's exact window and original source cohort to a verified
predeclared journal entry, then grade the predeclared direction rule without
modifying that entry. Only after that binding should prospective evaluation consume
the pair. Independently authenticated creation-time evidence and real-provider
eligibility remain separate blockers; this component cannot manufacture them.
