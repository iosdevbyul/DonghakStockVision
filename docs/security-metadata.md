# Security metadata v1

Corporations issue securities; their names are different facts. This display-only
contract separates those identities without changing existing research data,
PIT features, historical identity, trading, Operations or publication policies.
The initial implementation intentionally does not fill missing security names.
The [investigation](security-metadata-investigation.md) explains the evidence gap.

## Compatibility

Existing ResearchAPISnapshot, its exporter, deterministic bytes/hash, and
StockSummary/StockDetail are unchanged. `/api/v1/stocks`, `/stocks/{ticker}` and
`/stocks/{ticker}/ohlcv` keep their original keys, nullable fields and semantics.
In particular `name` remains the stripped **English corporation name** from one
exact DART `stock_code` match with `CURRENT_IDENTITY_VERIFIED`; it is not silently
replaced by a Korean security name, alias or candidate. Stock count remains 993,
legacy named count 871 and missing count 122 on the committed baseline universe.

Metadata uses separate optional endpoints, so existing iOS/Web decoders see no
new fields. Existing API dependencies/protocols remain compatible. No iOS or Web
source is modified. Optional API startup arguments only enable the new reader;
they do not alter Operations settings, research snapshots or publications.

## Data contract

The new envelope has `artifact` and `fingerprint`; artifact version is
`security_metadata_v1`. Collections are immutable tuples, uniquely identified and
sorted. All tickers are strings matching `[0-9A-Z]{6}`; leading zeros and uppercase
suffixes survive validation and JSON round trips. No suffix arithmetic, name
matching or common/preferred ticker-family join is performed.

| Model | Fields and meaning |
|---|---|
| CorporationMetadata | `corporationId` is namespaced `DART:<8-digit corp_code>`; `koreanName`, nullable `englishName`, explicit `nameLanguages`, source modification date and evidence reference |
| SecurityMetadata | `ticker`, nullable Korean `securityName` and `securityNameEnglish`, nullable `corporationId`, relationship verification/evidence, `securityType` and separate name/type evidence references |
| EvidenceReference | publisher, official source URL, raw SHA-256, derived artifact fingerprint, document reference and nullable verification time |
| HistoricalBoundaryReference | exact ticker/security identity and dated boundary from existing evidence, explicitly not current-name/full-period evidence |
| SecurityMetadataSnapshot | source references, corporations, securities, historical boundaries, limitations, research snapshot SHA-256 and legacy-stock fingerprint |

The conceptual `corporationName` is exposed as `corporation.koreanName` and
`corporation.englishName` in detail responses, with language explicit.
`securityName` always denotes the Korean security name, not a corporation name.
No fallback between those fields is allowed. Empty/missing English DART names
remain null, preserving the existing exporter semantics.

Current source-snapshot corporation verification does not establish the name
as of today or within a historical price period. DART `modify_date` is exposed
only as `sourceModifiedDate`; it is neither acquisition/verification time nor
an effective date. Existing source evidence does not record an independently
verified observation time, so `verifiedAsOf` stays null, with
`source_verification_time_not_recorded` on source references and security reasons.
No filesystem mtime, current clock or research-bar date substitutes for it.

`market=KOSPI` comes from the research snapshot scope; it does not assert current
listing. `historicalIdentityVerified`, `pitVerified` and
`operationalQualificationChanged` remain false. Historical boundary evidence
never populates current security-name/type fields.

## Supported evidence and fail-closed compilation

`build_metadata(root, research)` uses only existing local sources:

1. Validate the DART mapping envelope fingerprint and raw corpCode ZIP hash.
2. Re-run the existing exact DART mapping function for the complete ordered
   research universe and compare the entire resulting mapping. Arbitrarily
   rehashing a modified derived corporation name or relationship cannot pass.
3. Require the legacy stock names to match the existing English-name projection.
4. Derive corporation records and explicit relationships only from unique exact
   DART matches. Ambiguous/unmapped securities retain null relationships.
