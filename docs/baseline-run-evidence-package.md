# Baseline historical run evidence package

## Purpose and approved range

This is an evidence package, **not a backtest or a run-input freeze**. The first
baseline's approved signal anchors are **2024-07-01 through 2025-06-30 inclusive**.
This interval has been exposed in previous research. It is not an untouched final
test. Unbiased confirmation requires a separate future holdout. No prediction,
model deserialization, fitting, signal, trade intent or performance computation is
part of this verification.

The approved 993-security universe is retained, including securities without
observations. The original KRX / KOSPI / unadjusted data is not repaired or adjusted.
The approved research exclusions remain 2024-03-28 and 2025-03-21. These are actual
trading dates excluded for incomplete research data, **not exchange holidays**.

## Files and identity

- [Approval and independent pins](evidence/baseline-run/approval.json)
- [Official source inventory](evidence/baseline-run/sources.json)
- [Machine-readable package](baseline-run-evidence-package.json)
- [Existing evidence checks](baseline-run-evidence.md)
- [Locked quality policy](baseline-quality-policy.md)

`AnchorRange` has an identity separate from derived boundaries. Only the exact
approved range passes its approval check. `BaselineRunEvidencePackage` is immutable
and hashes canonical JSON containing the checks, dataset identity, boundaries,
source content identities and model envelope pin. Audit retrieval timestamps and
absolute workspace paths are outside semantic identity. Source documents are
preserved as deterministic gzip (`mtime=0`) of original, unmodified HTTP response
bytes; both compressed-file and original-byte SHA-256 values are recorded. These
external documents retain their source language; project-authored material is English.
A source URL or a hash alone does not establish coverage or authenticity approval.

The package command deliberately reports the currently available, incomplete
inventory. It cannot promote a landing page into a reviewed calendar. Generic
reviewed-calendar and corporate-coverage validators are available for a subsequent
review of complete source evidence; synthetic tests are never production approval.

## Minimal causal warmup and tail

`features.engine.feature_vector` uses `history[-11:]` for features and
`check_segment(history[-12:])` for the preceding quality edge. Therefore the
complete existing feature/quality contract requires **11 preceding observed
research sessions plus the anchor**: 10 prior bars for feature values and one
additional preceding bar for the quality edge. No labels are read.

The boundary resolver selects exactly these 11 preceding verified research
sessions. It does not add a longer buffer. Missing ticker history is not silently
extended, backfilled or accepted: per-security continuity needs verification.
Warmup cannot generate performance anchors.

For the final anchor, the first following verified research session is the entry
candidate and holding session 1. The following five research sessions determine
maximum execution coverage, including the fifth-session close exit. The final
settlement is the second eligible settlement session after that exit. Research
exclusions affect research sessions, never the exchange settlement calendar.
Tail sessions do not generate signals.

**Exact warmup dates, execution-tail dates and settlement-tail dates are currently
null/unverified.** The official full-date calendar has not been obtained. A weekday
calculation or observed DB rows must not be substituted for verified exchange
sessions. The package does not freeze guessed boundaries.

Existing `baseline_runner` has a shared contract end: it rejects records after that
end and invokes allocation on each processed in-range session. It does not yet
separate the newly approved signal-anchor cutoff from an execution-only tail.
That integration needs a follow-up before a run; no runner or policy code was changed.

## Official sources and actual coverage

Sources were retrieved on 2026-10-05. This audit date is not part of the semantic
package fingerprint.

