# Read-only KRX scheduler diagnostic HTTP endpoint

Endpoint: `GET /api/v1/operations/krx-scheduler/diagnostics`

The HTTP endpoint reuses the pure `summarize(backlog, preflight)` projection
introduced in PR #164. The application does **not** load a local scheduler
configuration, discover operator evidence, connect to an operational SQLite
database, enable collection, or contact KRX when serving this endpoint.

An embedding application must explicitly inject
`krx_diagnostics_reports: Callable[[], tuple[Mapping[str, Any], Mapping[str, Any]]]`
into `create_app`. The provider supplies independently approved and
sanitized report mappings that follow the existing backlog and preflight
contracts. A missing provider returns HTTP 503; no implicit local fallback is
available. The endpoint returns the bounded diagnostic summary, including
nonauthorization indicators, and does not return credentials, source file
paths, capture receipts, or policy fingerprints.

This opt-in API transport is not evidence of live operator validation. Before
exposing it to a browser or remote network, implement an operator-reviewed
offline report export, provenance/expiry controls, and access restrictions.
Do not provide unreviewed market data to research, AI signals, or PAPER orders.
