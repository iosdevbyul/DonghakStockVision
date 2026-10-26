# Authorized KRX forward observations to PAPER

This workflow closes the provider eligibility gap between original forward capture,
SMA signals and paper execution. It does not issue KRX permissions, authenticate
historical publication times, enable brokerage, approve a strategy, or change fees,
risk checks or SMA semantics. PAPER fills remain explicit simulations even when their
input observations were genuinely received over verified KRX HTTPS.

## Current evidence and boundaries

Verified development base: `5c2980f810a2651871a3f494cafa50482c661748`, including #143/#144.

| Boundary | Existing implementation | This change |
|---|---|---|
| Collection | `ForwardCapturePlan`, original KRX provider, `CaptureHTTP`, raw receipts/manifests | Requires original permission/terms plus scoped independent review before any provider request |
| Permission | Local key and authorization/terms reference strings | Strings/key presence alone remain insufficient; signed review and independent registry pin required |
| Custody | Original export/outbox/readback; local staging does not authenticate custody | Unchanged; provider-use permission is separate from independent custody |
| Signals | Original observed-bar SMA, source parser/DB linkage, pinned signal journal | v2 provider request verifies permission, original capture binding and origin; original v1 output stays compatible |
| PAPER | Original broker preflight/submission/fill checks, immutable action journal | v2 trusted account configuration supplies reviewer trust; original accounting and risk limits unchanged |
| Historical | Retrospective SMA and historical PIT/OOS gates | Unchanged; never a warm-up fallback or substitute source |

The operator confirms that service permission has already been obtained and reviewed.
This implementation does **not** conclude that the account lacks approval. A bounded,
read-only inspection of repository documentation and private records found:

| Located record | Finding | What it establishes |
|---|---|---|
| Private `20261016T040850Z-5c9c387a/user-authorized-scope.json` | Daily-trading approval period 2026-09-29 through 2027-09-28; basic-information period 2026-10-16 through 2027-10-15 | Retained user confirmation, explicitly not a new provider lookup; the old pilot authorized only basic information |
| Local `.env` | `KRX_API_KEY` is present; value never displayed | Credential presence, not independently authenticated service entitlement |
| Private `20261016T040505Z-b29ace6d/authorization-scope.json` | Conditional pilot scope and official terms/service URLs | Operator collection scope, not an original provider-issued permission document |
| Both pilot receipts and original raw responses | Terms and service-page requests returned HTTP 403; `acceptedAsEvidence`/`acceptedAsContract` are false | Locally retained failed-request records, not successfully obtained terms or a usable service contract |
| Private `20261016T083135Z-contract-review-a6ee7b66/contract-review.json` | Basic-information pilot stopped at missing official developer specification; zero data API requests recorded | The exact previous blocker; not evidence that daily-trading account approval was denied |
| Supplied `/tmp/dsv-krx-security-master-phase2-pilot-report.md` | Not present at inspection on 2026-10-26; no matching temporary KRX report located | Restore the original report if available; its absence does not negate existing account approval |

The two preserved error-page bodies are each 1934 bytes and independently match their
original receipt SHA-256
`120f225f7f86042485433a7af2f941736eea2314f56b4417a3c4a219447955c4`.
They must not be repackaged as terms. The September provider guide is a dated historical
investigation, including then-current absence of permission; it is not current account
status. Existing historical raw pages prove local retrieval lineage, not provider
entitlement or applicable usage terms.

No original provider-issued account/service-permission export or successful applicable
terms body, independently trusted reviewer registry/pin, or signed permission review
was located in the inspected repository documentation/private evidence. This is a
specific **verification-material gap**, not an assertion that approval was never obtained.
Supply the originals from the prior review (including its independent reviewer trust
root), or arrange the explicit independent review below. Do not infer a trusted pin
from a report's own hash or generate a self-approved registry to fill the gap.
No live KRX request was performed and no prior private evidence was modified.

## Independent review procedure

Existing operational-model review signatures bind different business contracts and
cannot grant KRX collection permission. Reuse their **existing**
`trusted_reviewer_keys_v1` public-key registry format and Ed25519 verification method;
use a narrowly scoped, domain-separated KRX review because model approval, provider
permission and custody have different meanings. No signing/key-generation/approval CLI
or competing custody database is added. Signature verification uses `cryptography`
from the existing `[api]` extra; base offline commands still work without that extra.

Before enabling an actual request:

1. The operator obtains actual original KRX account/service-permission evidence and
   applicable terms through an authorized source. A key, a remembered approval date,
   this guide, a local reference string or a screenshot filename alone is insufficient.
