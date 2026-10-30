# KRX operations read-only diagnostics

This CLI combines the existing KRX scheduler backlog and production preflight.
It does not perform a provider request, enable a LaunchAgent, register a capture,
modify policy or authorize PAPER trading.

Use an independently reviewed, private scheduler configuration and fingerprint:

```sh
dsv krx-scheduler diagnose \
  --config /absolute/private/scheduler/config.json \
  --config-pin <reviewed-sha256>
```

The JSON report shows the latest persisted trading day, observed row count,
due/expired/reserved sessions, stored observations without independently
verified history, pending capture, capacity state, preflight blockers and
readiness booleans. A readiness boolean is informational only; operator
authorization and the full existing policy still govern any actual request.

Diagnostic status prioritizes pending uncertainty, unresolved source evidence,
expired sessions, blocked preflight and due sessions. Do not interpret an
absence of due dates as proof of source completeness.

The command rejects all execution, scheduling, network-opt-in and application
flags. It does not expose private file paths, credentials, capture receipts
or grant pins in the summarized output. This PR uses fixture tests only; a
real operator configuration must not be passed to CI.
