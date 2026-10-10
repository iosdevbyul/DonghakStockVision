# Operational artifact provenance preflight

`inspect_operational_artifact` is a read-only structural inspection of a proposed operational checkpoint, manifest, experiment lock and qualification receipt. It compares the exact bytes of all four files to **independently pinned SHA-256 fingerprints**, checks checkpoint/proposal/manifest identity, links lock and qualification identifiers and checks that neither provenance document still says `research_only` or `unregistered`.

**This is not an authorization mechanism.** The supplied pins must originate from a trusted external authority. The manifest's `fingerprint` field is only checked for equality, not recomputed as a canonical model fingerprint. Consistent strings are not proof that a model underwent independent operational qualification. The output's `registration_authorized` is always false. Production usage requires authenticated reviewer signatures, a trusted authoritative operational qualification registry, verified lock/qualification semantics, a trusted runner, and a separate deployment approval.

The module does not read from the network or mutable market storage, deserialize pickle, load HGB estimators, generate predictions, write registry data or publish API responses. Existing research-only checkpoint controls remain unchanged.
