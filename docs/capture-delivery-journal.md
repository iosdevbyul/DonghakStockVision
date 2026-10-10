# Phase A-2: local delivery attempt journal

`reconcile_and_journal` records an append-only local SQLite attempt row
for each reconciliation outcome. It also rechecks successfully exported files
before recording their result. Use `after_raw_id` and `next_raw_id` to
page through the archive, then restart from the beginning during a later
verification cycle to revisit blocked receipts.

A crash after filesystem export but before its attempt row is saved is
handled by retry: reconciliation re-verifies the already-existing file.
If journaling fails, the error propagates; the caller **must not** claim
delivery completed. Existing evidence is not overwritten.

The receipt itself remains in local SQLite; a delivery journal in the same
administrative domain is **not** independent acknowledgement, tamper-proof
custody, or external durability. A-2 is still in progress pending an
independently administered destination, durable acceptance receipt, and
operational replay policy. Signing/key validation belongs to A-3.
No trading/inference authorization is granted.
