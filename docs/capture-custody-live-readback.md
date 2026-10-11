# A-2 remote custody integration: implementation contract and rollout

The optional approved HTTPS custody session now supports an explicit
`verify_readback(connection, destination, raw_id)` call. The HTTPS adapter
retrieves stored receipt **bytes** with `GET {endpoint}/{raw_id}/content`,
using the same Idempotency-Key and runtime Bearer token (if provided) as
submit and lookup. The returned bytes are compared with the verified local
receipt digest. A content match still produces only
`content_consistent_unverified`, **not** authenticated remote custody.

## Required independent custodian

An independently administered HTTPS service must implement:

- `PUT {endpoint}/{raw_id}`: durably store supplied canonical receipt
  bytes, enforce Idempotency-Key, reject conflicting replays, return a
  non-empty response; the client does not trust HTTP 2xx as acceptance.
- `GET {endpoint}/{raw_id}`: return a non-empty receipt status claim,
  or HTTP 404 for a missing claim.
- `GET {endpoint}/{raw_id}/content`: return **exact original receipt
  bytes** after durable storage, or HTTP 404 if absent. Do not return
  a JSON envelope or status metadata on this route.
- Use publicly trusted TLS, approved exact hostname and appropriately
  scoped runtime credentials. Do not store tokens in Git.
- Provide separately controlled durable retention, access logs, restricted
  deletion, and a verifiable acknowledgement/authentication design.

The existing HTTPS client limits responses to 64 KiB. A provider must
respect this limit, including readback content. No actual provider or
credentials are bundled.

## Operator acceptance checklist

1. Obtain a separately administered provider endpoint and a least-privilege
   runtime Bearer credential. Arrange and document independent custody
   ownership and deletion controls.
2. Create an approved session with the exact provider hostname.
3. With a non-sensitive test receipt, export and enqueue; submit explicitly
   and record the audit event.
4. Read back the exact stored bytes with `verify_readback`. Compare the
   receipt SHA-256 and investigate every mismatch or unavailable response.
5. Verify duplicate PUT is idempotent; conflicting bytes with the same key
   are rejected. Simulate lost response and service downtime; reconcile
   before any replay. Do not treat a 404 as authenticated absence.
6. Implement and validate **independently authenticated** provider receipt
   acknowledgements, remote durability and replay evidence; document
   who administers the remote system. This is not implemented by this PR.

A-2 remains **in progress** until the real provider and production-like
recovery tests are evidenced. A-3 separately addresses cryptographic
signatures and trust-key management.
