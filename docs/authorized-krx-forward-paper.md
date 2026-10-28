# Authorized KRX forward observations to PAPER

## Compressed capture processing and offline recovery (#149)

The recorder retains exact streamed response bytes, including gzip compression.
Parsing now decodes `Content-Encoding: gzip` separately, with both retained and
decoded bodies bounded by the reviewed response budget (at most 2 MiB). Corrupt
headers/CRC, truncated gzip, unsupported encodings, oversized decoded content and
invalid JSON fail closed. No magic-byte fallback silently changes an unlabelled
body into JSON. Standard uncompressed JSON remains supported.

New response metadata records `body_representation=wire` or `httpx_decoded`.
The latter covers already-consumed HTTPX responses, principally in-memory test
responses; these must not be decompressed twice. The approved real HTTPS path uses
streamed wire bytes. New manifests identify `KRXProvider-daily-unadjusted-v2`.
Both normal ingestion and cleaned-source verification use the same bounded decode
boundary and existing whole-market KRX parser. Only the exact approved ticker is
normalized, and its provenance continues to reference the original compressed raw
receipt. No raw source bytes or source hashes are replaced with decoded bytes.

Missing representation metadata deliberately retains original v1 parsing semantics.
In particular, an already sealed gzip capture that failed JSON parsing remains a
failed original ingestion with its exact original manifest hash. It is not silently
upgraded when a newer application reads it.

Use a separate, private, canonical output directory for a derived recovery artifact:

```sh
dsv forward-capture recover --root "$CAPTURE_ROOT" \
  --plan "$PLAN_JSON" --plan-pin "$TRUSTED_PLAN_PIN" \
  --attempt "$ORIGINAL_ATTEMPT" --pin "$TRUSTED_CAPTURE_PIN" \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root "$PRIVATE_PACKAGE" --authorization-pin "$TRUSTED_POLICY_PIN" \
  --output "$PRIVATE_DERIVED_ROOT/recovered-observation.json"
```

The output parent must already exist with 0700 permissions, outside the original
capture and policy directories. The command creates a new 0600 file exclusively;
it never overwrites an output. Network opt-in/confirmation flags are rejected.
No HTTP client, signal, broker, source-archive writer or new market DB is used.

Recovery first verifies the original capture pin, receipt/raw SHA-256, semantic
projection and original operator-policy binding at the retained response instant.
This permits read-only verification after expiry, not renewed collection permission.
It requires one complete HTTP 200 response without transport errors. Only then does
it decode and validate every market row and require exactly one scoped observation.
An unmarked legacy gzip source must actually contain valid compressed bytes; a
guessed or ambiguous representation is not a reason to bypass decoding checks.

The immutable `krx_capture_recovery_v1` document contains source capture/plan/policy
references, receipt identity, original ingestion/error state, encoded/decoded sizes,
market-row count and the single normalized `DailyBar` observation. It is a derived
dataset artifact, **not a successful original ingestion**, and never updates the
old pilot DB. Its canonical `FrozenJSON.identifier` is the separately retained
recovery pin. No rendering time is added to its identity. Repeated recovery of the
same verified owners produces identical bytes and hash.

`storage.krx_capture_recovery.verify_recovery` requires separately trusted source
and recovery pins and reconstructs the artifact from the original source owners;
self-rehashed altered observations or provenance fail semantic verification.
Keep the private source, authorization package and trusted pins available for replay.
Independent authorization, PIT, signal/PAPER and execution eligibility remain false.
No private provider response belongs in Git or CI; regression fixtures are synthetic.

### Verified read-only observation query (#150)

Inspect an existing artifact without regenerating it or importing it into a market DB:

```sh
dsv forward-capture inspect-recovery --root "$CAPTURE_ROOT" \
  --plan "$PLAN_JSON" --plan-pin "$TRUSTED_PLAN_PIN" \
  --attempt "$ORIGINAL_ATTEMPT" --pin "$TRUSTED_CAPTURE_PIN" \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root "$PRIVATE_PACKAGE" --authorization-pin "$TRUSTED_POLICY_PIN" \
  --recovery "$EXISTING_RECOVERY_JSON" --recovery-pin "$TRUSTED_RECOVERY_PIN"
```

