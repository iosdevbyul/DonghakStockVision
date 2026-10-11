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
