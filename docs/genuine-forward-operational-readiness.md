# Phase 6 — Genuine-forward operational readiness (offline)

The `genuine_forward_operational_readiness_v1` report is a **read-only,
fail-closed operator assessment**, not a provider activation tool or performance
approval. It composes existing #221–#223 source/prediction custody replay,
outcome binding, v1 evaluation consumer, prospective custody audit and optional
#217 configured trust-anchor governance. #216 cohort and #218 optimization
contracts remain intact and are not replaced.

## Execute and independently recheck

The input is the existing, pinned
`prediction_outcome_e2e_input_v1` manifest documented in
[prediction-outcome-e2e-rehearsal.md](prediction-outcome-e2e-rehearsal.md).
Use **previously retained original owners and independently retained pins**.
A freshly rehashed manipulated manifest is not an authenticated authority.

```sh
python -m donghak_stock_vision.genuine_forward_readiness_cli \
  --input /offline/evidence/e2e-input.json \
  --input-pin "$RETAINED_INPUT_PIN" \
  --as-of 2025-07-12T01:00:03+00:00 > /offline/reports/readiness.json

python -m donghak_stock_vision.genuine_forward_readiness_cli \
  --input /offline/evidence/e2e-input.json \
  --input-pin "$RETAINED_INPUT_PIN" \
  --as-of 2025-07-12T01:00:03+00:00 \
  --verify-report /offline/reports/readiness.json \
  --report-pin "$RETAINED_READINESS_REPORT_PIN"
```

An existing pinned governance snapshot can optionally be inspected using
`--anchor /offline/evidence/anchor.json --anchor-pin "$RETAINED_ANCHOR_PIN"
--issuer synthetic_configured_issuer --key-id "$EXPECTED_KEY_ID"`.
These are examples, **not** authorized issuers. All four arguments must be
provided together. A configured key, even if within its declared window,
is not an independently authenticated real-world issuer. The current
governance contract cannot prove independent historical revocation.

The report and JSON pins are deterministic for unchanged owners and `as_of`.
Store output and its pin separately; stdout is the only output operation.
Verification always replays the original owners and rejects rehashed forged
summaries. Failure exit status is 2. No network, cloud, credentials, signing,
database migration, capture, upload or broker API is invoked.

## Reading the decision

- `verified_local`: an original owner and its local integrity/semantic replay
  pass existing validation. This is **not** independent publication or custody.
- `verified_independent`: reserved for future actual independent verification;
  **never emitted** by this offline-only implementation.
- `unavailable`: a separate owner or future observation is absent or pending,
  or this CLI intentionally does not assess that operational subsystem.
- `blocked`: authority, source rights, independent chronology, or permission
  cannot be established. Do not attempt the prohibited operation.
- `not_applicable`: conceptual vocabulary only; no unsupported status is
  synthesized for supplied evidence.

`overall` stays `blocked` regardless of the successful offline software
replay. `offline_software_readiness=verified_local` means the supplied
offline workflow's original owners replay successfully, **not** that all
production software or on-premises services have been tested. A pending
outcome is not treated as success. An invalid original fails closed.

`capabilities` records the proof class and retained verification pins.
`missing_evidence_and_blockers` explicitly names external deficiencies.
`phase7` distinguishes offline engineering allowed now from operations
requiring separate authorization. All genuine-forward, PIT, performance and
trading flags are false. Metrics are null; structural v1 `evaluated` is not
predictive accuracy.

## Evidence retention and trust boundaries

Retain original market response bytes, immutable capture receipts and manifests,
prediction journal head and entry, strategy digest and parameters, frozen horizon,
independently retained owner/reference pins, observation raw responses, all
original signing/custody returns, and applicable historical issuer/key/revocation
owners. Retain at least the original evidence lifetime required by actual
contractual rights; this report establishes no retention permission.

Existing `prospective_operational_readiness.assess_operational_evidence` can
verify specific capture/custody owners, and `krx_readiness.preflight` covers an
operational scheduler/credential/storage environment. They are **separate,
unchanged validators**; this offline manifest does not contain their original
source-grant, credential, request budget or operational SQLite owners. Therefore
this report does not invent success or failure for actual installed credentials
or provider approval. Missing owners are reported as not assessed, rather than
assumed absent. Protected fixture observations do not establish real KRX rights.

For independent genuine-forward evidence, still obtain authoritative predeclared
policy and prediction existence *before* the exact target date; an independently
authenticated timestamp; source issuer/legal scope; separately retained external
custody; historical key rotation and revocation completeness; and original
future publication/availability evidence. A matching local clock, SHA-256, or
valid configured signature is not sufficient.

## Phase 7 controlled launch checklist

### Phase 7-A — authorized KRX acquisition

Offline engineering of the existing collection, storage and bounded request
workflow is permitted. Before any live call, an operator must independently
review the **original** KRX API grant and endpoint/market/usage rights; provision
credentials through approved existing private mechanisms; verify request quota,
market session/calendar, archive retention, bounded retries and original storage
health. Run the repository's existing KRX operational preflight and preserve the
result. Only after separate explicit approval may a bounded live smoke test be
authorized. Stop for missing/expired source permissions, unknown quota, wrong
scope, unexpected ingestion state, unauthorized endpoint or uncertain retry.

### Phase 7-B — persisted real data and read-only web research

Engineering can proceed offline. Before claiming *real-data analytical
readiness*, verify original persisted KRX source/receipt evidence, market
calendar, corporate action treatment, actual training/validation/test coverage,
point-in-time availability, eligible cohort and the existing backend read-only
API/web integration. An API route or synthetic bar alone is insufficient.
Stop if observations, permissions or provenance cannot be matched to their
original owners.

### Phase 7-C — genuine prospective forecast and outcome

Engineering can proceed offline. Before any genuine-forward evaluation, obtain
independently authenticated original predeclaration, immutable prediction
existence and external custody before target, original timely later observation,
eligible research cohort and separately approved prospective performance policy.
Review historical key/revocation/issuer independence with actual external
authority. Stop for retrospective backfill, forged local timestamps, mismatched
ticker/window, unknown external custody or expired/revoked authority. Never
authorize brokerage execution based on this readiness report.

This milestone can be delivered and tested while all live operations and
independent-evidence claims remain **BLOCKED**.

## Tests

```sh
pytest -q tests/architecture/test_boundaries.py
pytest -q tests/integration/test_genuine_forward_readiness.py \
  tests/integration/test_prediction_outcome_e2e.py \
  tests/integration/test_prospective_operational_readiness.py \
  tests/integration/test_prospective_anchor_governance.py
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

The existing GitHub CI checks Python 3.12, 3.13 and 3.14, installed wheel,
regression and packaging gates. Protected-source SHA-256 audits and complete
network-free verification must be independently executed and recorded before
calling the milestone Done. Never change the legacy v1/v2/v3 report contracts,
architecture checks or protected data to force a pass.
