# A-2: custody transport boundary (incremental)

`submit_capture_receipt` verifies an existing exported local receipt
against the original SQLite archive before passing its exact bytes to an
explicit caller-supplied `CustodyTransport`. It refuses missing or altered
local exports and redacts transport exceptions. A supplied acknowledgement
is parsed through the fail-closed custody acknowledgement contract.

Even when an acknowledgement's content, identity and externally supplied
fingerprint match, the result is `claim_consistent_unverified`. No
authentication or custody authority is inferred. The returned digest is
local bookkeeping, not a trusted independent pin. No external service is
configured by default and the mock transport exists only in offline tests.

**A-2 is not complete**: a separately administered durable external
destination, independently established acknowledgement pin, persistent
outbox/retry and operator recovery policy are still required. A-3 covers
signature verification and key management. No PIT flag or model inference
authorization changes in this PR.
