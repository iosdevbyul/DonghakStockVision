# A-2: remote byte readback comparison

`verify_custody_readback` takes an explicit `CustodyReadbackTransport`
provider implementation, retrieves remotely stored receipt bytes using
the stable idempotency key, and compares SHA-256 with the verified local
export. Missing bytes, altered bytes and transport failures are blocked.

A matching digest means `content_consistent_unverified`, **not**
authenticated provider identity or independently durable storage.
This is not wired to a real custodian, and it cannot approve inference.
Actual deployed readback, verifiable external acknowledgements and
independent operator control remain required before completing A-2.
