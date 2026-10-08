# Baseline Open DART evidence

## Scope

This evidence-only extension retains the locked 993-security universe, parent
package, models, quality classifications, cost schedule and runner unchanged.
Required security coverage is 2024-06-14 through 2025-07-07. Signal anchors remain
2024-07-01 through 2025-06-30; neither warmup nor tail creates signals. No real
prediction, training, signal generation, simulation or performance calculation
was executed. This exposed historical period is not an untouched final test.

The [manifest](evidence/baseline-dart/artifacts/dart-evidence-manifest.json) and
[integrated evidence](evidence/baseline-dart/artifacts/integrated-official-evidence.json)
reference the immutable parent from [official evidence](baseline-official-evidence.md).
They supplement it without overwriting previous evidence. Readiness is **NO**.

## Official API semantics

Official Financial Supervisory Service documentation:

- [Corporation codes](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018):
  `GET /api/corpCode.xml`, a ZIP containing corporate code, official stock code,
  corporate name and modification date. Modification date describes corporate
  overview updates, not a security identity effective interval or listing date.
- [Disclosure search](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001):
  `GET /api/list.json`, receipt-date filters, `page_no`, `page_count` up to 100,
  `total_page`, `total_count`. Without corporation code the period is limited to
  three months. `last_reprt_at=N` includes submitted correction filings.
- [Original documents](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003):
  `GET /api/document.xml`, original document ZIP by receipt number.
- [Major event API catalogue](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS005):
  separate capital increase/reduction, merger, division and exchange/transfer APIs.
  These extracted reports are not a complete domestic listing/suspension register.
  Overseas delisting APIs do not prove domestic KOSPI delisting.

All categories were retained in the 871 resolved-corporation disclosure queries;
no title or category filter removed filings from the preserved search responses.
Each query covers 2024-06-14 through 2025-07-07. Fourteen additional all-corporation
`I003` (exchange market actions) monthly queries cover June 2024 through July 2025,
including unresolved or delisted issuers. The small surrounding date extension
is evidence context only, not an extension of strategy anchors or execution.

All 885 queries completed their advertised pagination: 1,187 response pages,
64,981 unique receipts and zero conflicting duplicate receipt contents. Nine
queries returned official `013` (no matching data). A complete empty filing query
is not proof of no relevant economic event. Filings before the requested receipt
window can have effects inside it, and corrections may be submitted later.
Report-title annotations identify review candidates; they do not establish a
correction-to-original relationship or cancellation/effectiveness by themselves.

## Credential handling and acquisition

The existing `.env` key was read only into the acquisition client. Neither key,
credential-bearing URL, response request headers nor sensitive exception text is
serialized. Only a canonical endpoint, non-secret parameters and
`credential_used=true` are retained. Logging is suppressed around sensitive HTTP
calls, including child library loggers. Secret echoes in response bytes or ZIP
members are rejected before preservation. No redirects carry credentials.

Sequential DART requests use a minimum 0.35-second delay. Transport failures and
HTTP 429/5xx have bounded deterministic retries; official authorization/quota
errors stop immediately. There were no retry or rate-limit events in this run.
Cached bytes are hash-verified and never silently overwritten.

Preserved acquisition receipts (excluding search-engine discovery calls):

| Requests | Count |
| --- | ---: |
| DART corporation ZIP | 1 |
| DART disclosure pages | 1,187 |
| DART original documents | 8 |
| Official API/public support documents | 11 |
| KIND viewers | 131 |
| KIND document locators | 286 |
| KRX identity probes and guide | 3 |
| Total | 1,626 |
| HTTP 200 | 1,623 |
| Failed HTTP | 3 |

The KRX Open API host identity probe returned 404; the existing provider's
`data-dbg.krx.co.kr` identity endpoint returned 401; direct developer-guide
retrieval returned 403. These are preserved as failures, not absence/no-event
results. No access restriction was bypassed. The documented service is
[KRX KOSPI security basic information](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj),
which exposes security/short codes and listing dates. Separate service access may
be required; a working OHLC key does not establish authorization for this service.
No OHLC endpoint was requested.

