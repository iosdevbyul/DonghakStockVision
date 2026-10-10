# PIT inference feature input boundary

`inspect_pit_input` checks a single ticker's schema-versioned feature vector for the exact `ohlcv_value_v1` feature order, finite values, real point-in-time provenance declarations, a complete prior Seoul trading day, source/data cutoffs, input hash and empty quality flags.

A supplied snapshot ID or SHA-256 is **not** an independently verified provenance receipt. The caller has to verify snapshot identity, historical market rows, feature calculation, collection times and source cutoff against immutable trusted storage. This module does not access or trust such storage.

The existing execution gate is consulted, but the returned `inference_authorized` is deliberately **always false**, including if an untrusted caller supplies a forged positive execution assessment. No model loads, predictions, publications, write operations or research-only upgrades occur.

Next: build a trusted PIT snapshot/feature resolver with actual immutable market data, independent source checks and a real (separately approved) operational permission authority before allowing inference. This does not claim that production predictions are ready.