Keep the recovery pin independently retained, as with the original source pins.
The command reads at most 32 KiB of recovery JSON and invokes the existing
`verify_recovery` semantic reconstruction before emitting any observation. Missing
files, invalid JSON/UTF-8, wrong pins, modified original evidence and self-rehashed
alterations fail closed. It requires the original operator policy; verification at
the original capture instant does not renew expired collection permission.
Network opt-in/confirmation and output-file flags are rejected. All reads are local;
there is no capture, artifact writer, journal, signal, broker or training DB operation.

The canonical `krx_recovered_observation_query_v1` response includes `basDd`, one
`DailyBar` observation with its actual OHLCV/trading value, provider, market,
adjustment semantics and UTC collection timestamp. It also includes original
ingestion/error state, derived recovery status, parser versions and verified origin.
The allowlisted provenance identifiers are the capture attempt and receipt ID,
scoped to the operator-supplied archive. They do not replace trusted pins. No policy
pin, credential, source/content hash or filesystem path is serialized. The query
does not print the complete recovery document or whole-market provider response.

An empty original ingestion list with a parse error remains an unsuccessful
original ingestion, even though a derived observation can now be inspected.
Legacy parser-v1 evidence and new parser-v2 evidence retain their distinct original
states. Real HTTPS origin and synthetic mock origin remain distinguishable.
`pit_qualified`, `forward_signal_eligible`, `paper_execution_eligible` and
`execution_authorized` remain false. A local capture/collection timestamp is not
an independently authenticated exchange publication timestamp. This read-only
summary grants no historical PIT, signal, PAPER or brokerage eligibility.

The authorized-provider integration below connects original forward capture,
SMA signals and paper execution under their separate eligibility requirements;
the read-only recovery query does not enable that path. The integration does not
issue KRX permissions, authenticate
historical publication times, enable brokerage, approve a strategy, or change fees,
risk checks or SMA semantics. PAPER fills remain explicit simulations even when their
input observations were genuinely received over verified KRX HTTPS.

## Policy review before implementation (PR #146)

The minimum adjustment is an explicit **operator_read_only_pilot_v1** policy for a
personally operated, private one-request KOSPI/005930 capture. It does not replace the
default **independent_review_v1** policy or enable signals/PAPER from weaker evidence.

| Category | Requirement and authority |
|---|---|
| Official KRX | Issued AUTH_KEY, separately approved service and its validity, applicable terms, service specification, quotas and licensing |
| Project independent policy | Original-document Ed25519 review, external reviewer registry pin, reviewer/operator separation and credential binding |
| Existing safety | Explicit network opt-in, endpoint allowlist, verified TLS, bounded recorder, archive accounting, raw receipts, manifests, original source integrity, split/PIT/OOS/holdout guards |

The old policy requires the independent reviewer even for personal read-only capture.
The new **separately selected** policy trusts the identified local operator's review of
original provider permission, terms and service-specification bytes. Their hashes and
exact service, credential, scope, archive, one attempt and short validity are bound to
a separately retained owner policy pin. It also requires explicit operator
confirmation at execution. A local approval-reference string or a key alone remains
insufficient. The application verifies integrity, scope and limits; it cannot authenticate
an arbitrary portal export's issuer or understand arbitrary PDFs. The owner must actually
review the originals. This weaker, declared trust basis is never labelled independent.

The pilot is bounded to one request, no retries, one symbol, a maximum 30-second
execution, 2 MiB response, daily archive limit one and a maximum 24-hour policy window.
The result is read-only provider observation evidence. Source receipt validation stays
unchanged, but signal/PAPER, custody, PIT and operational authorization remain false.
The independent path still validates every signature and active/revoked reviewer key.
There is no automatic fallback from failed independent verification.

Threats: incorrect or malicious operator attestations cannot be ruled out by local
hashes; a machine owner can replace local files. Therefore this policy cannot establish
independent authorization, historical availability or downstream trading eligibility.
Changed originals, wrong credential/service/endpoint/scope, expired policy and budget
increases must fail before HTTP. Keep policy pins outside the mutable package. Existing
archive locking/limits are local controls, not a distributed or deletion-proof quota.

