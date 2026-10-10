# Operational qualification registry diagnostic contract

The read-only `operational_qualification_registry_v1` schema describes proposed, time-bounded qualified or revoked entries linked to exact proposal bytes, checkpoint hash, model identity/version and qualification ID. Loading requires a trusted SHA-256 pin provided **independently**, not from the submitted registry itself. Duplicate proposal IDs, invalid timestamps, mismatched identities and expired/revoked entries fail closed.

`inspect_qualification` additionally requires the prior composed registration review to be structurally ready and independently signed. It does not load any model or grant registration. Both `qualification_record_valid` and `ready_for_external_authorization` are diagnostics only; `registration_authorized` **always remains false**.

**Security boundary:** A matching fingerprint does not authenticate who issued the file. Authenticity of an authoritative operational qualification registry, accountable second-person deployment approval, revocation governance and real checkpoint qualification are not established. The implementation is intentionally non-authorizing and uses synthetic tests; no genuine operational HGB artifacts are currently approved, registered, inferred or published.

Next: establish an independently governed issuer trust mechanism and an audited authorization process before granting execution permissions. Research-only artifacts remain restricted.
