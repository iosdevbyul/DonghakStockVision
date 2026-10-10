# PIT source revision ledger review

This read-only `pit_source_revision_ledger_v1` contract documents sequential source observations, their SHA-256 identifiers, collection timestamps and predecessor digest references. The inspector checks snapshot/ticker linkage, exact independently supplied ledger fingerprint, sequence continuity, genesis, predecessor linkage, chronological observations and a last observation no later than the PIT cutoff.

**A hash-linked JSON chain is not an authenticated append-only ledger.** Prior entries can be replaced with an entirely recomputed chain and matching pin if the authority that supplies the pin is untrusted. In particular, `observedAt` is declared metadata rather than independently authenticated evidence that the data actually existed on that date. Neither historical availability nor operational inference is authorized by this review. The registry and source storage remain unchanged.

The output always has `historical_availability_verified=false` and `inference_authorized=false`. Next requires a trusted append-only capture process, independently signed origin and issuance timestamps, authentic data payload receipts, key lifecycle governance and continuous revocation checks. This tool never loads a model, publishes a prediction, or removes existing snapshot quality flags.
