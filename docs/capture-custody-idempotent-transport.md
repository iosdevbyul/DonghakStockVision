# A-2: opt-in idempotent custody transport boundary

The `IdempotentCustodyTransport` protocol requires a caller-provided adapter
to support `submit(receipt_bytes, raw_id, idempotency_key)` and
`lookup(raw_id, idempotency_key)`. The `observe_idempotent_custody`
function derives the stable key from a verified local export and passes
the same key to submission and lookup operations.

An adapter's raw response is **not** authenticated or parsed into a
custody acceptance. A response means only `claim_received_unverified`;
a missing lookup result is `not_found_unverified`. Neither state
authorizes data/model inference. This new protocol is opt-in and does not
change existing `CustodyTransport` callers or outbox persistence.

No remote service, credentials, cryptographic status validation, retry
scheduler, or independently administered custody is added. A-2 is still
in progress until a real independently controlled service and its verified
acknowledgement and recovery contract are implemented.

Before submission, the exact bytes to be sent are checked against the
previously verified receipt digest. If the file changes between the
identity check and transmission read, no submit call is made. A remote
transport remains responsible for idempotency and secure acceptance;
this local check does not eliminate all filesystem replacement races.
