# Security metadata investigation before implementation

Base: fresh origin/main `4fcf585de0be26cd25fbc58102588407dd23ce49` after the
user reported PR #107 merged. The working tree was clean; the existing PR #107
branch and other worktrees were preserved. No previous feature branch was used
as the base. The user subsequently instructed us not to check CI results.

## Name path and missing-name cause

`api/exporter.build_snapshot` uses `_mapping_by_ticker` and `_english_name` to
construct StockDetail.name. SnapshotReader.stock_detail returns that record;
SnapshotReader.stocks projects it to StockSummary.name. Both use the same English
corporation name, not an exchange security name. Only CURRENT_IDENTITY_VERIFIED,
a unique exact stock_code and a nonempty corp_eng_name produce a non-null name.

The existing saved `.data/research-api-snapshot.json` was read without modification
and compared with the complete committed mapping: **993 total, 871 named, 122
null**, and every ticker/name value exactly matched. All 122 missing records have
zero exact DART matches and BLOCKED_ON_IDENTITY_MAPPING. Raw corpCode XML also has
zero exact matches for these 122. Missing English name despite a verified mapping:
**0**. Duplicate/ambiguous missing mapping: **0** in this baseline.

The candidate table has **30 rows, 30 unique codes**, all in the missing set.
The introductory 28 is a count error. Candidate names are not imported, normalized
into official names or marked verified.

## Official sources and validation

The committed DART mapping contains ticker, raw matches (corp_code, stock_code,
corp_name, corp_eng_name, modify_date), current/historical status and null effective
bounds. The existing producer parses a single ZIP/XML member, rejects DOCTYPE,
and only marks a unique exact code with valid eight-digit corporation code and
nonempty corporation name as current verified. It explicitly does not infer
preferred-share relationships or historical identity. Envelope fingerprints are
validated with the existing canonical digest. Raw ZIP SHA-256:
`5ed56ec1c726ed4359fbfe042828228cd3ff4655c3bf0b4fa1bf0170e470721e`.
Mapping fingerprint:
`b9db52ec3ce75e4dc27e481cb682e2893e5d15000d289a81a43e22a1b82f904d`.

Existing `baseline-official/artifacts/security-mapping.json` remains blocked;
it does not contain a verified current security-name/type master. The recorded
KRX `stk_isu_base_info` probe received HTTP 401. No new request was made.
Existing KIND files include listing/delisting boundaries, not a comprehensive
current-name registry. Cross-reference document locators are not issuer/security
mapping approval.

Among the missing 122, **9** have exact historical boundary identity evidence:
000547, 000995, 001529, 001725, 001745, 004565, 009275, 010145, 016385.
Their preserved official delisting documents explicitly identify preferred-share
certificates and short codes in the same table. This is past documentary evidence,
not present-day security classification. The other **113** lack a corresponding
exact boundary reference in this reviewed artifact. None of the 122 gains a
verified current security name, corporation link or current classification here.
All current security types remain unknown, including the 871 DART-linked codes;
DART corporation identity alone does not establish common-share status.

Thus the coverage dimensions must stay distinct: 871 source-snapshot corporation
relationships; 0 newly verified current security names; 122 unresolved missing
legacy names; 9 of those with historical-only boundary evidence. Do not claim
that all 122 are preferred based on ticker endings or candidate names.

## Serialization and client compatibility

ResearchAPISnapshot uses frozen, extra-forbidden Pydantic models. Its existing
exporter serializes sorted compact JSON with the existing default ASCII behavior,
adds a newline and hashes the exact bytes; evidence envelopes separately use
`data.learning.digest`. These contracts are unchanged.

Read-only client inspection found Swift synthesized Codable nullable String name
fields in TrisMarketLens-iOS/TrisMarketLens/Domain/Stock.swift and Web nullable name
interfaces in TrisMarketLens/src/domain/stock.ts. HttpDataSource uses typed fetch
JSON rather than a strict unknown-field schema for these stocks. The server
snapshot models do forbid extra keys. Rather than depending on client tolerance,
this implementation keeps all existing responses byte/field compatible and
provides optional separate metadata endpoints. No client files are edited.

Existing tests cover exporter scope/993 count, deterministic snapshot bytes,
read-only DB access, stock routes, OHLCV and Operations. New tests add exact legacy
name preservation, independent metadata fingerprints and original-source replay.

## Protected evidence baseline

Market DB SHA-256 before work:
`2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`.
Only bytes were hashed; this investigation did not open the production DB via
SQLite. All 3,225 existing docs/evidence files were also hashed before work.
OOS/model/analysis stores are not used for metadata compilation. No official raw
source, approval, frozen dataset, model, trading code or operational configuration
is edited. End-of-task checksum verification is recorded in the completion report.

## Exact missing ticker set (122)

```text
000075 000087 000105 000145 000155 000157 000215 000225 000227 000325
000545 000547 000725 000815 000885 00088K 000995 001045 00104K 001065
001067 001275 001465 001515 001525 001527 001529 001685 001725 001745
001755 001795 002025 002355 002785 002787 002795 00279K 002995 003075
003465 003475 003495 003535 003545 003547 003555 003925 004105 004255
004365 004415 004545 004565 004835 004985 00499K 005257 005305 005385
005387 005389 005725 005745 005935 005945 005965 006125 006345 006405
006805 00680K 007575 007815 00781K 00806K 008355 008775 009155 009275
009415 009835 010145 010955 011155 011785 012205 014285 014825 014915
016385 019175 019685 02826K 03473K 051905 051915 066575 071055 078935
084695 090355 090435 096775 097955 108675 120115 145995 18064K 26490K
28513K 33626K 33626L 33637K 33637L 35320K 36328K 37550K 37550L 38380K
45014K 45226K
```
