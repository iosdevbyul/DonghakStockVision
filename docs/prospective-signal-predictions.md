# Prospective signal horizons in the existing journal

The forward signal journal already verifies captures, freezes the SMA implementation
and parameters, and stores immutable decisions. It previously had no predeclared
prediction horizon. This additive workflow uses the **same journal**, tables,
application ID, transaction/rollback guards and external head-pin discipline.
There is no second prediction database or custody system.

## Scope and trust

Initial prospective records are **offline synthetic fixtures only**. Local UTC
clock consistency is checked, but independent creation-time authentication is
unavailable. `independent_creation_time_verified`, `genuine_forward_evidence` and
`execution_authorized` remain false. Provider-mode prediction requests fail before
capture/journal loading rather than receiving a self-issued attestation.

This freezes the original deterministic SMA predictor's ID, version, source-code
hash and parameter snapshot. It does not add a learned model, a probability score,
training, model promotion, PAPER activation or orders. Existing signal/PAPER
authorization and historical PIT/OOS/holdout restrictions remain unchanged.

## Predeclared policy

`forward_prediction_policy_v1` contains exactly:

```json
{
  "version": "forward_prediction_policy_v1",
  "target_trading_date": "2025-07-11",
  "evaluate_not_before": "2025-07-12T00:00:00+00:00",
  "outcome_deadline": "2025-07-15T00:00:00+00:00",
  "max_recording_delay_seconds": 10
}
```

These dates belong to controlled test clocks, **not a current live plan**. The CLI
uses the actual current clock; old target dates cannot be backfilled with `predict`.
The runnable demo below prepares new synthetic dates relative to its current clock.

The target must be strictly after the recording date in Seoul and wholly inside
the source's original authorized training/tuning partition. Crossing into a protected
partition is rejected before source I/O. `evaluate_not_before` is an explicit policy
clock gate after the complete target calendar date, not an exchange publication
timestamp. The deadline is later, at most seven days after that gate and at most
31 days after recording. Recording must occur within the explicitly configured
1–300-second delay after signal calculation. Instants must use canonical UTC.

The policy does not certify a trading session. A holiday target must remain
unavailable if no eligible bar exists; no weekday or next-session substitution is
authorized. `strict_directional_close_change_v1` predeclares comparison with the
latest verified input close: BUY predicts strictly higher and SELL strictly lower
target close; an unchanged close is incorrect. This is a direction event, not
investment P&L. HOLD and INSUFFICIENT_DATA cannot become directional forecasts.

## Recording and compatibility

`forward-signal predict` accepts the existing source request/archive and journal
head, plus `--prediction-policy` and its separately retained
`--prediction-policy-pin`. It always regenerates and verifies the original signal;
arbitrary decision JSON is not appended. It records a `forward_signal_entry_v2`
with `forward_prediction_evidence_v1`, binding the policy, predictor, exact input
fingerprint, reference observation/close, direction and local recording time.

Legacy entry v1 bytes, IDs and semantics do not change. Mixed v1/v2 chains are
readable. Existing entries cannot be retroactively upgraded with a horizon, and
the same signal cannot receive a different policy. Repeating the same prediction
is idempotent even after its deadline: the original recording time and head remain
unchanged. No destructive update is supported. Existing source archives remain
read-only. Retain head pins outside the mutable journal to detect truncation;
neither local hashes nor those pins authenticate a historical clock.

## Executable offline example

With the package installed, use a new temporary directory:

```bash
DEMO_ROOT="$(mktemp -d /tmp/dsv-prospective.XXXXXX)"
dsv forward-signal offline-prediction-demo \
  --capture-root "$DEMO_ROOT/captures" \
  --journal "$DEMO_ROOT/signals.sqlite3" > "$DEMO_ROOT/result.json"
```

This reuses the original mock HTTP capture/parser, raw receipts, source verification
and existing signal journal. Four explicit fake closes (100, 90, 80, 120) produce
BUY and a reference close of 120. It constructs a **synthetic-only** research scope
covering a future fixture target; it never creates or alters an operator's real
research split. Weekday choices are fixture conveniences, not a verified calendar.
The result identifies `offline_fixture`, zero live requests and a pending forecast.

Inspect its original source-to-prediction chain and status:

```bash
python - "$DEMO_ROOT" <<'PY'
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
d = json.loads((root / "result.json").read_text())
subprocess.run([
    "dsv", "forward-signal", "prediction-status",
    "--capture-root", str(root / "captures"),
    "--request", d["request_path"], "--request-pin", d["request_pin"],
    "--journal", str(root / "signals.sqlite3"),
    "--journal-pin", d["journal_head_pin"], "--signal-id", d["signal_id"],
], check=True)
PY
```

For ordinary recording, replace the action with `predict` and provide the prepared
policy file and pin. No arbitrary CLI recording-time override exists.

## Read-only lifecycle

`prediction-status` replays source and journal integrity before reporting:

- `pending`: the policy clock gate has not elapsed.
- `matured`: the gate elapsed and a verified target observation is still awaited.
- `unavailable`: the deadline elapsed without an outcome supplied to this status view.

Exact deadline is included in the maturity window. Status before the original
recording time is rejected. This is a read-only clock projection, not an outcome
verifier: `outcome` remains null, including at maturity. A legacy v1 signal has no
predeclared prediction and cannot acquire one through inspection.

Synthetic tests verify chronology, policy/source pins, duplicate protection,
original predictor/input binding, mixed legacy chains, interrupted writes and
rehashed metadata forgery. Production data and all original evidence are untouched.

Next, separately verify exact target-date observations captured after the original
record, without modifying it. Genuine real-provider validation additionally needs
an existing approved independent creation-time/custody evidence path; this change
does not invent that authority or relax its absence.

## Complete source-to-prediction custody rehearsal

The existing fixture preparation, prediction contracts and journal are reused by
`forward-signal offline-prediction-custody-demo`. It freezes original raw-source
custody references before append and reconstructs a pinned end-to-end report after
readback. See [the executable workflow and recovery procedure](prospective-prediction-custody-workflow.md).