5. Validate historical boundary artifact fingerprint/parent mapping, supported
   scope, official host and referenced original decompressed bytes' SHA-256.
   Retain boundary references separately, without extracting current names.

The initial compiler has no official current security master adapter. All current
security names remain null and types `unknown`. The typed schema can express
known name/type facts only with evidence references, but the v1 reader will reject
any values the supported compiler cannot reproduce. A candidate name, inferred
preferred-share type, or borrowed parent corporation reference is insufficient.
A future reviewed official adapter requires its own evidence/temporal contract;
this change does not grant a general approval to external data supplied in JSON.

Evidence hashes establish integrity relative to supplied local evidence, not
independent official authenticity or new quality approvals. Original DART/KIND
artifacts and approvals are never rewritten. Current-source DART mapping
qualification remains the existing qualification; no historical identity or
corporate-action coverage is upgraded.

## Canonical output and replay

The envelope fingerprint is SHA-256 over the artifact using the existing
`data.learning.canonical` encoding: sorted object keys, compact separators,
UTF-8 Unicode and no NaN. The envelope is canonical JSON plus one final newline.
It is a different hash domain from ResearchAPISnapshot's existing bytes/hash;
that original contract is unchanged. No runtime timestamps, random values,
absolute paths or filesystem mtimes are inserted into canonical metadata.

`SecurityMetadataReader.from_path` requires an independently configured expected
artifact fingerprint, checks it against the envelope and recomputed artifact,
then recompiles from the declared local original sources and research snapshot.
Any mismatch fails startup. Sorted duplicate tickers/IDs, missing references,
unsupported name/type claims, missing raw files and source corruption fail closed.
At app creation the legacy-stock fingerprint must match the configured research
reader. Evidence is loaded/replayed only at startup; requests use indexed frozen
objects, never a market DB, network call or model load.

## Optional read-only API

- `GET /api/v1/security-metadata`: full bounded envelope and fingerprint.
- `GET /api/v1/security-metadata/{ticker}`: security, nullable corporation,
  relevant source/boundary references and the full envelope fingerprint.

Unconfigured endpoints return 503; unknown tickers return 404; malformed tickers
return 422. Existing stock routes remain usable without a metadata reader. All
routes are GET-only. There is no new refresh, acquisition or mutation endpoint.

Offline diagnostics (reads a saved Research Snapshot, not its source DB):

```sh
python -m donghak_stock_vision.security_metadata_cli \
  --root . --snapshot .data/research-api-snapshot.json
```

The result contains exact missing tickers/reason codes, legacy name coverage,
current security-name/type coverage and separate boundary-only counts.
To create a new metadata envelope (existing output paths are refused):

```sh
python -m donghak_stock_vision.security_metadata_cli \
  --root . --snapshot .data/research-api-snapshot.json \
  --output /tmp/security-metadata.json
```

Optional API configuration:

```sh
dsv-api --snapshot .data/research-api-snapshot.json \
  --security-metadata /tmp/security-metadata.json \
  --security-metadata-fingerprint <reported-artifact-fingerprint> \
  --metadata-evidence-root .
```

The optional metadata path/fingerprint must be supplied together. Neither command
collects official data, writes existing inputs or changes operational settings.

## Tests and follow-up

Tests use isolated synthetic market bars and read-only committed evidence. They
reproduce 993/871/122, exact relationships, suffix preservation, unknown types,
source replay, duplicate rejection, rehashed fabricated facts, source corruption,
API compatibility, canonical JSON and export non-overwrite behavior. Existing
Research/OHLCV/Operations/publication tests remain part of regression validation.

A follow-up can propose a bounded official KRX security-master acquisition. Before
any call, it must identify endpoints, request count/date scope, raw storage and
hashes, official name/type fields, corporation-link proof and independent source
observation/effective dates, and obtain the user's approval. It must reconcile the
historical universe (including delisted securities) separately from a current
listing master. The 30 supplied names remain candidates, not evidence.