2. A separately identified trusted reviewer checks the originals against the provider
   account/service, exact endpoint/method/parameters, validity, permitted private
   capture/signal/PAPER use, credential association, frequency and request budgets.
   The reviewer must resolve contradictions and licensing restrictions. Unverifiable
   documents must not be signed as approved. This application does not automatically
   understand arbitrary portal screenshots/PDFs or authenticate their issuer.
3. The reviewer signs the precise canonical review below using an existing independently
   established Ed25519 key. Reviewer and collector/operator identities must differ.
   Deliver the registry's original-file SHA-256 and reviewed grant pin through a
   separately authenticated channel. A key supplied by the review itself is rejected.
   Do not promote a newly generated/self-declared key to independent trust merely
   because its signature verifies.
4. The operator separately verifies those trust roots, stores evidence privately and
   supplies the explicit plan pin, grant pin and registry pin to the existing CLI.
   `--allow-network` is still required for capture. Permissions alone never start a call.

This authenticates a **review by the independently trusted reviewer**, not a KRX-issued
cryptographic signature. Readiness reports state this basis and
`provider_signature_verified=false`. If such a review procedure is inappropriate for
the available evidence, leave the path blocked; do not fabricate a substitute approval.

## Private evidence package and exact contract

Use a private (0700), non-symlink directory:

```text
permission-package/
  permission.bin   # exact original service-permission bytes, <=2 MiB
  terms.bin        # exact applicable terms bytes, <=2 MiB
  grant.json       # canonical review, <=128 KiB
  signature.json   # detached review signature, <=4 KiB
reviewers.json     # original trusted_reviewer_keys_v1 registry, <=64 KiB
```

Never commit originals, credentials, private keys, or real permission packages.
Registry public keys are not API secrets. Keep the credential binding digest private
as part of the grant; CLI output never exposes it or the actual key. The original
capture recorder continues to omit authentication headers from retained metadata.

`grant.json` has exactly these fields:

```json
{
  "version": "krx_forward_permission_review_v1",
  "reviewer": "INDEPENDENT_REVIEWER_ID",
  "operator_id": "COLLECTOR_ID",
  "reviewed_at": "REVIEWED_UTC_INSTANT",
  "valid_from": "ACTUAL_PERMISSION_START_UTC",
  "valid_until": "ACTUAL_PERMISSION_END_UTC_EXCLUSIVE",
  "permission": {"issuer": "KRX", "source_url": "ORIGINAL_OFFICIAL_HTTPS_REFERENCE", "sha256": "ORIGINAL_BYTES_SHA256"},
  "terms": {"issuer": "KRX", "source_url": "ORIGINAL_OFFICIAL_HTTPS_REFERENCE", "sha256": "ORIGINAL_BYTES_SHA256"},
  "captures": [{"plan": "FULL_REVIEW_TEMPLATE_OBJECT", "attempt": "pilot-1"}],
  "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
  "transport": "provider_https",
  "capture_root": "ABSOLUTE_CANONICAL_PRIVATE_CAPTURE_ROOT",
  "max_observation_lag_days": 7,
  "uses": {"private_capture": true, "forward_signal": true, "paper": true},
  "credential_sha256": "PRIVATE_DOMAIN_SEPARATED_CREDENTIAL_BINDING"
}
```

Placeholders are not usable approval evidence. `captures[].plan` is an actual
`forward_capture_plan_v1` object, with `mode=authorized_provider` and both reference
fields null. All split, instrument, date, byte, timeout, spacing, daily-budget and retry
fields are included. At most 32 distinct (normalized plan, attempt) pairs are approved.
After review, the actual capture plan sets `authorization_reference` to the canonical
grant fingerprint and `terms_reference` to the raw terms SHA-256. Normalizing just these
two fields to null avoids a circular plan/grant hash. Every other field must match.
Different attempts, extra retries, archive roots or changed budgets require a new explicit review.
The exact canonical `capture_root` is signed so a new archive cannot reset the reviewed
request budget. Do not delete/replace the archive to reset attempts. This is local
archive-bound accounting, not a distributed request counter or crash-proof external quota.

The credential digest is SHA-256 of the exact bytes
`b"DonghakStockVision:krx-credential-binding:v1\x00" + API_KEY.encode()`.
The reviewer must verify its association with the actual service entitlement through
a private review process; the application does not infer account ownership from a
hash. A different credential is rejected before HTTP. Do not print the key/digest or
send it through logs/public reports. Rotating credentials requires a new review.