Migration: previous signed grants, normalized plan identities, raw manifests and sealed
journals retain their exact meaning. New operator policies use a different document
version/file, explicit CLI selection, readiness/result versions and capture binding.
They cannot be supplied to the existing signed signal/PAPER verification path.

Official pages inspected without a data-API call on 2026-10-26:

- [KRX usage procedure](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp): key application and separate service application/administrator approval.
- [KRX terms](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp): agreement, key care, validity, noncommercial use, per-key 10,000/day maximum, source attribution, third-party restrictions and termination conditions. The displayed effective date is 2025-12-26. These are not blanket permission for redistribution or continued use after expiry.
- [KOSPI daily-trading service](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC): service identity and AUTH_KEY header were visible; production request URL/method were not exposed in retrieved text. No sample TEST or data request was executed. The repository GET/endpoint remains allowlisted; retain the original official development specification before an actual pilot.

These public pages do not authenticate the operator's account-specific approval. The
existing user-confirmed daily-trading (2026-09-29 through 2027-09-28) and basic-information
(2026-10-16 through 2027-10-15) approval history remains real existing history. Earlier
retained HTTP 403 error pages remain failed evidence, not terms. No missing document or
project reviewer pin is described as proof that KRX approval itself is absent.

## Personal read-only pilot: operator checklist

This is optional and explicitly weaker than independent review. Keep the default signed
path for signals/PAPER or whenever an independent review is required. Do not switch modes
to evade a revoked reviewer, failed signature, expired service or missing original records.
An operator policy cannot reinterpret, migrate or approve an old signed grant/capture.

1. Restore the actual original account/service permission and its approved dates; retain
   the exact applicable terms and official development specification. Review the account,
   service, credential association, GET/endpoint, private noncommercial use and restrictions.
   A key, the previous scope JSON, a screenshot filename, an error page or this guide is
   insufficient. Generic documents cannot be machine-authenticated by this application.
2. Verify the already approved research split and its independently retained plan pin.
   Never change a split to relabel final-test/OOS/holdout data. Select one completed,
   unprotected KOSPI/005930 observation date within 1–7 calendar days. This read-only
   recent-bar capture is not contemporaneous historical availability or a forward signal.
3. Review all traffic using this key and confirm quota headroom; stop other clients for
   the pilot. The local per-archive counter cannot inspect other programs or enforce the
   exchange's aggregate per-key quota. No online quota/account lookup is performed here.
4. Prepare a private 0700 non-symlink package with exact `permission.bin`, `terms.bin`,
   `specification.bin` (each 32 bytes–2 MiB) and canonical `operator-policy.json` (<=32 KiB).
   Do not reconstruct originals from parsed web-tool output or synthetic fixtures.
5. Bind the actual credential privately using the existing domain-separated digest below,
   without printing it. Keep the policy pin separately from the mutable package. A new
   credential, scope, attempt or archive needs a fresh explicit owner review/pin.
6. Run readiness/dry-run with explicit policy selection. Neither command contacts KRX.
   Only after every prerequisite and separately authorized one-request proposal is met,
   supply both network opt-in and operator confirmation. No pilot was executed for #146.

`operator-policy.json` has exactly these fields (placeholders are **not evidence**):

```json
{
  "version": "krx_operator_read_only_pilot_v1",
  "operator_id": "IDENTIFIED_OWNER",
  "acknowledgement": "private_operator_review_not_independent_authorization",
  "reviewed_at": "ACTUAL_OWNER_REVIEW_UTC",
  "valid_from": "EXPLICIT_PILOT_START_UTC",
  "valid_until": "EXPLICIT_PILOT_END_UTC_EXCLUSIVE",
  "approved_from": "ORIGINAL_APPROVAL_DATE",
  "approved_through": "ORIGINAL_APPROVAL_DATE_INCLUSIVE",
  "service_id": "stk_bydd_trd",
  "method": "GET",
  "permission": {"issuer": "KRX", "source_url": "OFFICIAL_REFERENCE", "sha256": "ORIGINAL_BYTES_SHA256"},
  "terms": {"issuer": "KRX", "source_url": "OFFICIAL_REFERENCE", "sha256": "ORIGINAL_BYTES_SHA256"},
  "specification": {"issuer": "KRX", "source_url": "OFFICIAL_REFERENCE", "sha256": "ORIGINAL_BYTES_SHA256"},
  "captures": [{"plan": "FULL_UNBOUND_PLAN_OBJECT", "attempt": "pilot-1"}],
  "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
  "capture_root": "ABSOLUTE_CANONICAL_NEW_PRIVATE_ARCHIVE",
  "transport": "provider_https",
  "document_origin": "operator_supplied_provider_originals",
  "max_observation_lag_days": 7,
  "credential_sha256": "PRIVATE_DOMAIN_SEPARATED_CREDENTIAL_BINDING",
  "uses": {"private_capture": true, "forward_signal": false, "paper": false}
}
```

