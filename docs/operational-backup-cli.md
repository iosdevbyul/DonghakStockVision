# Operational SQLite offline backup CLI

The existing `dsv market-storage` command now exposes explicit, offline-only
backup, fingerprint verification and separate restore. It does not run a
scheduler, contact KRX, change eligibility, or create PAPER trades.

All source and destination paths must be absolute; parent directories must
exist. Run only against an operator-approved source after checking permissions,
disk space, capture provenance and authorization. No operator database is read
by tests or CI.

```sh
dsv market-storage backup --source /private/source/operational.sqlite3 --destination /private/backups/daily.snapshot.sqlite3
dsv market-storage verify-backup --source /private/backups/daily.snapshot.sqlite3 --expected-sha256 <reviewed-lowercase-sha256>
dsv market-storage restore-backup --source /private/backups/daily.snapshot.sqlite3 --destination /private/recovery/restore.sqlite3 --expected-sha256 <reviewed-lowercase-sha256>
```

The backup operation prints a JSON report including its SHA-256, byte size and
row counts. Store that hash separately and require it for later verification
and restore. The restore command requires a new destination, never overwrites a
running operational database, and performs an independent verification. This
is not an automated backup service and must not be scheduled without separate
operator approval.

The source identity, eligibility boundaries and read-only snapshot publication
requirements remain governed by the existing operational backup and API contracts.
