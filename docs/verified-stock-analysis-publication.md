# Verified stock analysis publication boundary

The optional offline `publish_approved_signals(source, approval, destination)` converts **previously generated and independently reviewed operational point-in-time signal records** to the `published_stock_analysis_v1` API file. It never fits, loads, or executes models; it never converts aggregate Phase 2 validation/OOS metrics into individual ticker predictions.

The source must be a JSON export of the operational signal query, shaped `{"scope":"operational","items":[...]}`. The independent reviewer must supply a separate JSON receipt:

```json
{
  "schemaVersion": "approved_operational_signals_v1",
  "sourceSha256": "<sha256 of exact input file bytes>",
  "reviewer": "<independent reviewer>",
  "approved": true,
  "scope": "operational",
  "sourceMode": "point_in_time",
  "reviewedAt": "2026-10-02T08:00:00+09:00"
}
```

This receipt is an explicit **manual approval assertion**, not cryptographic proof of who reviewed it. Before production publishing, establish authenticated reviewer signatures, source registry checks, immutable artifact identity verification and a second-person approval workflow. Do not sign your own unverified files.

The converter requires real-data PIT mode, approved registered operational status, usable input status, empty quality flags, nonempty source snapshot identity and SHA-256 input hash, ordered timestamps and unique analysis IDs. Directional scores must be conditional, not both simultaneously active. Empty reviewed source exports produce empty publications, never guessed stock signals. No output overwrites existing files.

**Remaining gate:** Approved operational registered models and genuine PIT inference records must exist before this yields nonempty results. Current reviewed Phase 2 model metrics alone do not satisfy that requirement. The publication remains off unless manually supplied to the API via `--analysis-publication`.

This implementation is deliberately separate from `ModelInference.HGBInference` (the approved checkpoint trust boundary). It does **not** claim to provide a new HGB batch inference runner. No actual model execution or end-to-end market database integration was performed.