The full unbound plan is the original `forward_capture_plan_v1` object with both
reference fields null, `mode=authorized_provider`, KOSPI/005930, `max_requests=1`,
`daily_limit=1`, `max_seconds<=30`, original byte limit <=2 MiB and all original split,
spacing and concurrency constraints. At most one plan/attempt is accepted. Fill actual
plan references with the canonical policy `.identifier` and original terms SHA-256,
just as the signed path avoids circular hashing. The supplied `--authorization-pin` is
the separately retained canonical policy fingerprint, not an automatically trusted report
hash. No signing key, registry or reviewer identity is required **for this policy only**.

All instants use canonical UTC `+00:00`. The pilot interval is `[valid_from,valid_until)`,
review occurs within it and it spans at most 24 hours. These are explicit project pilot
instants, not fabricated exchange approval timestamps. `approved_from/through` are the
actual civil approval dates and are checked against KRX/Seoul dates, not stored as UTC
midnight events. Both the requested observation and pilot window must fall within the
stated service approval. Existing key binding, direct TLS, real clocks, expiry checks,
time/byte limits, durable receipt handling and source replay are reused.

Exact non-network commands:

```sh
dsv forward-capture authorization-readiness --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt pilot-1 \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root PRIVATE_PACKAGE --authorization-pin OWNER_POLICY_PIN

dsv forward-capture dry-run --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt pilot-1 \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root PRIVATE_PACKAGE --authorization-pin OWNER_POLICY_PIN
```

The separate `krx_operator_capture_readiness_v1` JSON reports owner-policy integrity,
credential binding, blockers and `ready_for_explicit_read_only_capture`; it always
reports `independent_authorization_verified=false`, `forward_signal_eligible=false`,
`paper_execution_eligible=false`, `pit_qualified=false` and `network_requests=0`.
Missing originals remain blocked even though existing KRX approval history is acknowledged.
A reviewer registry supplied to this mode is rejected, so it cannot accidentally replace
independent trust. `document_origin=synthetic_test_fixture` requires `transport=mock_fixture`
and cannot be used by the live CLI. Test fixtures are never original permission.

**One-request proposal only, not executed:** one GET for the reviewed `basDd` against the
allowlisted KOSPI daily endpoint, one original response preserved privately, no retries,
30-second maximum, <=2 MiB, one archive request/day, no signal or PAPER action. Review
and explicitly authorize the concrete date, plan, policy pin and all-key quota first.
Only then would the execution command be:

```sh
dsv forward-capture capture --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt pilot-1 \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root PRIVATE_PACKAGE --authorization-pin OWNER_POLICY_PIN \
  --allow-network --confirm-operator-authorization
```

The result is `operator_read_only_capture_result_v1` and the original manifest v1. The
new immutable `krx_operator_read_only_capture_binding_v1` records lower trust explicitly.
Original `verify/history/readback` commands still verify source bytes/manifests; the
Python `verify_operator_capture_authorization(...)` additionally verifies the original
policy binding at the original retained UTC time. It performs no request. Completed
capture retry requires the original capture pin and cannot repeat HTTP. Preserve the
archive, policy and original pins; local deletion/replacement is outside crash/durable
quota guarantees. Do not feed these receipts into the independent signal/PAPER path:
it rejects the operator policy/binding. No automatic trust upgrade is implemented.

## Current evidence and boundaries

