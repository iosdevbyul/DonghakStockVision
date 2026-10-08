# Official baseline calendar and corporate-action evidence

## Scope and immutable parent

This work extends [the existing package](baseline-run-evidence-package.md), without
replacing its locked dataset, universe, model, policy or runner identity. The
approved signal anchors remain 2024-07-01 through 2025-06-30. This is an exposed
historical baseline period, not an untouched final test. No production model or real signal was
executed and no price, strategy, quality classification or cost rate was changed.
Existing offline tests may exercise synthetic model and simulation fixtures; the
real-input verification never invokes those paths.

The new [manifest](evidence/baseline-official/artifacts/official-evidence-manifest.json)
references the parent fingerprint. The
[integrated package](evidence/baseline-official/artifacts/integrated-evidence-package.json)
uses the existing `BaselineRunEvidencePackage` contract. New official evidence checks
replace calendar, corporate-action and settlement checks and refresh session-ordering
verification over the complete warmup/execution input range. The original package
and its anchor-only dataset identity remain byte-for-byte unchanged.

## Public official retrieval

The previous annual attachment path was insufficient. Inspection of the official
KRX calendar JavaScript led to the KIND `StockScheduleMain` form. Its active form
selects `searchTraidingCalendar` (the source's exact spelling) and company-action
methods. No login, credential, CAPTCHA or access restriction was bypassed.

Official endpoint: `https://kind.krx.co.kr/common/stockschedule.do`.
Publisher: Korea Exchange (KIND).

POST form fields used for the calendar:

```text
method=searchTraidingCalendar
forward=searchtraidingcalendar
searchMenu=13
selYear=<year>
selMonth=<two-digit month>
ldMktTpCd=1
periodType=M
repIsuSrtCd=
```

Fourteen monthly responses cover June 2024 through July 2025. All returned HTTP
200 and a complete calendar grid. The grid's Sunday/Saturday classes, every day
number, known holiday labels and query scope are validated. Unknown annotations,
missing cells, duplicate dates and month gaps fail closed. Session classification
uses this official trading-calendar grid and the existing
[KRX cash-equity trading/settlement rules](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp),
not an inferred weekday-only calendar or third-party package.

The official source labels 2024-10-01 and 2025-01-27 as substitute holidays. The
normalized artifact preserves that source label rather than silently substituting
a different legal description. Both are non-trading dates. The grid also includes
the 2024-12-31 exchange year-end closure and 2025-06-03 presidential election day.
Research exclusion 2025-03-21 remains an exchange **session**. The earlier
2024-03-28 exclusion remains in the locked policy and is outside this input period.

The corporate schedule uses the same form with these method/menu pairs:

| Menu | Method | Source event category |
| --- | --- | --- |
| 02 | searchPaidinCapitalIncrease | Paid-in capital increase |
| 03 | searchFreeIssueNewShares | Bonus issue |
| 04 | searchFaceValueChange | Face value change |
| 05 | searchStockDividend | Stock dividend |
| 06 | searchReductionCapital | Capital reduction |
| 07 | searchDivisionAffiliation | Division / merger |
| 08 | searchNameChange | Company-name change |

For each, `forward` is the lowercase method name and `searchMenu` is the menu.
The same fourteen monthly periods were requested: **98 category responses**.
One initial `searchTotalCalendar` probe redirected to an official maintenance
page despite HTTP 200. It is preserved as a failed query, not an empty event result.
There are 113 saved request responses in total (14 calendar + 98 category + 1 probe).
The category endpoints returned multiple markets despite `ldMktTpCd=1`; the
normalizer preserves rows and explicitly records KOSPI source markers. It does not
assume the request filter was honored.

[Raw request inventory](evidence/baseline-official/raw/retrieval.json) records exact
POST fields, endpoint, publisher, document name, requested period, HTTP status,
response length and raw SHA. Each response is saved as deterministic gzip of
unmodified bytes. Normalized months and query results have separate fingerprints.
Source-language text is preserved externally, with JSON Unicode escapes; authored
code and documentation are English. Retrieval was on 2026-10-05. Paths and
retrieval timestamps are excluded from semantic fingerprints. The maintenance
redirect's transient request identifier is not a semantic input.

## Exact boundaries

[Boundary artifact](evidence/baseline-official/artifacts/exact-boundaries.json):

- Warmup: **2024-06-14 through 2024-06-28**, exactly eleven research sessions:
  June 14, 17, 18, 19, 20, 21, 24, 25, 26, 27, 28.
- Feature window: ten preceding sessions; the eleventh is the preceding quality edge.
- Final-anchor execution tail: **2025-07-01, 02, 03, 04, 07**.
- Last possible baseline exit: **2025-07-07**.
- Settlement tail: **2025-07-08 and 2025-07-09**.

These are exchange-level bounds. A missing individual security session remains
blocked; it cannot extend the tail by searching later observations. Warmup and tail
cannot generate signal anchors. The separate runner anchor/tail integration debt
from the parent document remains outside this evidence-only change.

## Calendar comparison and settlement

The read-only comparison covers market input from 2024-06-14 through 2025-07-07.
The exchange-wide observed date union matches official research sessions: no
unexpected observed non-session and no missing exchange-wide research date.
There are 245,655 source rows; research exclusions are applied only to the session
view. The original rows remain unchanged.

However, **46 securities have unexplained missing sessions** against the official
calendar. Existing `calendar_check` therefore retains
`BLOCKED_ON_CALENDAR_EVIDENCE` at the per-security level. This does not mean the
exchange calendar is missing anymore. It means security-level absence needs
historical listing/suspension/identity evidence. The missing-date lists are preserved
in [calendar verification](evidence/baseline-official/artifacts/calendar-verification.json).
They are not reclassified as holidays or automatically excused.

[Settlement mapping](evidence/baseline-official/artifacts/settlement-calendar.json)
uses the existing T+2 eligible-session checker and tax schedule. It covers **244
possible entry/exit sessions**, a conservative calendar superset, including the
final exit. Research exclusions never remove settlement sessions. All settlement
dates are supported by the existing cost schedule. Examples: June 5, 2025 settles
June 10 (holiday and weekend crossing); July 7 settles July 9. No calendar-day +2,
interpolation or zero-tax fallback is used. `BLOCKED_ON_SETTLEMENT_EVIDENCE` is resolved.

## Corporate actions and security mapping

Official schedules contain **418 rows across all returned markets**, including
**133 KOSPI-marked rows and 131 distinct KOSPI disclosure references**. These are
verified source records, not 131 verified mechanical events affecting the approved
universe. Categories can overlap; schedules can reflect amendments and later
filings. Effective economic dates require original-disclosure review.

The source's company popup uses five-character issuer codes, including nonnumeric
codes. These are not proof of a repository six-character security ticker, share
class, historical validity interval or ticker replacement. No zero was appended
and no issuer/name similarity was used to fabricate mappings.

[Security mapping](evidence/baseline-official/artifacts/security-mapping.json) retains
all 993 securities as `BLOCKED_ON_IDENTITY_MAPPING`: **0/993 historically verified**.
[Corporate coverage](evidence/baseline-official/artifacts/corporate-actions.json)
likewise has **0/993 complete verified coverage**. Verified universe event count is
null, not zero. The states distinguish mapped event/no-event verification from
mapping, coverage and credential blockers. No-event requires complete authoritative
query semantics; an empty response alone is never sufficient.

Remaining gaps include effective security mapping, original event dates, complete
listing-termination/relisting/reorganization and other discontinuity classes, and
proof of complete no-event coverage. Events do not authorize adjusted prices or
weaken existing quality checks. All captured responses are latest public evidence,
not reconstructed historical PIT information.

## DART credential and next evidence

Public KIND interfaces were used first. They yielded real evidence but not complete
993-security coverage. The local DART credential-presence check was false; no value
was read into logs. The alternative Open DART route is
**BLOCKED_ON_DART_CREDENTIAL**. It needs an issued Open DART API authentication key
(`crtfc_key`), available through the official
[authentication-key application](https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do).

A credential alone is not coverage proof. Subsequent authorized work must preserve
company/security mappings with effective periods, complete paginated disclosures,
original event references, relevant event dates and any official suspension/listing
history required to explain the 46 securities' missing sessions. Do not place the
key in repository files or include it in commands/logged URLs. No DART API request
with a fake credential was attempted.

## Reproduction and read-only protection

The offline verifier uses the existing immutable SQLite audit (`mode=ro`,
`immutable=1`, `query_only=ON`). It does not invoke model loading, prediction, signals
or the runner. Output must be a new directory:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.verify_official \
  --root . \
  --raw-dir docs/evidence/baseline-official/raw \
  --output /tmp/baseline-official-replay
```

Compare fingerprints to the published manifest. Raw source content, normalization,
calendar checks and settlement mappings can be replayed without network requests.
All original Market/Analysis DB, model files, frozen OOS files and parent evidence
files retain their before/after SHA. No model, quality or economic policy changed.

**READY FOR BASELINE RUN INPUT FREEZE: NO**. Settlement is verified. Per-security
calendar gaps and corporate-action/identity completeness remain blocked, with the
DART credential dependency explicitly recorded. No policy relaxation is proposed.

## Public interface and fingerprints

The official form and linking JavaScript are preserved in the
[interface source inventory](evidence/baseline-official/support/sources.json).
They document public request fields, not complete corporate-action coverage.

| Artifact | Semantic fingerprint |
| --- | --- |
| official-calendar | `6501a189c73e8e69063276142fd592ed995fc35e35e05b0d87c048a51240dc7b` |
| exact-boundaries | `4efea5dcbbb77ad29621738b38f07b7a1ef841ea7e1c25ef721132c824df52d9` |
| settlement-calendar | `1040c85bf7b963e670dfd1478825c7965d0d8f3658ca1a424685b012719c8f99` |
| security-mapping | `6e764b3477458d12446d18a619ae438f688c0b9d8ccb70504f767b909e8daca2` |
| corporate-actions | `0f88349cf0ac3c2dce0920792b73bad0085f75f8f0c01bce17153ad464aa9d52` |
| official-evidence-manifest | `81c9d582d88732e1b8ea5104d656301a21189d7b2d6d09ca450b6926b93e63bf` |

## Validation

- New official-evidence tests: 26 passed; combined evidence tests: 91 passed.
- Full offline suite: 1,095 passed, one live test deselected.
- Ruff, formatting, mypy and `git diff --check`: passed.
- Eight generated artifacts reproduced byte-for-byte in a separate output directory.
- All 32 protected files retained their original SHA-256.

An earlier full-suite run overlapped source edits and failed a source-identity
stability assertion: model provenance hashes all Python implementation files.
The isolated test and full suite passed with source files held fixed. No test,
training implementation or locked policy was changed to bypass that assertion.
The final suite reported one joblib physical-core detection warning and used the
logical-core fallback.
