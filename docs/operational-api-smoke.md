# Offline operational API smoke check

After an operator separately approves and completes the manual snapshot publishing
and full verification procedure from `docs/operational-snapshot-publishing.md`,
the operator can verify the read-only FastAPI contract against that already
published copy:

```sh
python scripts/smoke-operational-api.py \
  --snapshot /absolute/private/krx-2026-10-29.snapshot.sqlite3 \
  --sha256 <independently-reviewed-lowercase-sha256>
```

The command uses FastAPI's in-process TestClient and never opens a listening
port or invokes the KRX provider. It requires the existing SHA-256-pinned,
read-only `.snapshot.sqlite3` contract. It checks the health route, snapshot
summary, first catalog page, first catalog ticker OHLCV (when nonempty), and
that the unrelated research stock route remains unavailable.

The smoke check is deliberately **not** a replacement for the complete
catalog/OHLCV verifier. Successful output establishes only sampled API
transport consistency, not source authenticity, complete market coverage,
research eligibility, prospective signal validity, or PAPER authorization.

Output contains bounded summary counts, date range and explicit
`evidenceStatus: unreviewed`. It does not print private snapshot paths,
credentials, raw market rows or authorization material. A failed check
must not trigger an automatic retry, snapshot overwrite or collection.
