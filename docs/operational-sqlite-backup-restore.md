# Operational SQLite offline backup and restore

This is an **opt-in Python API** for an operator. Nothing is scheduled or enabled
by default. No provider requests, network access, KRX policy mutation or PAPER
trading occurs.

The functions are available from
`donghak_stock_vision.storage.operational_backup`:

- `backup_operational_database(source, destination)` uses the SQLite online
  backup API with a **read-only** connection to the original database, then
  publishes a consistent backup as a new file.
- `verify_backup(path, expected_sha256=...)` checks exact operational schema,
  integrity, foreign keys and file fingerprint.
- `restore_operational_backup(backup, destination, expected_sha256=...)`
  creates a new offline database file and verifies it; it does **not** modify
  the existing active database.

All paths must be absolute. Destination directories must already exist.
Destinations may not exist beforehand and can never be overwritten.
Source symlinks and hardlinks are rejected. Temporary files are mode 0600.
Use a dedicated private (0700) directory. A backup does not make the
collection's evidence research-qualified, paper-authorized or production-ready.

Example using operator-reviewed private paths (replace placeholders):

```python
from pathlib import Path
from donghak_stock_vision.storage.operational_backup import (
    backup_operational_database,
    restore_operational_backup,
    verify_backup,
)

source = Path("/absolute/private/database/operational.sqlite3")
snapshot = Path("/absolute/private/backups/operational.snapshot.sqlite3")
report = backup_operational_database(source, snapshot)
print(report)
assert verify_backup(snapshot, expected_sha256=report.sha256) == report
restore_operational_backup(
    snapshot,
    Path("/absolute/private/restore-test/operational-restored.sqlite3"),
    expected_sha256=report.sha256,
)
```

Do not run this example against actual paths without confirming the storage
root, disk headroom, permissions and operator approval. Save and separately
protect the SHA-256 from the source backup. An offline restore is intended as
a separate verification artifact, never as an in-place replacement or a way
to bypass original receipt/pin verification. The active collection database
must never be used directly as the read-only web snapshot.

Before accepting a backup in operations, verify source capture receipts,
ingestion provenance and the recovery plan's existing guards independently.
A database integrity check alone does not establish provenance.
