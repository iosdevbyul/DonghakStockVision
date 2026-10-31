# Synthetic KRW 10 million PAPER account genesis

`open_krw_10m_account(account_id, at, policy)` provides a deterministic,
in-memory starting point with exactly 10,000,000 KRW in available cash,
zero reserved cash, no positions, no orders and trading disabled.

It reuses the existing `DeterministicPaperBroker`, `AccountSnapshot`,
`FixturePolicy` and `PaperFrame`. Opening timestamps must be timezone-aware
UTC. The account identity, amount and opening timestamp are pinned into a
deterministic genesis fingerprint. The caller supplies the existing reviewed
synthetic risk and mechanics policy.

This is **not** a funded real account, durable journal, authorized KRX
execution, verified performance history or permission to consume unreviewed
operational data. The existing `forward_paper_journal` and authorization
boundaries remain unchanged. Persistent equity observations, cost basis and
realized/unrealized P&L require separate accounting and replay work.
