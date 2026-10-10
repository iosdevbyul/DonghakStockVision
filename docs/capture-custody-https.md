# A-2: opt-in HTTPS custody transport adapter

`HTTPSCustodyTransport` is an optional adapter for the existing
`IdempotentCustodyTransport` protocol. A caller must explicitly provide
a fixed HTTPS endpoint. The adapter issues `PUT {endpoint}/{raw_id}`
with the locally derived `Idempotency-Key` and receipt bytes, and
`GET {endpoint}/{raw_id}` with the same key for lookup.

It rejects plain HTTP, credentials embedded in URLs, redirects,
invalid keys, oversized responses, and non-successful HTTP status codes.
GET 404 is mapped to an **unverified absence claim**, never to an
authenticated statement of non-delivery.

This adapter has no embedded tokens, provider registration, remote
configuration, signature verification, replay scheduling, or
independent acceptance authority. Only explicitly configured operators
can point it at a real independently administered service. Even an
HTTP 2xx is **not** a verified custody acknowledgement; the existing
observation layer never promotes it to inference permission.

A-2 remains in progress pending a genuine external custodian,
provider-specific authenticated acknowledgement contract, and
delivery/recovery verification.
