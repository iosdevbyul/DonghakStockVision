# Baseline real-input evidence verification

This stage inspects data structure, existing frozen metadata and identity only.
It does not approve a run range, freeze run inputs, produce signals, select trades,
load an estimator, fit, predict, simulate a portfolio or calculate performance.
No policy, classifier, quality classification, fee or tax rate is changed.

## Interfaces

- `backtest/run_evidence.py`: immutable `EvidenceCheck` and `BaselineRunEvidence`,
  source-row auditing, independent evidence-pin checks, calendar/corporate-action
  checks and an explicit T+2 settlement-session mapping check.
- `backtest/inspect_evidence.py`: reproducible read-only inspection command for the
  existing OOS manifest layout and experiment lock/qualification files.
- `tests/backtest/test_run_evidence.py`: synthetic rows, temporary SQLite databases
  and synthetic artifact metadata only. These tests establish no real evidence.

Every evidence object contains all seven categories. Each category reports VERIFIED
or its own BLOCKED_ON_* status and all detected reasons. RUN_RANGE is separate from
DATASET_IDENTITY: an approved model-evaluation dataset is not automatically an
approved baseline simulation period. Missing expected inference-envelope pins are
reported even when checkpoint bytes match their prior approved experiment hashes.

Fingerprint inputs include version, source/row/universe/session identities, scope,
exclusions, every component status/reason, model provenance and policy identities.
Wall-clock verification time is omitted; a report may record it outside the semantic
object. File locations do not determine the result. Deterministic row hashes use
canonical `DailyBar.to_dict()` JSON plus LF, ordered by ticker/trading_date, including
original collection timestamps. No data repair, sorting of a malformed supplied
sequence, imputation or resampling is performed by the pure row verifier.

## Read-only execution

```sh
.venv/bin/python -m donghak_stock_vision.backtest.inspect_evidence \
  --market-db .data/market.sqlite3 \
  --analysis-db .data/analysis.sqlite3 \
  --frozen-dir .data/oos-preparation-20261001/part2-freeze \
  --models-dir .data/phase2-improvement-20261001 \
  --evaluation-lock .data/oos-evaluation-20261003/evaluation-lock.json \
  --calendar-review .data/oos-preparation-20261001/calendar-review.json \
  --expected-dataset-id oos-fcfbd930cb4a65c48b0c6464cf2959d84a94aaef5232ce4bd41f3f0c853beb6b \
  --expected-source-sha 2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42
```

The command prints evidence JSON to stdout, including blockers. It never writes a
DB or source artifact. The SQLite connection uses URI `mode=ro&immutable=1` and
`PRAGMA query_only=ON`. Nonempty WAL/journal files are rejected because ignoring
uncheckpointed state would invalidate the source fingerprint. Run against a quiescent
source: before/after SHA checks detect a changed source, not every possible external
write-and-restore race. This command does not initialize the normal SQLiteStore,
whose constructor creates schema. It uses the existing DailyBar validator directly.

The entire source DB is structurally audited separately from the frozen OOS slice.
Only the slice participates in the OOS row fingerprint. Per-ticker session counts,
bounds and hashes are retained; ordering verification is **not** a claim that the
next observed row is the next exchange session. Missing tickers remain in the
universe, and research exclusions do not delete source rows.

`TrustedHGBResolver.verify()` verifies hashes/provenance without calling `.load()`.
Inference envelopes can be reconstructed from the existing evaluation contract's
recorded parameters and runtime versions, without unpickling. Their reconstructed
fingerprints are observations, not self-authorized trusted baseline run pins.
The command deliberately leaves missing independent run-pin approval blocked.
Stored evaluation metrics and predictions are not calculated or used for decisions;
qualification JSON is read only to validate its existing content hash and linkage.

## Calendar evidence

The existing `calendar-review.json` explicitly describes itself as a secondary
cross-check rather than a certified complete exchange calendar. Its 2026 source is
secondary; public-holiday and broker year-end notices alone do not prove every
exchange/extraordinary closure. The frozen policy itself says its calendar is the
union of observed bars, not a certified calendar. Neither is promoted to VERIFIED.

