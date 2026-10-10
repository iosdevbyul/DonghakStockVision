# Phase A-2: explicit local capture evidence export

The existing collection code already records `raw_pages` and immutable local
`raw_capture_receipts` in one SQLite transaction (PR #67).

This increment introduces an **explicit** filesystem export boundary.
`export_capture_receipt(connection, raw_id, destination)` verifies the stored
raw payload and receipt, then writes deterministic metadata to
`raw-<id>.json` with exclusive creation, file fsync, and directory fsync.
The destination must be provisioned by the caller. It never overwrites an
existing file. `verify_exported_capture_receipt` compares bytes against
the current archive and detects missing/modified exports.

The call is **not** wired into `archive` yet: SQLite commits and a separate
filesystem cannot be made one atomic transaction without a delivery journal
and recovery process. Failure to export is surfaced to the caller, never
reported as successful durable independent capture. Raw originals are
preserved; no implied rollback of already committed collection rows occurs.

The file is **not signed**, cannot prove a collection timestamp, and is not
a WORM or remotely managed trust store. A filesystem administrator can
alter both locations. The generated SHA-256 is not an independent pin.
A-2 remains incomplete until a delivery/acknowledgement and replay protocol
is implemented and the sink is under a separately controlled trust boundary.
A-3 will address cryptographic signatures and independent key verification.

No external API calls, database migrations, model activation, or quality
flag relaxations are introduced in this PR.
