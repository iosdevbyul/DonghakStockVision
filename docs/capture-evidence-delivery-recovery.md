# A-2: receipt export reconciliation

`reconcile_capture_receipts` scans the existing automatically captured
`raw_capture_receipts` rows in stable ID order. It explicitly exports missing
files to a separately provisioned directory, confirms matching existing files,
and **blocks** conflicting or unsafe paths rather than replacing them.
A bounded `limit` restricts each call. Use `after_raw_id` with the last\nreturned raw ID to continue through the entire archive; do not retry\nevery batch from the first receipt. Blocked rows remain visible to callers\nand must be tracked for operator remediation. The operation is idempotent for
successfully exported files. An interrupted export can require operator
intervention if a partial file exists; it is never silently overwritten.

This is a deliberate, operator-invoked recovery step, **not yet a scheduled
delivery daemon or a durable external acknowledgement journal**. Raw pages
and receipt rows are committed together, but the export is later and not
atomic with SQLite. A crash can leave a missing or partial export, which
this reconciliation reports or repairs according to its safe rules.

Exports are on a local filesystem and are **not** a trusted independent
archive. The scanner's delivered status describes only current byte equality,
not proof of historical availability, custody, or signed origin. An
administrator with access to both stores can alter both. Old raw pages lacking
receipts remain untouched. No quality flags, PIT authority or operational
inference permissions are changed.

To complete A-2, add secure externally controlled custody plus a durable
acknowledgement/retry policy; A-3 separately addresses evidence signatures
and independently managed verification keys.