## Identity coverage

[Corporation mapping](evidence/baseline-dart/artifacts/dart-corporation-mapping.json)
contains every one of the 993 approved tickers. Exact unique official stock-code
matches establish current source mapping for **871/993 (87.71%)**. The other 122
remain unresolved. Delisted issuers may retain stock codes in the current ZIP;
this status does not certify current listing or tradability. Preferred shares,
names and five-character KIND issuer codes are not heuristically mapped.

Full-required-period historical security continuity is **0/993**. Three exact
listing boundaries are verified separately below. A boundary is not a blanket
historical interval or proof that no later security transformation occurred.

## Corporate actions and cross-source links

Preserved queries identify 3,108 title-based review documents across their whole
scope, of which 1,648 have a stock-code field in the approved universe. These are
**documents awaiting review**, not verified economic event counts. No automatic
no-event finding, effective-date assertion or price adjustment is made.
Complete identity/event-class/effective-date coverage remains **0/993**; verified
corporate-economic-event count remains null. The source rows and receipt dates
are retained in [the review queue](evidence/baseline-dart/artifacts/corporate-action-events.json).

The previous 418 KIND schedule rows / 133 KOSPI rows / 131 references remain
protected. All 131 viewer references were inspected, yielding 286 main-document
locators including correction/related-document selections. Twelve locators from
12 distinct KIND references match receipt IDs in the DART search corpus. The
remaining 119 KIND references have no established match in that corpus.
A locator match proves a document-reference connection only, not historical
security continuity or economic effectiveness. The machine-readable
[cross-reference results](evidence/baseline-dart/artifacts/kind-dart-cross-reference.json)
retain unresolved links explicitly. No source is automatically preferred.

## Missing-session investigation

The source of truth is the previous calendar artifact: 46 securities with 8,554
missing ticker-sessions. Every security remains in
[the reconciliation](evidence/baseline-dart/artifacts/missing-session-explanations.json),
with relevant disclosure review references when available.

Three official KRX/KIND listing notices prove exact security codes and listing
boundaries:

