# Real OHLCV to PIT feature candidate

`build_pit_features` uses the existing `MarketDataStore.read` boundary via `capture(mode="point_in_time")`, followed by `snapshot_bars` and the existing causal `feature_vector` implementation. It does not substitute sample values for actual features. Inputs require externally reviewed quality coverage and strict past-data cutoffs.

The resulting candidate has exact feature order, a snapshot content digest and an input window digest. These are reproducibility identifiers, **not** authenticated proof of revision history or independent provenance. It preserves **all** source snapshot quality flags, including revision-history and retrospective-review warnings, and reports `snapshot_quality_review_required`. It never marks inference authorized; the existing research-only HGB constraints remain.

No model loading, inference, registration, publication, fake OHLCV generation, network call or DB write occurs. A read-only adapter for the source store is required at runtime. Subsequent work must attest source revision history and resolve snapshot quality flags independently, before any inference permission.
