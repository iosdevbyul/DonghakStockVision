# A-2: approved custody session

Use `create_approved_custody_session` to require an independently
supplied, exact HTTPS hostname approval before starting the existing
explicit submission or lookup operations. The resulting session
reuses the same transport for both operations, preserving a stable
receipt idempotency key and separate append-only local audit trails.

The session **does not** send automatically, promote unverified remote
responses to accepted custody, mutate outbox retry state or grant
inference permission. Existing lower-level transport interfaces remain
available for compatibility; operators must opt into this boundary.

This is not a deployed independently administered custodian. DNS
resolution restrictions, authentication, signed acknowledgement
validation, remote durability and verified replay are outstanding.
A-2 remains incomplete.

## Opt-in credential-required session

Use `create_authenticated_custody_session` when an operator requires
a caller-provided bearer credential to construct the approved session.
Missing or malformed tokens fail before any network call. The token
is excluded from the transport's ordinary representation and is only
placed in the HTTPS Authorization header for outbound requests.

The existing optional-auth factory stays available for backwards
compatibility. A bearer credential authenticates the **request to the
provider** according to that provider's policy; it does not authenticate
the provider's acknowledgement, prove independent custody, or grant
inference authorization. Do not persist credentials in source control.