All instants are canonical UTC `+00:00`; validity is `[valid_from, valid_until)`.
Review must occur within that interval and before the scoped observation dates.
The original capture policy requires a completed prior local trading date; the new
review additionally limits calendar-day lag to 1–7 days. This is an explicit pilot
freshness limit, not proof of exchange session completeness or historical availability.
Do not insert arbitrary midnight/publication times into these fields.

The signature envelope is:

```json
{"version":"signed_krx_forward_permission_review_v1","keyId":"TRUSTED_KEY_ID","signatureBase64":"DETACHED_ED25519_SIGNATURE"}
```

The reviewer signs
`b"DonghakStockVision:krx-forward-permission-review:v1\x00" + canonical_grant_json_bytes`.
Canonical JSON is the existing `FrozenJSON.freeze(...).payload_json.encode()` contract;
the grant pin is its `.identifier`. Original permission/terms and registry hashes are
SHA-256 of exact original bytes. No document download occurs during verification.

## Readiness and one explicitly approved capture

Use the existing `KRX_API_KEY` private environment setting. It becomes the original
provider's `AUTH_KEY` HTTP header; never pass the secret on the command line.

```sh
dsv forward-capture authorization-readiness --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt pilot-1 \
  --authorization-root PRIVATE_PERMISSION_PACKAGE --authorization-pin TRUSTED_GRANT_PIN \
  --reviewer-registry reviewers.json --reviewer-registry-pin INDEPENDENT_REGISTRY_SHA256 \
  --operator-id COLLECTOR_ID
```

`forward-capture dry-run` with the same arguments performs the same no-network provider
readiness assessment. JSON identifies exact endpoint/`basDd`, all budgets, validity,
permission-review verification, credential binding and missing reasons. These actions
never create a capture DB or start a client request. Missing evidence remains blocked.
`ready_for_explicit_capture` does not remove network opt-in; mock review fixtures have
`live_provider_collection_eligible=false`.

Only after the prerequisites and operator permission are satisfied:

```sh
dsv forward-capture capture --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt pilot-1 \
  --authorization-root PRIVATE_PERMISSION_PACKAGE --authorization-pin TRUSTED_GRANT_PIN \
  --reviewer-registry reviewers.json --reviewer-registry-pin INDEPENDENT_REGISTRY_SHA256 \
  --operator-id COLLECTOR_ID --allow-network
```

This reuses the original KRX endpoint allowlist, GET/`basDd`, streaming byte limit,
sequential daily/spacing gate, timeout and bounded retry recorder. A direct verified
HTTPX TLS transport is required: no MockTransport, disabled TLS, proxy discovery,
custom hooks or redirects. Live capture uses the actual local UTC/monotonic clocks,
so fixture clocks cannot revive expired permission. Permission expiry stops the
recorder, including during a response; original retained failures are not successes.
The current HTTPX 0.28 pool/TLS checks fail closed if its transport internals change.

Retain the returned plan/capture pins independently. New provider captures add a small
immutable authorization binding beside the original raw/manifest tables. Original
manifest v1 hashes/meaning are preserved. Old captures without that original binding
cannot be retroactively declared authorized. The original custody staging/readback
commands remain separate and do not manufacture independent custody or publication.

## Prepare the provider signal request

After each explicitly reviewed capture, build the existing request JSON using the
returned, independently retained source references. The original request fields remain:
`symbol=005930`, `market=KOSPI`, `currency=KRW`, explicit dates, canonical current
`calculated_at`, `fast`, `slow`, policy `forward_local_capture_observed_bars_v1`, explicit
`max_latest_age_seconds`, exact final `database_pin` and ordered `sources` (each actual
plan, plan pin, attempt and capture pin). Use the SHA-256 of the sealed pilot DB.

For this path set `version=forward_signal_request_v2`, `mode=authorized_provider`, and
add only:

```json
{"authorization":{"root":"PRIVATE_PERMISSION_PACKAGE","grant_pin":"TRUSTED_GRANT_PIN"}}
```

Trust is supplied separately, never taken from the request or grant:

```sh
dsv forward-signal dry-run --capture-root PRIVATE_CAPTURE_ROOT \
  --request REQUEST.json --request-pin TRUSTED_REQUEST_PIN \
  --reviewer-registry reviewers.json --reviewer-registry-pin INDEPENDENT_REGISTRY_SHA256 \
  --operator-id COLLECTOR_ID

dsv forward-signal evaluate --capture-root PRIVATE_CAPTURE_ROOT \
  --request REQUEST.json --request-pin TRUSTED_REQUEST_PIN \
  --journal SIGNALS.sqlite3 --journal-pin TRUSTED_SIGNAL_HEAD \
  --reviewer-registry reviewers.json --reviewer-registry-pin INDEPENDENT_REGISTRY_SHA256 \
  --operator-id COLLECTOR_ID
```