| Source | Established evidence | Missing evidence |
| --- | --- | --- |
| [KRX general procedures](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp) | Cash-equity settlement at T+2; no trading/settlement on listed holiday categories | Complete historical day classification and historical rule-effectivity review |
| [KRX yearly calendar](https://www.krx.co.kr/contents/MKD/01/0110/01100303/MKD01100303.jsp) | Official 2024/2025 download links | Attachments returned HTTP 200 with zero bytes; no complete session inventory obtained |
| [KIND calendar](https://kind.krx.co.kr/common/marketschedule.do?method=loadInitPage) | Official entry point | Returned landing page is not a dated historical coverage export |
| [DART disclosure guide](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) | Authenticated disclosure-query specification, pagination and date filters | No credential configured; no company/universe query executed |
| [DART security-code guide](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018) | Official company/security mapping API specification | Historical mappings not retrieved or reviewed |

No market-data endpoint was requested. No unofficial portal, inferred weekday
calendar, price movement or absence of price movement is accepted as evidence.

Corporate-action coverage remains **0/993 verified securities**, not zero events.
`returned_events` is null, not an empty no-event claim. Coverage is missing for
splits, reverse splits, stock dividends/bonus issues, rights/ex-rights, mergers,
spin-offs, security identity changes, consolidations, listing terminations and
other mechanical discontinuities throughout warmup, anchors and execution tail.
A complete query must preserve scope, all pages, raw hashes, official event
references and security mappings. An event remains blocked for existing quality
resolution; the evidence layer does not adjust prices or approve an unsafe event.

The generic settlement resolver advances by two explicitly classified settlement
sessions, checks all intervening dates and calls the existing bounded tax schedule.
It never uses calendar-day addition or a zero-tax fallback. Actual mapping coverage
is currently zero because the authoritative historical calendar remains incomplete.

## Actual read-only inspection

The immutable SQLite connection uses `mode=ro&immutable=1` and `query_only=ON`;
nonempty WAL/journal sidecars cause rejection. Source DB integrity is checked.
All protected model, analysis, approval and frozen-manifest bytes are SHA-checked
before and after. SQL reads anchor rows only while exact context/tail dates remain
unverified; this is explicitly **not** a complete run dataset.

Anchor audit: 230,357 source rows, 968 observed tickers, 993 retained universe
members, 240 research dates, one excluded source row. Duplicates, non-increasing
sessions, malformed bars, out-of-range rows, unexpected tickers and metadata
mismatches are zero. The per-ticker inventory preserves 25 unobserved members.
Their absence is not attributed to suspension/delisting without official evidence.

The full source DB has 1,099,255 rows and 993 tickers from 2022-01-03 to 2026-10-01.
The approved source SHA and universe SHA match. This does not prove calendar or
corporate-action completeness. Expected-missing/unexpected-session comparison
cannot be marked passed until a reviewed official calendar exists.

## Model envelope pin

Only `TrustedHGBResolver.verify()` is used, never `.load()` or estimator methods.
The approved Up HGB-7 and Down HGB-15 bytes, configuration, qualification, lock,
feature order, runtime metadata and policy pins are checked. Down remains a
provenance dependency; only Up is executable under the unchanged baseline.

The exact `ohlcv_value_v1` feature order is:
`return_1`, `return_3`, `return_5`, `close_to_sma10`, `volatility_10`,
`range_to_close`, `volume_change_10`, `trading_value_change_10`.
The label remains `reversal_barrier_v1`. Full approved estimator parameters are
copied from existing metadata, not generated or fitted.

All model and policy identities, the complete envelope and file fingerprints are
in the machine-readable package. Approval/configuration mismatch aborts before a
package can claim readiness. No economic or quality policy was altered.

## Reproduction

From the repository root, using existing protected local inputs:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.package_evidence \
  --root . \
  --approval docs/evidence/baseline-run/approval.json \
  --sources docs/evidence/baseline-run/sources.json \
  > /tmp/baseline-run-evidence-package.json
```

The command is network-free and outputs only to stdout. Use a new output path;
do not overwrite approved/frozen inputs. Compare `package_fingerprint` with the
checked-in report. No absolute path or execution timestamp enters that identity.

Official source retrieval used HTTPS GET to the five inventory URLs. Calendar
attachments were requested using the official page's public download flow:
GET `/contents/COM/GenerateOTP.jspx` with `name=fileDown`, `filetype=att`,
`url=MKD/01/0110/01100303/mkd01100303_DN`, and
`file_nm=K_Trading_Calendar2024.doc` or `K_Trading_Calendar2025.pdf`, then POST the
returned opaque download code to `https://file.krx.co.kr/download.jspx`.
Both attachment responses were empty; no attachment content hash or verified
calendar was fabricated. No API secret was required or logged for this flow.
Source bytes are replayed from local gzip evidence, not re-fetched during verification.

## Readiness and next evidence

**READY FOR BASELINE RUN INPUT FREEZE: NO**

Resolved: `BLOCKED_ON_RUN_RANGE`, `BLOCKED_ON_ARTIFACT_IDENTITY`.
Remaining:

1. `BLOCKED_ON_CALENDAR_EVIDENCE`: complete, officially supported civil-date
   classification, including special closures, year-end closures, exact warmup
   and tail; per-security missing-session explanations.
2. `BLOCKED_ON_CORPORATE_ACTION_EVIDENCE`: complete 993-security official event/no-event
   coverage and historical security mapping over the derived input period.
3. `BLOCKED_ON_SETTLEMENT_EVIDENCE`: reviewed effective settlement rule plus complete
   settlement-day mapping through final exit, checked against the locked tax schedule.

The next evidence acquisition should obtain an official KRX calendar export and
complete official corporate-action/security-mapping query results with raw hashes
and pagination/coverage proof. This enables exact boundary derivation and settlement
mapping. Do not relax strategy or quality classification to remove these blockers.
The runner's distinct anchor/tail boundary integration remains a separate pre-run task.

## Verified identities

| Component | Fingerprint / SHA-256 |
| --- | --- |
| Evidence package | `8d67955fef21d4f13c494b954d0497a2e2c90f754eb96d4412046608d3eaa620` |
| Model envelope pin | `be86e0581caa4244c9774acc855ae6d75c4340f2e9517764fea5267674702af6` |
| Backtest anchor contract | `9e04444931a8e076c20fce5dacd2ad31a7b822a6a959f56b4717eac7b61f891b` |
| baseline_policy | `89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8` |
| cost_policy | `c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef` |
| down_fingerprint | `19f4f83a0640227b688e49c64519642498b7751f674ccaab54e8d9a3c0f5f44e` |
| down_sha | `42e3ed40c33b513a9ee066157ceada00b2b44c337259f817875f018e943a6b2c` |
| lock_id | `5d1d04e9607926ed5b89c70b71a36d4852f43d73723fab70e895db4830bb821d` |
| qualification_id | `164748197f6616057040d4f8ae2044ff35b5471d29b2d96512a2efa680ec17ed` |
| quality_policy | `c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c` |
| signal_policy | `7240e945da5cab800e6adb5a62debe71d7e09cce6b0882f02ecac32e26b754c1` |
| up_fingerprint | `81c2e45091a9af58786d7d251f4a19e52b17921032cc19a9e4c01dd580db9a15` |
| up_sha | `43ffe774ce67f70c692a087183153b1cf5da86528ac36f2e2e78305daac29f60` |

## Retrieval commands and coverage caveat

The following read-only requests were used (temporary destinations shown):

```bash
curl --fail --location --max-time 30 \
  'https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp' \
  -o /tmp/dsv-krx-settlement.html
curl --fail --location --max-time 30 \
  'https://www.krx.co.kr/contents/MKD/01/0110/01100303/MKD01100303.jsp' \
  -o /tmp/dsv-krx-calendar.html
```

The annual-download page is a derivatives schedule view. Even a successful
attachment retrieval would need a cash-equity scope review; this page is not
approved as a KOSPI session calendar. The saved KIND and DART responses were
retrieved with `httpx.Client(timeout=30, follow_redirects=True).get(url)` for the
exact inventory URLs, with HTTP errors checked before saving response bytes.
No secret values, authenticated market-data URLs or model executions were involved.

## Validation

The new tests use synthetic reviewed calendars and fake model bytes solely for
hash/provenance verification. Real-input inspection is a separate command above.
The raw official-source files are checked for compressed and decompressed SHA
integrity. The package is regenerated twice from protected local inputs and compared
byte-for-byte and by semantic fingerprint. Full offline tests retain the existing
live-test exclusion; no live KRX test is run.