A future independently reviewed calendar must classify every civil date in scope as
session, weekend, holiday or extraordinary closure, with source URLs, captured-source
hashes and review reference. A trusted pin is supplied independently of that document;
these fields alone do not authenticate a source. Missing dates are not holidays.
Calendar verification also reports unexplained missing ticker sessions, including
unobserved securities; authoritative listing/halt/availability evidence is necessary
before such absences can be resolved. No inferred delisting or ticker mapping exists.

Research exclusions 2024-03-28 and 2025-03-21 are real exchange sessions removed from
research only. They must remain sessions in official trading/settlement calendars.
They are outside the inspected OOS slice, but remain in its recorded research policy.

## Corporate actions

No complete, pinned, authoritative security-level action dataset/review covering
this universe and proposed period was found in the inspected local artifacts.
The source policy explicitly records `corporate_actions_unverified`.

The existing snapshot quality contract supports reviewed coverage, sessions and
explicit excluded corporate-action dates. It is not itself an action-data source.
Feature discontinuity detection cannot determine splits, reverse splits, stock
or bonus dividends, rights adjustments, security identity changes, mergers or
spin-offs. No price correction is allowed.

The evidence interface requires a separately pinned review, exact dataset/universe
and date scope, all listed action categories, no unresolved conditions, and a
quality-manifest hash. This is a review attestation boundary, not an automatic
corporate-action classifier. Source documents and the referenced quality manifest
must be independently validated before their pin is trusted. It does not rewrite
or approve existing quality manifests on its own.

## Official sources and settlement boundary

Sources consulted on 2026-10-05:

- [KRX KOSPI trading procedures and holidays](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp):
  cash-equity settlement is T+2, and exchange holidays are excluded from both
  trading and settlement. General rules include public holidays, Saturdays, May 1,
  year-end closure and exchange-designated closures.
- [KRX ex-dividend/ex-rights explanation](https://global.krx.co.kr/contents/GLB/06/0602/0602010204/GLB0602010204T1.jsp):
  confirms business-day settlement semantics; it is not security-level action evidence.
- [KRX historical trading guide](https://global.krx.co.kr/contents/GLB/01/0109/0109000000/guide_to_trading_in_the_korean_stock_market.pdf):
  also describes T+2. General publications do not establish a versioned effective-date
  history and complete daily settlement mapping for the proposed run.
- [KRX reference-price rules](https://regulation.krx.co.kr/contents/RGL/03/03010100/RGL03010100T6.jsp)
  and [KRX listing types](https://listing.krx.co.kr/contents/LST/02/02020000/LST02020000.jsp):
  explain why changes in capital/share rights can affect price continuity. They are
  not a complete event dataset for the approved universe.

The pure settlement checker counts the **next two supplied settlement sessions**,
not calendar days or filtered research sessions. It requires a pinned reviewed rule
with effective start/end and a fully classified calendar through the settlement tail.
It rejects unsupported rules/dates and delegates rate-coverage validation to existing
`tax_rates()`. Rates are not changed or interpolated. Coverage ends at settlement date
2026-10-01; a later settlement fails even if the execution date was inside the OOS
period. No real date mapping was approved from the general web guidance alone.

## Readiness and next review packet

The real evidence report is [baseline-run-evidence-report.json](baseline-run-evidence-report.json).
It is an inspection artifact, not a RunManifest or a frozen simulation input.

Before Run Input Freeze, obtain one independently reviewed packet containing:

1. Explicit baseline run dates/universe and warmup scope, distinct from OOS dataset approval.
2. Complete official trading/settlement calendars, extraordinary-closure evidence,
   effective settlement convention and required post-period settlement coverage.
3. Security-level corporate-action and listing/halt/availability evidence for all
   approved tickers, mapped into the existing quality-manifest contract without
   price repair or survivorship filtering.
4. Approved inference-envelope fingerprints linked to the already verified checkpoints,
   lock and qualification, plus the unchanged signal/quality/baseline/cost pins.

Do not weaken the quality policy to complete this packet. Date or cost coverage that
cannot satisfy existing contracts remains blocked and requires a separate explicit
approval process, not silent truncation or a new default.