For a new signal journal use its original `forward_signal_journal.GENESIS` pin.
Retain the new head. `forward-signal verify` uses the same request/trust flags plus
`--journal`, `--journal-pin` and `--signal-id` to replay the original source and SMA.
BUY/SELL/HOLD and warm-up semantics are unchanged: fast < slow <=30, slow+1 observations
needed. One newly captured bar returns INSUFFICIENT_DATA. No historical DB warm-up or
retrospective output is accepted. New real-origin insertions require a current local
clock, fresh calculation and permission valid at insertion. Old legitimate records
are verified against their original timestamps; replay does not fabricate new signals.

## PAPER execution and inspection

Use the [existing PAPER guide](forward-paper-trading.md) for exact risk, sizing and
fill assumptions. Create a separate explicitly pinned account configuration with
`version=forward_paper_account_v2`, `mode=PAPER_AUTHORIZED_CAPTURE`, all original account,
risk/mechanics fields, and this separately trusted configuration:

```json
{"authorization_trust":{"registry":"reviewers.json","registry_sha256":"INDEPENDENT_REGISTRY_SHA256","operator_id":"COLLECTOR_ID"}}
```

Use `forward_paper_action_v2`, `mode=PAPER_AUTHORIZED_CAPTURE` in the action file.
Keep all original fields: capture root, original v2 signal request/pin, sealed signal
journal/head, signal ID, bounded whole-share quantity, explicit current UTC `at` and
later `fill_at`, simulated session/trading flags and declared synthetic risk daily P&L.
The account pin independently binds trust; the action cannot replace it.

```sh
dsv paper-trade dry-run --account ACCOUNT.json --account-pin TRUSTED_ACCOUNT_PIN \
  --journal PAPER.sqlite3 --journal-pin TRUSTED_PAPER_HEAD --action-file ACTION.json
dsv paper-trade execute --account ACCOUNT.json --account-pin TRUSTED_ACCOUNT_PIN \
  --journal PAPER.sqlite3 --journal-pin TRUSTED_PAPER_HEAD --action-file ACTION.json
dsv paper-trade portfolio --account ACCOUNT.json --account-pin TRUSTED_ACCOUNT_PIN \
  --journal PAPER.sqlite3 --journal-pin CURRENT_PAPER_HEAD
dsv paper-trade verify --account ACCOUNT.json --account-pin TRUSTED_ACCOUNT_PIN \
  --journal PAPER.sqlite3 --journal-pin CURRENT_PAPER_HEAD
```

For a new paper journal use its original GENESIS. Original idempotency, conflict
rejection, transaction rollback and exact-intent `paper-trade reconcile` remain.
New real-origin actions require a current local operation clock; already recorded
identical retries/readbacks use their original evidence rather than executing again.
The Risk Engine and Paper Broker remain authoritative; a HOLD, insufficient data or
preflight denial makes no order. Filled quantity, cash, reservation, fee, tax and
slippage accounting are unchanged. PAPER fills are synthetic assumptions, not KRX fills.

## Offline verification and limitations

Run the executable bounded integration fixtures:

```sh
pytest -q tests/integration/test_krx_forward_authorization.py
```

They use clearly labelled synthetic original permission/terms, a TEST reviewer key and
`transport=mock_fixture`, then the original MockTransport capture/parser/manifests,
SMA/journal, Risk Engine and broker. The real CLI refuses mock reviews for actual
collection. Result origin is always `authorized_provider_mock_fixture`; replacing it
with a real-provider label fails semantic replay. Fixtures demonstrate a two-context
BUY/SELL round trip (10000 → 9915.27 synthetic KRW, zero remaining shares), HOLD and
insufficient-data no-ops, missing/expired/tampered permission rejection, request bounds,
source tampering and full replay. This is not actual KRX authorization or performance.

Original v1 synthetic and sealed journal identities remain compatible. Authorization
uses new v2 request/decision/account/action/result contracts; journal wrappers retain
their original version and validate the original owner-specific result. Raw source
snapshots and reviewer registries must remain independently pinned and available.
Revoked/untrusted keys, changed registry pins, changed originals, missing capture
bindings, changed plans/attempts, future/old bars and forbidden splits fail closed.
No automatic key rotation, evidence migration, approval issuance, public redistribution,
independent custody, PIT qualification, OOS approval, scheduler or brokerage is added.
Local clocks/TLS capture receipts are not independently authenticated exchange
publication timestamps. All existing historical approval gates remain unchanged.
