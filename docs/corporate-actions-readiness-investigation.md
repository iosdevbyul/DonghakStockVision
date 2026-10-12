# Corporate Actions readiness 조사 — 2026-10-13

기준: 최신 `origin/main` `f33b94c` (PR #94). 이 보고서는 로컬 파일의
읽기 전용 조사 결과다. 새 공식 데이터 조회나 기업행위 승인이 아니다.
Corporate Actions, Runner Tail, Run Input Freeze는 **blocked 유지**다.

## 결론과 원인 분류

| 조건 | 실제 확인 | 분류 | 해소 조건 |
| --- | --- | --- | --- |
| 정상 DART 문서 원본 | 이 Mac의 status-800 캐시 3,108건 중 ZIP 0건 | 원본 부족 / 과거 실패 응답 | 승인된 수집으로 정상 ZIP과 영수증을 새 디렉터리에 보존하고 offline replay 통과 |
| 전체 기간 역사적 종목 식별자 | 0 / 993; 현재 issuer 매핑만 871 / 993 | 증거 부족 | 2024-06-14~2025-07-07의 정확한 security identity와 유효 기간을 공식 자료로 검토 |
| 전 클래스 조회와 authoritative no-event | 승인 0 / 993; KIND 스케줄은 완전성 증명 아님 | 증거·조회 의미 부족 | 클래스별 공식 조회 범위, 페이지 완전성, 유효 기간과 무사건 결론의 의미를 독립 검토 |
| 이벤트 적용일·정정 체인 | 후보만 존재; 확인된 전체 커버리지 없음 | 의미 검토 부족 | 원문 항목, 해시, 정확한 종목, 적용일, 정정·취소 관계의 명시적 판단 |
| 최종 커버리지 → API 통합 | exporter가 옛 DART manifest 숫자를 사용; final coverage 입력 없음 | 후속 통합 미구현 | 실제 최종 승인 후 독립 pin과 lineage를 사용하는 명시적 통합 |
| 모순된 manifest의 verified 표시 | 역사적 identity 0 / 상위 blocked인데 coverage 993을 수용 | **구현 결함** | 이번 PR에서 거부하도록 수정 |

정상 문서를 추가하는 것만으로 모든 차단이 해소되지는 않는다. 특히 후보가 없는
673개 종목에 대해 기업행위가 없었다고 단정할 수 없다. 정책·독립 승인과 PIT 증거는
별도 조건이다. 실제 이벤트 누락의 개수나 경제적 발생 여부는 현재 자료로 **미검증**이다.

## 실제 코드 경로

모든 아래 경로는 repository root 기준이다. 단계는 실제 함수/계약만 열거한다.

1. `backtest/verify_official.py::verify`는 KIND schedule을
   `official_corporate_action_review_v1`로 정리한다. 종목별 identity blocked,
   `verified_count=0`, `verified_universe_events=null`을 명시한다.
   `official_evidence.py::action_status`와 `mapping_status`는 별도 계약이며,
   schedule rows 자체가 해당 계약을 만족한 기록은 아니다.
2. `backtest/verify_dart.py::verify`는 제목 기반 후보를
   `dart_evidence.py::document_candidate`로 만든다. `effective_date=None`,
   `BLOCKED_ON_EFFECTIVE_DATE`는 검토 대기다. DART manifest를 만들 때
   `historical_identity_count=0`, `complete_corporate_coverage_count=0`,
   `BLOCKED_ON_CORPORATE_ACTION_EVIDENCE`를 명시한다. 이 단계는 문서를
   의미 검토하거나 final coverage를 로드하는 구현이 아니다.
3. `corporate_action_acquisition.py::build_document_plan`은 3,108개 후보의
   acquisition plan을 만든다. `inspect_document_sources`는 request identity,
   endpoint, HTTP 200, gzip, raw SHA, ZIP 구조를 검사한다.
   `build_security_review_queue`는 993개 종목 모두 `PENDING_REVIEW`로 남긴다.
   `build_acquisition_manifest`도 `coverage_records_approved=0`, gate false다.
4. `corporate_action_document_review.py::build_document_review_index`는
   완전한 acquisition inventory가 필요하다. 용어·날짜 후보와 archive entry
   해시를 추출할 뿐 이벤트·적용일을 승인하지 않는다. 이 Mac의 캐시에서는
   upstream `document_archive_required`로 실패하여 index를 만들 수 없다.
5. `corporate_action_semantic_review.py::_validated_review`,
   `approved_event_evidence`, `build_semantic_review`는 명시적 reviewer decision을
   exact `(rcept_no, ticker)`와 entry 해시에 연결한다. document-scoped
   `NO_RELEVANT_EVENT_IN_DOCUMENT`는 전체 기간의 무사건 증명이 아니다.
   의미 검토를 모두 마쳐도 역사적 identity, authoritative no-event, final gate는
   자동 승인되지 않는다. 별도 final record를 생성하는 함수도 여기에는 없다.
6. `corporate_action_coverage.py::CorporateActionSecurityCoverage.status`는
   `dart_evidence.py::corporate_status`를 호출한다. 역사적 identity가 없으면
   `BLOCKED_ON_IDENTITY_MAPPING`; 조회 미완료, 14개 클래스 부족, 시작/끝 기간 부족,
   authoritative semantics false이면 `BLOCKED_ON_SOURCE_COVERAGE`; 적용일 또는
   공식 source가 없으면 `BLOCKED_ON_EFFECTIVE_DATE`다. typed event 생성자도 실제
   calendar date, 공식 host/hash/reference와 동일 종목·조회 기간을 검사한다.
   `summarize_corporate_action_coverage`는 누락 record를 만들지 않으며 하나라도
   미충족이면 final gate false다. 실제 final 993개 승인 artifact는 조사한
   `docs/evidence` 및 `.data` 경로에서 발견되지 않았다.
7. `api/exporter.py::load_sources` → `_evidence`는
   `baseline-dart/artifacts/dart-evidence-manifest.json`의
   `complete_corporate_coverage_count`를 market universe와 비교한다.
   표시용 schedule metric과 fingerprint는
   `baseline-official/artifacts/corporate-actions.json`에서 온다.
   `build_snapshot`은 동일 ticker universe를 검사한다. acquisition/index/semantic/
   final coverage는 exporter 입력에 없다. 따라서 수집 디렉터리를 채워도 API 상태는
   자동 변경되지 않는다.
8. `api/exporter.py::_baseline`은 Corporate Actions 상태를 readiness로 전달한다.
   Runner Tail 표시 상태는 `docs/research-api/model-publication.json`의
   `runnerTailIntegrated=false`에서 온다. Run Input Freeze는 lifecycle,
   corporate 또는 runner가 blocked면 blocked다. 이는 실제 freeze 실행기가 아니라
   준비 상태 표시다.

Python 경로의 앞부분은 `src/donghak_stock_vision/`이다. 공식 자료의 URL/해시는
입력 연결 검증을 위한 것으로, 자기 일관성만으로 진위를 독립 인증하지 않는다.

## 읽기 전용 실제 데이터 검증

### 시장 DB와 이전 검증의 관계

`sqlite3.connect(...?mode=ro, uri=True)`와 `PRAGMA query_only=ON`으로
`.data/market.sqlite3`를 검사했다. 검사 전후 SHA-256은 동일하다:

`2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`

이 해시는 `docs/evidence/baseline-official/artifacts/calendar-verification.json`의
`database.sha256_before`와 `sha256_after`에 정확히 일치한다. 자료가 다른 DB를
가리키는 상황은 아니다. 이 일치는 진위 인증이나 PIT 승인이 아니다.

- 993개 ticker, 1,099,255 bars, 2022-01-03~2026-10-01.
- bars metadata는 전부 `krx / KOSPI / unadjusted`.
- raw_pages 2,151건의 **저장 body** SHA 검사 불일치 0건.
- bar_sources 없는 bar 0건, 존재하지 않는 raw_id 연결 0건.
- bar_sources에서 동일 ticker/session의 다중 원본 연결 0건.
- baseline required period의 저장 bars: 245,655개, 968 tickers, 257 dates.
  기존 calendar ordering은 명시적 exclusion 1일을 적용해 research date 256개다.
  DB에 해당 날짜가 존재하는 것과 research session 허용 여부는 다르다.
- 기존 lifecycle reconciliation은 8,118개 추가 설명, unresolved securities 0,
  unexplained sessions 0. 이는 특정 결측 구간 설명이며 whole-period identity나
  기업행위 완전성을 승인하지 않는다. boundary identity 기록은 46건,
  whole-period identity 검증은 0이다.

수집 시간: bars `2026-09-29T06:12:48.049844+00:00`부터
`2026-10-02T17:28:05.916694+00:00`; raw_pages 마지막 수집은
`2026-10-02T17:28:07.614123+00:00`다. 거래일보다 훨씬 뒤에 수집된 자료다.
DB는 raw_pages, bars, bar_sources, collection_runs, request_budget를 가지며,
기업행위 ledger나 종목별 적용일 테이블은 없다. 연결된 raw 수집 시점과 최종 bar는
검사 가능하지만, 과거 시점의 공개본·정정본 전체를 재구성할 증거는 이 DB만으로
확인되지 않는다. 다중 원본이 없다는 사실은 사후 정정이 없었다는 증명이 아니다.

`.data/research-api-snapshot.json`의 기존 표시도 제공된 수치와 일치한다.
SHA-256: `71b875b358dd3e2ce821c0fb67b92ec29cad2cb4db306cbd7a61297275364940`.
수정 후 실제 DB를 사용한 in-memory export는 기존 snapshot과 byte-for-byte 동일했다.
기존 snapshot을 덮어쓰거나 새 공식 데이터 조회 결과로 취급하지 않았다.

### 공식 증거와 캐시

- KIND artifact: query results 99개, 전체 시장 schedule rows 418개,
  KOSPI rows 133개, KOSPI disclosure references 131개. 모든 993개 종목 status는
  identity blocked다. issuer code와 security code가 동일한 매핑이 아니다.
- DART filing-query artifact는 unique filing 64,981건을 기록한다.
  제목 후보 3,108건은 320개 종목에 연결되고 673개는 후보가 없다.
  완전한 filing-date query가 모든 경제적 event의 발생/적용일 완전성인 것은 아니다.
- `.data/corporate-action-documents`는 이 Mac에서 존재하지 않는다.
  확인 가능한 디렉터리는 `.data/corporate-action-documents-status-800`이다.
  3,108개 영수증 모두 request identity와 raw hash가 맞고 HTTP 200이지만,
  **3,108개 본문 모두 `<status>800</status>` XML**, 정상 ZIP 0개다.
  유지보수/오류 응답을 filing 원문으로 승격할 수 없다. 검사한 timestamp 필드
  `collected_at`/`retrieved_at`도 없으므로 receipt만으로 acquisition cutoff를
  인증할 수 없다. 폴더명·mtime을 공식 수집 시각으로 사용하지 않는다.
- 기존 offline replay를 실행한 실제 결과는 `ContractError:
  document_archive_required`다. 실패는 정상 차단이며 우회하지 않았다.
  정상 문서 0개이므로 실제 적용일 일관성, entry-level semantic binding,
  correction chain, 경제적 event 누락 여부는 **미검증**이다.
- 보존된 KIND corporate raw 99개는 retrieval raw SHA와 모두 일치한다. DART
  `source_inventory` 재계산도 기존 fingerprint와 일치한다 (content identities 2,819개).
  이 검사는 보존 자료의 연결/변경 여부이며 독립적 공식 진위 또는 완전성 승인이 아니다.
- 문서 검토 안내의 “수집 완료”로 읽힐 수 있던 문장을 acquisition replay 통과가
  전제임을 나타내도록 고쳤다. 이 로컬 검사 결과를 다른 환경의 캐시로 일반화하지 않는다.

## 수정한 구현 결함과 범위

기존 `_evidence`는 coverage 값을 `int(...)`로 변환하고 상한만 검사했다.
역사적 identity 0 / 상위 corporate blocked인 상태에서 count 993을 제공하면
Corporate Actions를 verified로 표시했다. 역사적 count도 993으로 바꾸면 명시적인
blocked lifecycle와 모순되어도 verified였다. 음수, boolean, 숫자 문자열도 수용했다.

먼저 5개 재현 테스트를 작성하여 모두 실패함을 확인한 뒤 다음만 수정했다:

- coverage count는 strict integer, 0~market universe여야 한다.
- coverage는 historical identity count를 초과할 수 없다.
- 현재 지원되는 blocked lifecycle evidence와 full coverage가 충돌하면 export를 거부한다.

현재 실제 count 0의 정상 blocked 출력과 JSON 계약은 유지된다. 공식 근거를 추가하거나
approved pin을 만들거나 verified 경로를 새로 구현하지 않았다. future verified
lifecycle/final-coverage adapter는 검토된 입력을 바탕으로 별도 구현해야 한다.

## 적용일·가격·정책·PIT 경계

현재 final coverage는 법적 effective **calendar date**를 보존한다. 날짜를 거래일로
자동 roll하지 않으며, 공시일·이사회 결정일·상장일·권리락일이 같다고 추론하지 않는다.
거래정지/상장폐지의 결측 구간은 `security_lifecycle.py::LifecycleInterval`의
공식 effective boundary로 따로 검증된다. 기업행위 적용일을 실제 주문·수량·가격에
반영하는 runtime adapter는 이 coverage 계층에 존재하지 않는다.

`baseline_policy.py`는 `reject_unusable_segment_no_adjustment`를 사용한다.
`baseline_runner.py`는 KOSPI unadjusted bars를 요구하고,
`baseline_quality.py`는 `corporate_actions_unverified`, `excluded_corporate_action`,
`price_jump`, `adjustment_mismatch`, `unverified_adjustment`를 차단한다.
`data/snapshot.py`도 검증된 adjustment method 없는 adjusted 자료를 거부한다.

split/reverse split/name change 등은 14개 EVENT_CLASSES의 명시적 검토 대상이다.
합병/분할/종목교체는 successor 가격 연결이나 현행 issuer 이름 추론을 허용하지 않는다.
상장폐지 결측 설명은 청산가격·회수금·후속 종목 보유 전환 정책이 아니다.
stock dividend는 클래스에 있으나 **cash dividend는 명시적 클래스가 아니다**.
현금배당 포함 수익률·분할 수량 변환·승계/청산 회계·정정 우선순위·거래일 적용 규칙은
이번 작업에서 새로 선택하지 않는다. 현행 가격 연구/차단 정책을 바꾸려면 운영자 결정,
공식 증거와 별도의 versioned contract 검토가 필요하다.

`api/pit_provenance_review.py::inspect_pit_provenance`는 revision history와
corporate actions 미검증을 각각 차단한다. semantic effective date 승인이 historical
knowledge cutoff를 제공하지는 않는다. 2026년 수집 자료를 2024년 의사결정에 사용
가능한 사실로 소급하지 않는다. AI 학습·추론·backtest를 실행/승인하지 않았다.

## 의존 관계와 구체적인 다음 작업

1. 기존 status-800 진단 자료는 보존한다. 권한·약관·호출량을 확인하고 필요한 승인을
   받은 후 별도의 새 acquisition 디렉터리에서 DART document.xml 정상 ZIP을
   배치 수집한다. 이번 조사에서는 공식 API 호출·credentials 확인을 하지 않았다.
2. acquisition replay와 document review index를 통과시킨다. DART 원문과 KIND/거래소
   종목별 상장·변경·적용일 자료를 보존하고 exact security 연결을 검토한다.
3. 993개 전체에 대해 유효 기간 identity, 전 클래스 official query completeness,
   무사건 의미, confirmed event의 원문 entry·적용일·정정/취소 관계를 작성한다.
   후보가 없는 종목도 이 조건이 필요하다. 현재 매핑 불충족 122개도 임의 보충하지 않는다.
4. 법적 적용일과 거래일 처리, cash dividend 범위, 변환/승계/청산 처리의 선택지를
   운영자가 결정한다: 현행 비수정 가격·불확실 구간 차단 정책 유지 또는 별도 버전의
   corporate-action accounting/adjustment contract 도입. 후자는 별도 공식 근거,
   PIT 및 회계 검증이 필요하다. 현행 정책은 이번 PR에서 유지한다.
5. 실제 reviewed `CorporateActionSecurityCoverage` 993개를 final artifact로 요약하고
   독립 승인 pin 및 기존 DART/lifecycle lineage에 연결한다. 이를 exporter에 연결하는
   adapter는 아직 구현되지 않았다. 숫자만 바꾸거나 기존 manifest를 재작성하지 않는다.
6. `runner_tail.py::_corporate` / `verify_runner_tail`에 독립 pin으로 final coverage를
   제공한다. 993개 statuses/record fingerprints, 전체 기간과 history count, blocker 0이
   필요하다. 의미 검토 artifact는 대체 입력이 아니다. 성공해도 runtime signal cutoff
   adapter와 실제 publication 승인이 별도다 (`docs/baseline-runner-tail.md`).
7. Runner Tail 통합 이후 별도 model/input/quality/PIT 검토와 Run Input Freeze를
   진행한다. final coverage만으로 freeze나 실제 실행을 승인하지 않는다.

## 검증 기록

신규 테스트는 synthetic 입력만 사용한다. 실제 evidence로 채택하지 않는다.

- 수정 전 exporter 재현: 5 failed (예상 실패), 수정 후 관련 회귀 통과.
- coverage 회귀: 14개 클래스 각각 누락 차단, warmup/tail 기간 부족 차단,
  비거래일 effective date 원형 유지, 비공식 source 차단을 추가했다.
- PIT 회귀: hash가 맞아도 corporate action unverified/unknown이면 차단됨을 추가했다.
- 기존 acquisition, document review, semantic review, identity/date linkage,
  lifecycle, baseline quality/runner, Runner Tail, API snapshot 및 PIT 회귀를 전체 suite로 검증한다.

로컬 Python 3.12.14 검증 결과:

- 전체 offline pytest: **1,603 passed, 3 deselected** (live tests 제외).
- 수정 파일의 exporter / coverage / PIT 집중 회귀: **42 passed**.
- 신규 regression cases: **24개**.
- Ruff check 및 format check: 통과 (262 files formatted).
- strict Mypy: 통과 (262 source files).
- `python -m build`: sdist 및 wheel 생성 통과.
- `git diff --check`: 통과.

Python 3.12·3.13·3.14 GitHub Actions CI 결과는 PR 검증 기록과 완료 보고에 남긴다.
