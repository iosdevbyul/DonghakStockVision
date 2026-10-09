# Baseline security lifecycle evidence

This read-only evidence layer explains the existing 43-security / 8,118-session
unresolved inventory. It does not run inference, generate signals, simulate trades,
change market bars, or approve corporate-action uncertainty. All authored text is
English; preserved official source responses retain their original language.

## Locked scope and parent identity

- Warmup: 2024-06-14 through 2024-06-28 (11 research sessions).
- Signal anchors: 2024-07-01 through 2025-06-30.
- Execution tail: 2025-07-01, 02, 03, 04 and 07.
- Settlement tail: 2025-07-08 and 09.
- Universe: the unchanged 993 securities from the parent evidence.
- Research exclusions remain distinct from exchange holidays.
- The period is not an untouched final test.

The parent [DART reconciliation](evidence/baseline-dart/artifacts/missing-session-explanations.json)
is the sole gap inventory. No gap is recomputed from prices or database rows.
The previously explained 436 sessions for 062040, 031210 and 483650 remain in the
parent. They are not counted again. The new integrated artifact links the parent
DART package and all new semantic artifact fingerprints without overwriting it.

## Official sources and retrieval

Only anonymous official KIND requests were made. No environment credential was
needed. There were 121 direct official HTTP requests, all HTTP 200, with no rate
limit or retry events. Two initial requests returned an application-level error
page; these are preserved but never used as positive evidence. The corrected
form submissions succeeded. Discovery web searches are not verification sources.

