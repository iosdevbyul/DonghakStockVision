# Manual Track A: publish and verify an offline KRX snapshot

This workflow requires explicit operator approval before accessing the private
operational SQLite database. No real database was accessed in this PR.

The script `scripts/publish-operational-snapshot.sh` combines existing commands:
(1) an online SQLite **read-only-source** backup into a new file and
(2) an independent SHA-256-pinned catalog and OHLCV verifier.

```sh
bash scripts/publish-operational-snapshot.sh \
  /absolute/private/operational.sqlite3 \
  /absolute/private/krx-2026-10-29.snapshot.sqlite3 \
  940
```

The optional final argument is an operator expectation, not a claim of
exchange completeness. Omitting it checks self-consistency without asserting
the number of tickers. The published snapshot must have a new path ending
in `.snapshot.sqlite3`; existing files cannot be overwritten. The command
prints the verifier JSON only on successful verification.

After verification, independently review and preserve the SHA-256 and keep
the snapshot private. The operator may then manually run the operational-only
API with the exact pinned copy:

```sh
dsv-api --operational-only \
  --operational-snapshot /absolute/private/krx-2026-10-29.snapshot.sqlite3 \
  --operational-snapshot-sha256 <verified-lowercase-sha256> \
  --host 127.0.0.1
```

Use `GET /api/v1/operational/summary` and
`GET /api/v1/operational/stocks?limit=25&offset=0` on loopback to inspect
observed data. Rows remain `unreviewed` and are not eligible for AI research,
signals, backtests or PAPER orders. The script never launches the server,
makes a KRX HTTP request, changes the scheduler or touches original DB contents.

If verification fails after the backup succeeds, the new snapshot remains
for manual investigation; do not treat it as approved, and do not automatically
retry or overwrite it.
