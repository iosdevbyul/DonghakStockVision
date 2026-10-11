# A-2: runtime bearer authentication for HTTPS custody calls

The optional `bearer_token` argument is supported by
`HTTPSCustodyTransport`, `create_approved_https_custody_transport`
and `create_approved_custody_session`. A caller must obtain a provider
credential from an external secret manager or runtime environment and
pass it at invocation time. Tokens are excluded from the transport's
representation and cannot be embedded in endpoint URLs.

When present, the same Authorization Bearer header is passed to PUT
and GET requests; absent tokens preserve the previous behavior.
Malformed tokens are rejected, including whitespace and CRLF.
The adapter does not persist credentials, but callers must still keep
headers and exceptions out of logs and rotate revoked credentials.

A bearer credential authenticates the client to a provider only when
the provider implements that contract. It does not independently
authenticate the returned receipt, verify remote durability, or prove
custodian independence. A-2 remains incomplete.