- [KIND delisting status](https://kind.krx.co.kr/investwarn/delcompany.do?method=searchDelCompanyMain):
  POST `searchDelCompanySub`, `marketType=1`, 2022-01-01 through 2025-07-07;
  all 23 rows were returned on one page. This is the actual delisting date column,
  not the date a board approved or announced a proposed delisting.
- [KIND listing status](https://kind.krx.co.kr/listinvstg/listingcompany.do?method=searchListingTypeMain):
  POST `searchListingTypeSub`, all listing types and security groups, KOSPI,
  2024-06-14 through 2025-07-07; all 15 rows were returned on one page.
- KIND `companysummary.do / searchCompanySummaryOvrvwDetail`:
  official issuer links expose literal six-digit security codes and ISINs.
  The five-digit issuer key is used only as an opaque official routing parameter;
  it is never padded or transformed into a ticker. Listing dates are cross-checked.
- [KIND disclosure search](https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain):
  all 13 pages / 1,295 results for delisting titles, KOSPI, 2022-01-01 through
  2024-06-13. Nine preferred-share notices provide an exact `A`-prefixed six-digit
  short code and an explicit effective delisting date. Viewer/document locators
  and the original notices are preserved separately. No title alone verifies an event.
- [KIND current suspensions](https://kind.krx.co.kr/investwarn/tradinghaltissue.do?method=searchTradingHaltIssueMain):
  the retrieved page explicitly describes the current 2026-10-09 status. It cannot
  establish a historical bounded suspension interval, so no such interval is approved.

Every response has a compressed original and a JSON receipt under
[evidence sources](evidence/baseline-security-lifecycle/sources/).
Receipts contain the endpoint, exact non-secret parameters, method, status, byte
length and uncompressed SHA-256. Source hashes are checked before normalization.
The directory is named `sources/`, avoiding the repository's generic `raw/` ignore
rule. There are no dependencies on the untracked older DART raw-response cache.

## Meaning and limits of identity

DART current corporation mapping remains 871 / 993; 122 current mappings remain
unresolved. This work adds exact boundary evidence for nine preferred-share codes
among those gaps, without inventing a DART corporation relationship. These are
historical share-class notices, not current corporation mapping evidence.
Whole-required-period historical identity continuity remains 0 / 993. Boundary
identity and full historical continuity are different claims.

A delisting notice identifies the discontinued share class and effective date.
A KOSPI listing boundary explains only pre-listing KOSPI observations; for 034230,
this is a transfer into KOSPI, not a claim that it had never traded on another market.
The three relistings are explicit transitions, not inferred ticker substitutions.
No predecessor prices are joined to a successor security. Company overview name
history is preserved as supporting evidence, not a substitute for full continuity.

## Reconciliation results

| Reason | Securities | Newly explained sessions |
| --- | ---: | ---: |
| DELISTED | 31 | 7,138 |
| NOT_YET_LISTED (KOSPI scope) | 9 | 749 |
| RELISTING_TRANSITION | 3 | 231 |
| TRADING_SUSPENDED | 0 | 0 |
| **Total** | **43** | **8,118** |

Remaining unexplained sessions: **0**. Remaining unresolved securities: **0**.
Every original missing date has its own reason, official source identity, exact
security identity and interval fingerprint in the reconciliation artifact.
Intervals are clipped to the requested gap scope; a listing never explains a
post-listing gap. A delisting decision without a verified effective date is not
accepted. Unknown, open-ended or conflicting evidence remains blocked. Identical
re-delivery is idempotent; different overlapping claims require review.

| Ticker | Official effective date | Explanation | Sessions | Remaining |
| --- | --- | --- | ---: | ---: |
| 000060 | 2023-02-21 | DELISTED | 256 | 0 |
| 000547 | 2023-07-17 | DELISTED | 256 | 0 |
| 000995 | 2023-07-17 | DELISTED | 256 | 0 |
| 001529 | 2022-07-15 | DELISTED | 256 | 0 |
| 001725 | 2024-04-26 | DELISTED | 256 | 0 |
| 001745 | 2023-07-17 | DELISTED | 256 | 0 |
| 001880 | 2024-03-04 | DELISTED | 256 | 0 |
| 002270 | 2022-07-20 | DELISTED | 256 | 0 |
| 003410 | 2024-07-09 | DELISTED | 239 | 0 |
| 004565 | 2023-07-17 | DELISTED | 256 | 0 |
| 007630 | 2022-02-22 | DELISTED | 256 | 0 |
| 008560 | 2023-04-25 | DELISTED | 256 | 0 |
| 009275 | 2022-07-15 | DELISTED | 256 | 0 |
| 010050 | 2023-08-28 | DELISTED | 256 | 0 |
| 010145 | 2023-07-17 | DELISTED | 256 | 0 |
| 012600 | 2025-05-27 | DELISTED | 28 | 0 |
| 015350 | 2022-01-18 | DELISTED | 256 | 0 |
| 015540 | 2023-11-07 | DELISTED | 256 | 0 |
| 016385 | 2022-07-15 | DELISTED | 256 | 0 |
| 030790 | 2024-05-13 | DELISTED | 256 | 0 |
| 034230 | 2024-06-24 | NOT_YET_LISTED | 6 | 0 |
| 034300 | 2025-02-24 | DELISTED | 89 | 0 |
| 064400 | 2025-02-05 | NOT_YET_LISTED | 154 | 0 |
| 068400 | 2024-01-31 | DELISTED | 256 | 0 |
| 079900 | 2024-08-19 | NOT_YET_LISTED | 45 | 0 |
| 096300 | 2023-02-02 | DELISTED | 256 | 0 |
| 101060 | 2022-01-18 | DELISTED | 256 | 0 |
| 115390 | 2024-12-09 | DELISTED | 137 | 0 |
| 138250 | 2022-03-22 | DELISTED | 256 | 0 |
| 153360 | 2022-04-11 | DELISTED | 256 | 0 |
| 155900 | 2023-11-24 | DELISTED | 256 | 0 |
| 168490 | 2025-04-01 | DELISTED | 65 | 0 |
| 282690 | 2024-10-08 | DELISTED | 180 | 0 |
| 380440 | 2024-01-02 | DELISTED | 256 | 0 |
| 415640 | 2024-11-29 | NOT_YET_LISTED | 113 | 0 |
| 462870 | 2024-07-11 | NOT_YET_LISTED | 19 | 0 |
| 475560 | 2024-11-06 | NOT_YET_LISTED | 96 | 0 |
| 480370 | 2025-03-17 | NOT_YET_LISTED | 181 | 0 |
| 481850 | 2024-07-01 | NOT_YET_LISTED | 11 | 0 |
| 484870 | 2024-12-16 | NOT_YET_LISTED | 124 | 0 |
| 487570 | 2024-07-29 | RELISTING_TRANSITION | 31 | 0 |
| 489790 | 2024-09-27 | RELISTING_TRANSITION | 71 | 0 |
| 499790 | 2024-12-23 | RELISTING_TRANSITION | 129 | 0 |

## Corporate-action boundary and readiness

The security-level missing-session calendar blocker is resolved by official
lifecycle evidence. Exchange-wide calendar and settlement evidence are unchanged.
This is evidence reconciliation only: existing runner and quality contracts are
not relaxed, and no execution permission is granted for a discontinued security.

Corporate-action complete coverage is still blocked. Full historical identity,
all relevant event classes, effective dates, complete official query semantics
and no-event claims must be established across all 993 securities. Merger,
relisting and share-class conversion evidence is retained in the lifecycle event
records for that next review; no prices are adjusted. In particular, 001725's
notice states conversion to ordinary shares; this layer does not map its prices
to a replacement security.

- `BLOCKED_ON_CALENDAR_EVIDENCE` resolved: **YES**, for the inherited 8,118 gaps.
- `BLOCKED_ON_CORPORATE_ACTION_EVIDENCE`: **retained**.
- `READY FOR CORPORATE-ACTION FINAL COVERAGE`: **YES**, the next evidence task.
- `READY FOR BASELINE RUN INPUT FREEZE`: **NO**.

## Deterministic artifacts

Canonical JSON uses sorted keys and escaped Unicode. Semantic identities include
source hashes, exact queries, effective boundaries and parent fingerprints.
Retrieval timestamps and local filesystem paths do not identify evidence. Source
bytes are frozen: later changes to an official website require a new evidence
version, not silent replacement of a stored response. No secret participates in
identity or serialization.

| Artifact | Fingerprint |
| --- | --- |
| [delisting-history](evidence/baseline-security-lifecycle/artifacts/delisting-history.json) | `9c1054defcbeb7196a6bdac200c4c8413c3038bbdb2556ecbc6c738c428737f6` |
| [historical-security-identities](evidence/baseline-security-lifecycle/artifacts/historical-security-identities.json) | `1c06d0606f1bdb247bb722662643907dfabb3c1a82bff333028fd2e6f8c91dae` |
| [integrated-security-evidence](evidence/baseline-security-lifecycle/artifacts/integrated-security-evidence.json) | `db56575dc3f1abef3f1266e3e9ee5bcfdb555cd4ff9f22c958a476272e2ed90c` |
| [listing-history](evidence/baseline-security-lifecycle/artifacts/listing-history.json) | `adcbe8362fc2af7cff111b94354662d75876a9be740dfd129922ad1d9fcde4d2` |
| [missing-session-reconciliation](evidence/baseline-security-lifecycle/artifacts/missing-session-reconciliation.json) | `978b735c35c14e46bfb26eba46f01a29c26ac3d1664ffc8e9a16eb36f85f3fc2` |
| [security-lifecycle-manifest](evidence/baseline-security-lifecycle/artifacts/security-lifecycle-manifest.json) | `7b9d43b9817c5e5c9f29af76f9b44c1a99ab9159e71e77b9ed15e972a50c2f22` |
| [security-lifecycle-sources](evidence/baseline-security-lifecycle/artifacts/security-lifecycle-sources.json) | `d1ddac3cf1401cb63e5ddbfab52e8243a95ca5c0cd95b23ca8071a6334db3a9a` |
| [trading-suspensions](evidence/baseline-security-lifecycle/artifacts/trading-suspensions.json) | `c26cc7c470e0e6a1c1de09fb87981da64b1206bc32cd18e33a840590c50ca846` |
| [unresolved-security-gaps](evidence/baseline-security-lifecycle/artifacts/unresolved-security-gaps.json) | `c3f7922cb6d5175f637b67a879a123f9ae4262be58f56f5922020a151e9bc820` |

## Offline reproduction and validation

Run from the repository root, with a new output directory:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.verify_security_lifecycle \
  --root . --output /tmp/baseline-lifecycle-replay
.venv/bin/pytest tests/backtest/test_security_lifecycle.py
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy
git diff --check
```

Replay reads only checked-in parent manifests and the new preserved evidence.
It does not open SQLite, deserialize estimators, contact a network, or import a
runner/inference service. New output directories are required to prevent silent
evidence overwrites. Compare every generated artifact byte-for-byte with the
published version. Official retrieval was performed with `httpx` GET/POST using
the exact endpoint and parameter maps in the source receipts; credentials were
not used. Re-querying a mutable official site is not a substitute for replaying
these preserved responses.

Protected-file before/after hashes and validation results are recorded in
[verification results](evidence/baseline-security-lifecycle/verification-results.json).
The protected scope includes Market DB, Analysis DB, Up/Down estimators, frozen
OOS manifests and prior official/DART evidence. Existing baseline, quality, cost,
calendar and settlement contracts are unchanged.