PR #145 baseline: `5c2980f810a2651871a3f494cafa50482c661748`. Policy-review base: `f138f16a5343395331f7b2409d64e17170748538`, with #145 squash-merged and identical implementation tree.

| Boundary | Existing implementation | Preserved independent-review path |
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
Restore originals from the prior review. For the independent signal/PAPER path, also
restore its reviewer trust root or arrange the explicit independent review below.
For the personal read-only policy, use the original-document owner checklist above. Do not infer a trusted pin
from a report's own hash or generate a self-approved registry to fill the gap.
No live KRX request was performed and no prior private evidence was modified.

## Independent review procedure (preserved default)

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

## First real read-only pilot readiness (#147)

### Scope separation audit (#148)

Before this change, `ForwardCapturePlan.__post_init__` required an
`ExplicitResearchSplit`, its exact pin, and training/tuning membership for every
capture. The retained historical training/tuning windows end in 2023/2024. They
cannot represent a recent completed observation within the operator policy's
one-to-seven-day limit. Extending those frozen windows would change research
evidence and is not an acceptable collection configuration.

The additive v2 plan below separates prospective read-only collection from research
membership. Existing v1 documents, hashes and training/tuning validation retain
their meaning. No research split is generated, extended or reclassified.

`forward_capture_plan_v2` replaces the v1 `partition`, `split`, and `split_pin`
wire fields with `observation_scope`. It is deliberately limited to the existing
`operator_read_only_pilot_v1` policy: KOSPI, 005930, one request, one archive request
per day, at most 30 seconds and 2 MiB. The scope version is
`prospective_read_only_observation_v1`; its UTC `valid_from` and `valid_until` must
span at most 24 hours and match the original reviewed operator policy exactly.
The explicit observation date must be a weekday, precede the scope's start date
in Asia/Seoul, and be at most seven calendar days old. The policy's possibly tighter
lag and actual approval period remain authoritative at capture time. Weekday/lag
checks do not prove that an exchange session occurred or that data is published.
The operator must separately review the completed session, original documents,
quota headroom and the response's instrument representation.

The scope does not automatically approve a recent date. Its exact date, window,
request budgets and source identity enter the unbound plan hash, existing policy,
final plan pin and original capture identity. A different date/window needs a new
explicit review and pins. On real HTTPS, the existing verifier rechecks actual UTC
time and replaces caller-supplied fixture clocks. An old scope/policy cannot be
backdated to request protected historical dates. Readback verifies the original
capture at its retained timestamps; it does not authorize a new request.

For a reviewed collection-only plan, construct the existing owner explicitly:

```python
from datetime import date, datetime
from donghak_stock_vision.data.forward_capture import (
    ForwardCapturePlan, ForwardObservationScope,
)

# These three values come from the concrete operator review, not a synthetic fixture.
scope = ForwardObservationScope(
    datetime.fromisoformat(reviewed_valid_from_utc),
    datetime.fromisoformat(reviewed_valid_until_utc),
)
unbound = ForwardCapturePlan(
    market="KOSPI", ticker="005930", day=date.fromisoformat(reviewed_completed_date),
    partition="forward_observation", split=None, split_pin=None,
    max_requests=1, max_response_bytes=2 * 1024 * 1024, max_seconds=30,
    interval_seconds=1, daily_limit=1, mode="authorized_provider",
    authorization_reference=None, terms_reference=None, observation_scope=scope,
)
```

This creates no research split. Do not convert an old v1 plan by renaming its
partition: v2 rejects mixed research/scope fields and v1 still rejects final-test,
OOS and holdout partitions. Recent collection uses only a fresh, explicitly reviewed
pilot archive; it never reads or reclassifies historical databases or partitions.
Retain the known historical split pin unchanged:
`6d4fa516227f13640cdb785f029359134d51b074468be543654c8873f9296150`.

Then follow the existing private original-document package, credential binding and
canonical policy/plan pin procedure below. The operator policy wire version is
unchanged; its `captures[0].plan` contains the v2 unbound document. Keep the policy
and scope validity windows identical. Use only the existing non-network commands:

