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

## Explicit endpoint approval gate

Use `create_approved_https_custody_transport(endpoint,
approved_hostname=...)` for operator-controlled HTTPS destinations.
The independently supplied approved hostname must exactly match the URL
hostname. The gate refuses hostname suffix impersonation, localhost,
IP literals and non-443 ports. It rejects a mismatch before any request.
The lower-level transport constructor remains available for backward
compatibility, so callers must opt into the approval gate. This is not
a DNS/IP resolution policy, TLS certificate pinning or proof that a
third-party custodian is independent. A-2 remains in progress.