| Security | Listing date | Explained pre-listing sessions | Official source |
| --- | --- | ---: | --- |
| 062040 | 2024-07-29 | 31 | [KRX listing notice](https://kind.krx.co.kr/external/2024/07/25/000747/20240725001545/68152.htm) |
| 031210 | 2025-03-14 | 180 | [KRX new-listing price notice](https://kind.krx.co.kr/external/2025/03/13/001171/20250313002833/99364.htm) |
| 483650 | 2025-05-22 | 225 | [KRX listing notice](https://kind.krx.co.kr/external/2025/05/20/000533/20250520001324/68152.htm) |

Reviewed literal source excerpts, raw hashes, filing dates, listing effective
dates and exact security identifiers are pinned in the lifecycle review. Only
pre-listing dates in the required range are explained. No post-listing continuity
is inferred. Total explained: **436/8,554**. Remaining: **8,118 across 43 securities**.

Eight original DART documents were inspected for the missing-security review.
Examples of evidence deliberately not overpromoted: a planned KOSDAQ-to-KOSPI
transfer is not a final KOSPI listing confirmation; intraday/overnight important
announcement suspensions do not explain later months of missing observations;
a delisting decision or resumed liquidation-trading procedure does not itself
prove the actual final delisting date. No lack of bars was treated as suspension.
The three verified effective dates above are listing lifecycle dates, not a claim
of complete corporate-action effective-date coverage.

## Reproduction and integrity

Offline replay requires no credential or network access:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.verify_dart \
  --root . \
  --evidence docs/evidence/baseline-dart \
  --output /tmp/baseline-dart-replay
```

The output directory must not exist. Replay checks original protected file SHAs,
raw response hashes, query identity, every page and reviewed source excerpts.
It does not open either database for writing or deserialize estimators.

The opt-in acquisition/resume command below reuses verified cached pages; uncached
requests use the root `.env`. It does not recreate the initial corporation ZIP:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.collect_dart \
  --root . --evidence docs/evidence/baseline-dart
```

Raw and support sources are content-addressed independently of local paths,
retrieval time or retries. Gzip preserves original response bytes. JSON escapes
source-language strings; authored code and documentation are English. Artifacts
are immutable `FrozenJSON` normalizations or canonical JSON plus SHA-256.
The source inventory and parent-package pin participate in the final manifest.

All 142 protected DB/model/frozen/parent-evidence files retained their original
SHA-256. No production source data, labels, models or prior evidence was changed.
Secret scanning across preserved bytes and ZIP members found no configured key.

## Remaining blockers

- `BLOCKED_ON_CALENDAR_EVIDENCE`: 8,118 security-session absences still need official
  listing, delisting, suspension or identity-transition evidence.
- `BLOCKED_ON_CORPORATE_ACTION_EVIDENCE`: full-period exact-security identity,
  complete event-class scope, correction handling and actual effective dates are
  not proven for all 993 securities.
- DART credential access is verified; `BLOCKED_ON_DART_CREDENTIAL` is resolved.
- KRX security-basic-information service access is denied (401). Enabling that
  service may supply identity/listing evidence, but is not by itself proof of full
  historical lifecycle or corporate-action coverage.

Exchange-wide calendar and settlement verification remain unchanged. No policy
weakening is proposed. **READY FOR BASELINE RUN INPUT FREEZE: NO**.

## Fingerprints

| Artifact | Semantic SHA-256 |
| --- | --- |
| corporate-action-events | `478d9b393683183dc4ee78247b003a07c2f8c36687c25b7f1f351daa5c5c5ffd` |
| dart-corporation-mapping | `b9db52ec3ce75e4dc27e481cb682e2893e5d15000d289a81a43e22a1b82f904d` |
| dart-evidence-manifest | `4118ba0c91fb9cd7e5c6f037c4b81677ff3272871210c429b654730790014d86` |
| filing-query-coverage | `5cd11f69440e597ffac85d07b36605e3da62ed53809474b305a5b14815f5136b` |
| historical-security-mapping | `ca68217713683c7698a7f5c3057576b3c3312422a919214a9b9deccfbef28264` |
| integrated-official-evidence | `a40255faaa6f52aa7fbef9a70b6e8b6b9df24ec13757f6457b5957269d3dc4d6` |
| kind-dart-cross-reference | `144496a1d2952d150fef121179e48347c74076198f1bcc6ca389c4f30fc31e46` |
| missing-session-explanations | `159c702cea8d1a1a8b74ebc0b42d8c5e1128a498e71aceac2629fb601012a170` |
| security-lifecycle | `743625626d7aba59c6211acbde05689d1a525c20aeb8bbad743791fb2cef4a1c` |
| source-inventory | `c6b7825597d35da7a06e941237b1088b293dc7fb903a1897557c7732d590eaaf` |

## Validation results

- New offline evidence tests: 31 passed.
- All evidence-related tests: 122 passed.
- Full offline suite: 1,126 passed, one live test deselected.
- Ruff, format check, mypy, local documentation links and `git diff --check`: passed.
- Ten published artifacts reproduced byte-for-byte in a separate directory.
- All 142 protected files retained their pre-task SHA-256.
- Configured DART/KRX secrets were absent from saved bytes and ZIP members.

The suite reported one joblib physical-core discovery warning and used its logical
core fallback. No test failures were skipped or assertions weakened. Existing
synthetic tests may exercise model/simulation fixtures; production evidence
verification does not invoke those paths. No commit, push, PR or merge was made.