```sh
dsv forward-capture authorization-readiness --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt REVIEWED_ATTEMPT \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root PRIVATE_PACKAGE --authorization-pin OWNER_POLICY_PIN
dsv forward-capture dry-run --root PRIVATE_CAPTURE_ROOT \
  --plan PLAN.json --plan-pin TRUSTED_PLAN_PIN --attempt REVIEWED_ATTEMPT \
  --authorization-policy operator_read_only_pilot_v1 \
  --authorization-root PRIVATE_PACKAGE --authorization-pin OWNER_POLICY_PIN
```

For v2, readiness also exposes `observation_scope` and `research_partition: null`.
Require empty blockers, verified policy integrity and credential binding, but do not
interpret them as independent authorization, authenticated publication, custody,
PIT/OOS, signals or PAPER approval. The default independent-review path rejects
v2 rather than reinterpreting old signed grants. Signal requests and historical PIT
assessment reject this read-only scope before reading source evidence; PAPER cannot
obtain an eligible signal through it. No new execution path or broker is introduced.

Existing archive/manifest, receipt, readback and custody plumbing remains shared.
The plan's new content hash changes the new capture identity, while existing v1
plan, journal and manifest hashes remain unchanged. Local custody staging still
does not constitute external submission or independent custody. This PR executes
no real capture and creates no private production authorization package. Separate
explicit approval of the actual pinned one-request plan remains necessary.

This is a preparation step, **not a completed collection**. Base main for this audit is
`85672b2e2ace6ea008f363d21054fe0abc90d3e0` (#146 merged). No KRX data API, sample TEST,
broker or external custody request was made. The next collection step requires a
separately authorized single real request; further synthetic development is not a
substitute for locating the original documents below.

### Confirmed prerequisites and remaining material

Inspection on 2026-10-26 confirmed existing local approval-reference records and
credential presence only. Known daily-trading approval is 2026-09-29 through
2027-09-28; basic-information approval is a different service and does not authorize
`stk_bydd_trd`. This acknowledges the operator's approval history, without presenting
local records as provider-authenticated permission. Existing private scope records,
raw 403 receipts and prior contract-review records were preserved. No usable original
permission/terms/specification or operator-policy package was found in the scoped
repository private evidence. The earlier `/tmp` security-pilot report is still absent.
Do not publish original documents, account identifiers, document hashes or credentials.

| Required original | Exact expected source and purpose | Current finding |
|---|---|---|
| Service permission | Operator's original KRX My Page / API key issuance history and approved service application record, or original KRX approval notification; identify the daily-trading service, account/key association and approved dates privately | Existing operator-confirmed history; original export/notification not located |
| Applicable terms | [Official Korean terms](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp); preserve the actual successful original response/export, review applicability to this account and private retention | Public text reviewed; original applicable successful bytes not in the private package |
| Developer specification | “Development specification download” on the [official KOSPI daily service page](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC); retain the original download and review method, host/path, parameters, outputs and all-market scope | Download document not obtained; exact original download URL unavailable in retrieved text |
| Scope and policy | Existing `forward_capture_plan_v1` and `operator-policy.json`, reviewed private originals, credential binding and separately retained policy/plan pins | No production package created; blocked until originals and concrete scope are reviewed |

The public [usage procedure](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp)
confirms separate key and service approval. The official service page identifies KOSPI
daily trading and describes market-listed equity coverage from 2010-01-04; AUTH_KEY
header instructions are visible. The table and sample URL/method/parameters are empty
in retrieved text. A direct documentation-only GET returned HTTP 403. No bypass,
repeated direct download, sample TEST or endpoint experiment was attempted. That error
body is not a specification or terms acceptance.

Consequently the following remain **repository implementation assumptions awaiting the
original official specification**, not newly verified provider guarantees:

- GET `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`.
- `AUTH_KEY` header; `basDd` formatted `YYYYMMDD` (header instruction independently visible).
- JSON object with `OutBlock_1` array. Existing parser requires string `ISU_CD` short
  code, `BAS_DD`, `MKT_NM`, `TDD_OPNPRC`, `TDD_HGPRC`, `TDD_LWPRC`, `TDD_CLSPRC`,
  `ACC_TRDVOL`, `ACC_TRDVAL`, and validates exact KRW/share integers and OHLC relations.
- Market-wide daily response rather than a server-side 005930 filter. No undocumented
  symbol parameter is added. Verify exact instrument-code, market and date semantics
  from the original specification before approving this mapping.

Published terms currently state noncommercial use, at most 10,000 requests/key/day,
source attribution, no provision to third parties, and no continued use after contract
termination. No more precise service-specific rate or retention duration was verified.
Do not turn these into indefinite retention/redistribution permission. Review any
service-specific conditions and how expiry affects private archives before collection.
The project's one-request/day archive limit is much tighter but cannot account for
traffic from other clients sharing the key.

### Capture audit and scoped correction

The original collector already preserves full response bytes before parsing, uses
`follow_redirects=False`, verified direct TLS and no environment proxies, and records
request/response instants in UTC. The operator policy permits one request and zero
retries. The existing CLI constructs a default HTTPX transport with zero connection
retries. This audit also closes two caller-injection gaps: provider capture now rejects
client-level default query parameters/cookies and Host/Cookie/Authorization/Proxy-Authorization
headers, and real transport preflight rejects
nonzero internal connection retries. A reviewed request must not acquire hidden scope
parameters, session data or retry attempts from an injected client.

Offline synthetic full-market tests verify that valid unrelated rows are preserved in
the original private raw response but **only 005930** is ingested/projected. An invalid
unrelated row invalidates the complete page; missing/duplicate Samsung rows, malformed
fields, wrong dates/markets or invalid numeric values cannot produce a successful
scoped bar. Unexpected responses are retained as failed evidence, not silently accepted
as empty success. A committed failure can be inspected without another HTTP request.
The complete market page may contain unrelated records: keep it private and ensure the
original permission permits retention; do not pretend only Samsung bytes were collected.

Original `forward_capture_manifest_v1`, receipts, policy and independent-review
contracts are unchanged. Completed capture reuse requires its separately retained pin
and never repeats HTTP. Unknown interrupted request outcomes must not be automatically
retried: inspect the original status/receipts and seek separate operator review.
The archive request gate is durable locally, not a distributed or deletion-proof limit.

### Canonical plan and policy-pin preparation (offline)

After reviewing actual originals, prepare the private 0700 package using the existing
checklist and exact operator schema above. Select a recent **actually completed session**
only at authorization time, within 1–7 calendar days and the existing approved unprotected
training/tuning split. A weekday alone does not establish a trading session. Do not
fabricate a split to reach protected data. Do not preselect a date in this guide.

The policy contains the complete **unbound** plan, with both references null. Its
canonical `FrozenJSON.identifier` is the policy pin. The bound plan replaces only
`authorization_reference` with that pin and `terms_reference` with the reviewed original
terms hash; its own canonical identifier is the plan pin. These are canonical evidence
fingerprints, not `sha256sum` of pretty-printed JSON. Retain both pins outside the mutable
package. Changing originals, credential, scope, date, attempt, limits or archive root
requires a fresh explicit review; never automatically rehash changed evidence as approved.

This offline Python example derives a bound plan **from an already reviewed package**.
It does not create permission, invent missing fields, choose a date or generate a grant.
Set `DSV_PILOT_PACKAGE` and `DSV_PILOT_PINS` to actual private absolute paths. The latter
is an existing separate 0700 directory containing the independently retained
`operator-policy.pin`. Never copy a pin from an untrusted report as the trust root.

```sh
python - <<'PY'
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from dotenv import load_dotenv
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.storage.krx_authorization import (
    bounded, operator_readiness, verify_operator_authorization,
)

package = Path(os.environ['DSV_PILOT_PACKAGE'])
pins = Path(os.environ['DSV_PILOT_PINS'])
assert all(p.is_absolute() and p.is_dir() and not p.is_symlink()
           and p.stat().st_mode & 0o077 == 0 for p in (package, pins))
assert package.resolve() != pins.resolve()
policy = FrozenJSON(bounded(package / 'operator-policy.json', 32768).decode())
assert policy.identifier == (pins / 'operator-policy.pin').read_text().strip()
p = policy.to_dict()
assert len(p['captures']) == 1
c = p['captures'][0]
unbound = ForwardCapturePlan.from_dict(c['plan'])
assert unbound.authorization_reference is None and unbound.terms_reference is None
plan = replace(unbound, authorization_reference=policy.identifier,
               terms_reference=p['terms']['sha256'])
reference = FrozenJSON.freeze({'root': str(package), 'grant_pin': policy.identifier})
now = datetime.now(UTC)
verify_operator_authorization(plan, c['attempt'], reference, None, now)
load_dotenv(Path.cwd() / '.env', override=False)
key = os.environ.get('KRX_API_KEY', '')
readiness = operator_readiness(plan, c['attempt'], reference, None, now, bool(key.strip()),
                              credential=key, capture_root=Path(p['capture_root']))
assert readiness.to_dict()['ready_for_explicit_read_only_capture'], 'Readiness blocked'
for path, content in ((package / 'plan.json', plan.document.payload_json),
                      (pins / 'plan.pin', plan.document.identifier + '\n')):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
print('Offline package verified; plan written. No network request performed.')
PY
```

Exclusive creation prevents overwriting an earlier plan/pin. This preparation snippet is
not a transactional multi-file store: if interrupted, inspect existing files and compare
their canonical identities before continuing. Do not collect on a partially prepared
package. Original permission/terms/specification bytes remain unmodified.

Then use the actual reviewed attempt and archive root with the existing non-network
`authorization-readiness` and `dry-run` commands shown above. Keep their JSON private.
Exit code 0 means the assessment was produced, **not** that all requirements passed:
check `blockers=[]`, policy/credential binding, endpoint, basDd, budgets and
`ready_for_explicit_read_only_capture=true`. Independent authorization, signal/PAPER,
PIT and live-order eligibility must remain false. The verifier enforces local owner
review; locating a policy cannot authenticate an arbitrary document's issuer.

### Single-request proposal, stop rules and readback

Only after the developer contract and originals are reviewed, ask the operator to
separately authorize the exact plan pin, policy pin, date, attempt and private archive.
Propose **one GET**, concurrency one, no retries/redirects, <=30 seconds, <=2 MiB,
archive daily limit one, read-only/no signal/no PAPER/no real orders. The existing
execution interface requires both `--allow-network` and
`--confirm-operator-authorization`; this task does not execute it or create a valid
production package from placeholders.

Stop on every missing original/pin, expiry, wrong endpoint/service/credential/split,
quota uncertainty, non-200 response (including 403), parse error, byte/time budget or
interruption. Do not try another date, endpoint, key or attempt automatically. The
collector may commit a failed manifest; inspect response completeness, HTTP status,
parse error and `ingestion`, rather than calling any manifest a successful capture.

After a future authorized capture, retain the original manifest pin privately. Existing
commands below are all local; replace the uppercase arguments with the actual reviewed
values. `readback` creates a new 0600 file and refuses overwrite.

```sh
dsv forward-capture status --root PRIVATE_CAPTURE_ROOT
dsv forward-capture verify --root PRIVATE_CAPTURE_ROOT --plan PLAN.json \
  --plan-pin TRUSTED_PLAN_PIN --attempt REVIEWED_ATTEMPT --pin TRUSTED_CAPTURE_PIN
dsv forward-capture history --root PRIVATE_CAPTURE_ROOT --plan PLAN.json \
  --plan-pin TRUSTED_PLAN_PIN --attempt REVIEWED_ATTEMPT --pin TRUSTED_CAPTURE_PIN
dsv forward-capture readback --root PRIVATE_CAPTURE_ROOT --plan PLAN.json \
  --plan-pin TRUSTED_PLAN_PIN --attempt REVIEWED_ATTEMPT --pin TRUSTED_CAPTURE_PIN \
  --sequence 0 --output NEW_PRIVATE_READBACK_FILE
```

Also use the existing `verify_operator_capture_authorization(...)` interface at the
original retained capture instant to verify policy/source binding. Passing a later time
after policy expiry is not a renewal of authorization. Original raw receipt integrity
and local HTTPS origin do not establish exchange publication time, independent custody,
PIT safety or downstream signal/PAPER eligibility. No collection is marked complete in
this preparation PR. **First real pilot status: blocked on original documents and
verified developer contract; live KRX data requests: zero.**
