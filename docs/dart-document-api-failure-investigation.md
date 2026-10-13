# DART Document API status 800 조사 — 2026-10-13

작업 시작 시 `git status -sb`는 clean이었고 `git fetch origin`으로 확인한 최신 main은
`5cb3db327355d782f299bea365b2c71983d5dc7c`였다. 이 조사에서는 공개 웹 문서만 조회했다.
인증정보·환경변수·`.env`를 읽지 않았으며 OpenDART `/api/` 요청, 키 검증, 재수집을
실행하지 않았다. Corporate Actions는 **blocked 유지**다.

## 공식 계약과 확인 범위

[공식 한국어 공시서류원본파일 가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)를
2026-10-13에 확인했다. 최초 직접 조회는 실패했지만 이후 동일 공식 페이지를 읽었다.
영문 `engopendart/engapi` 가이드는 별도 서비스이며 한국어 endpoint의 대체로 사용하지 않았다.

가이드는 `GET https://opendart.fss.or.kr/api/document.xml`, 필수
`crtfc_key` STRING(40), `rcept_no` STRING(14), 정상 binary ZIP을 명시한다.
오류 필드는 `result/status/message`다. **800의 공식 의미는 시스템 점검에 따른
서비스 중지**다. 주요 나머지 코드는 다음과 같이 요약된다.

| 코드 | 공식 의미 요약 |
| --- | --- |
| 000 | 정상 |
| 010 / 011 / 901 | 미등록 / 사용 중지 / 보유기간 만료 키 |
| 012 | IP 접근 거부 |
| 013 / 014 | 조회 자료 없음 / 파일 없음 |
| 020 / 021 | 요청 한도 / 회사 수 한도 |
| 100 / 101 | 부적절한 필드 / 접근 |
| 900 | 정의되지 않은 오류 |

020 설명은 통상 20,000회 이상의 요청에서 제한될 수 있고 별도 설정도 있음을 알린다.
이 숫자를 특정 계정의 현재 남은 한도나 안전한 일일 호출 허가로 간주하지 않는다.

해당 가이드에서 정확한 HTTP Content-Type, archive member 개수·이름·디렉터리 구조,
요청별 최대 크기, 코드별 재시도 간격·횟수는 **명시되지 않았다**.
ZIP 확인은 실제 bytes/구조 기준이다. 확장자 `.xml` 자체가 XML 오류의 증명은 아니다.
이 보고서의 재시도 제한·안전한 member 규칙·64 KiB XML 진단 한도는 내부 방어이며
공식 서비스 규칙이라고 주장하지 않는다.

[공식 이용약관](https://opendart.fss.or.kr/intro/terms.do)의 제10조는 점검시간 및
변경 가능한 사용량 제한을, 제13조는 설비/기술 사유의 서비스 중지와 사전 공지의
예외를 설명한다. 따라서 공개 일반 가이드만으로 특정 키의 권한·한도·서비스 가동을
인증할 수 없다. 약관 제11조의 일반 무료 원칙도 무제한 이용 허가는 아니다.

## 기존 실패 캐시: 읽기 전용 실측

입력: `.data/corporate-action-documents-status-800`.
모든 JSON 영수증과 gzip 본문을 읽어 aggregate만 조사했다. 메시지는 한국어 문자와
허용된 구두점만 있는 것을 확인한 뒤 공백을 정규화해 공식 의미와 비교했다.
비밀정보를 읽거나 공개하지 않았다.

| 검사 | 결과 |
| --- | --- |
| 영수증 / gzip 본문 | 3,108 / 3,108 |
| 기록된 endpoint | 모두 `https://opendart.fss.or.kr/api/document.xml` |
| 저장 파라미터 | 모두 `rcept_no` 하나; 14자리 ASCII 숫자 |
| 계획과 receipt 집합 비교 | 3,108개 정확히 일치 |
| request identity → 파일명 digest | 3,108개 일치 |
| HTTP status | 모두 200 |
| API XML status | 모두 800 |
| API message | 모두 점검에 따른 중지 메시지; 공백 정규화 후 공식 의미와 일치 |
| raw 본문 길이 / 파일 형태 | 모두 175 bytes / `result` XML; ZIP 0건 |
| raw SHA-256 | 모든 3,108건이 아래 하나의 해시; 영수증 해시와 일치 |
| 기록된 시도 | 모두 `[{"attempt":1,"http_status":200}]` |
| Content-Type / 응답 HTTP Date | 기록 없음: **미검증** |
| 호출 시각, 인증키 값/길이, 실제 IP | 기록 없음: **미검증** |
| credential_used | true라는 선언만 존재; 성공 인증 증명 아님 |

공통 raw SHA-256:
`7facdcd1154ee2da86363c2e8e5955358ea72c319f98211980a12d93d38e91fd`

캐시의 영수증 필드는 `attempts, bytes, credential_used, endpoint, http_status,
params, publisher, raw_sha256`뿐이다. 파일 mtime이나 이름의 status-800을 호출 시각,
서버 응답 헤더, 발신자 진위의 대체 증거로 사용하지 않았다. 시도 1회는 영수증의
기록이며, 서버에서 받은 총 요청 수를 독립 인증하지 않는다.

검사 전 파일 목록/bytes hash의 canonical digest:
`aea4dab42514119f9ac91437d0696e2a79ef2e137ccc35bcf73c17004f2cfc68`.
검사 후 재계산한 digest도 동일하다 (6,216개 파일).
JSON과 gzip 파일을 수정·삭제하지 않았다. HTTP 200을 문서 수집 성공으로 바꾸지 않았다.

기존 시장 DB SHA-256:
`2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`.
기존 연구 snapshot SHA-256:
`71b875b358dd3e2ce821c0fb67b92ec29cad2cb4db306cbd7a61297275364940`.
이 작업은 둘 다 읽기/해시 확인만 수행했다.

## 원인 판정: 응답이 말하는 것과 독립 증명을 구분

| 조사 항목 | 판정 |
| --- | --- |
| endpoint 오류 | 현행 코드와 저장 영수증 모두 공식 endpoint에 일치; 오류 증거 없음 |
| 파라미터 오류 | 현행 collector 및 캐시 receipt 형식은 일치; 접수번호의 실제 파일 존재 여부는 미검증 |
| API 키 인증 문제 | 키 값을 조사하지 않음; 800은 인증 오류 코드가 아니지만 인증 성공도 증명하지 않음 |
| API 사용 권한 / IP 문제 | 해당 응답은 012/101이 아님; 실제 계정/IP 권한은 미검증 |
| 호출량 제한 | 해당 응답은 020이 아님; 계정 총량과 잔여 한도는 미검증 |
| 서비스 점검 | **보존된 응답이 공식 800 점검 중지 상태를 보고했음**; 특정 점검 시간·현재 복구·전체 시스템 장애 원인은 미확인 |
| 일반 업무 오류 | 013/014/100 등 문서/입력 오류 응답으로 보고되지 않았음; receipt의 경제적 의미는 이 단계에서 판단하지 않음 |
| HTTP 200 처리 | HTTP 성공과 문서 성공은 다름. 현행 main은 XML을 거부함. 과거 구현은 해당 방어가 없었음 |

동일한 175-byte 응답이 3,108개 receipt에 반복된 패턴은 공통 서비스 응답과 일치하지만,
독립 인증 없이 실제 서버 내부 장애나 단일 점검 기간을 확정할 수는 없다. 호출 시각이
없으므로 특정 공지와 결합하지 않았다. status=800을 키 재발급 사유로 해석하지 않는다.

## 실제 코드 경로 및 과거 성공 저장 문제

Python 파일의 기준 디렉터리는 `src/donghak_stock_vision/backtest/`다.

- `corporate_action_acquisition.py::build_document_plan`: immutable DART candidate
  queue의 14자리 receipt를 deduplicate하여 계획을 작성한다. title 기반 후보는 이벤트
  승인이나 원문 존재 증명이 아니다. 현재 계획은 3,108개다.
- `collect_corporate_action_documents.py::collect`: 계획의 offset/limit slice를 기존
  `DartEvidenceClient.fetch("document.xml", {"rcept_no": ...})`에 전달한다.
  기본 배치 25개, HTTP timeout 45초다. `.env` 접근은 실제 collector 실행에만 있다.
  이번 조사에서는 이 함수의 실제 수집 경로를 호출하지 않았다.
- `dart_transport.py::request_identity`: secret을 제외한 endpoint/params/publisher/
  credential_used를 digest한다. 실제 네트워크 GET에서만 `crtfc_key`를 추가하고
  redirect는 따르지 않는다. 요청 URL·httpx/httpcore 로그는 억제한다.
- `DartEvidenceClient.fetch`: main 기준 HTTP 200과 `zip_response_valid`를 모두
  만족해야 성공 cache를 저장한다. 기존 status-800 cache도 fail closed다.
- `corporate_action_acquisition.py::inspect_document_sources`: request identity,
  endpoint, HTTP 200, gzip/raw hash와 ZIP을 offline 검사한다. 이 캐시의 replay는
  `document_archive_required`로 실패한다. 새 오류 분류가 이 차단을 해제하지 않는다.
- `corporate_action_document_review.py::build_document_review_index`: 위 inventory가
  complete인 경우만 원문 entry/hash와 검토 후보를 추출한다. 이 캐시는 통과하지 못한다.
- `dart_evidence.py::document_candidate`, `corporate_status`: filing-date/title,
  identity, 전체 event-class coverage, effective-date 검증은 별도다. HTTP 오류 해소가
  Corporate Actions final coverage 승인으로 이어지지 않는다.

Git 이력 `18bbf6807590fc651b72f1bc6f3f24c6a6269819` (PR #43)은 정상 ZIP 검사를
추가하고 HTTP 실패 검사도 파일 쓰기 앞에 배치했다. 그 이전 코드는 본문을 먼저 쓰고
HTTP 200만 검사했으므로 XML 800을 cache에 저장할 수 있었다. **그 결함의 존재는
코드로 증명되지만, 현재 캐시를 만든 실제 실행 버전은 기록이 없어 확정하지 못한다.**
현재 main에 이미 있는 HTTP 200/XML 차단을 중복 구현하지 않았다.

## 이번 최소 수정

`dart_transport.py`:

1. `document_api_error` / `DartAPIError`: 공식 코드의 안전한 category, status,
   message 존재 여부만 보고한다. 원문 message·키·URL을 예외 문자열에 넣지 않는다.
   알 수 없는 3자리 코드는 `unknown_status`; 누락/중복/손상 XML, DOCTYPE/ENTITY,
   64 KiB 초과 envelope는 추정하지 않고 기존 일반 차단으로 남긴다.
2. document request는 정확히 `rcept_no` 하나와 14자리 숫자만 허용한다.
   기존 유효 request identity hash는 그대로다. receipt 존재나 인증 성공을 추론하지 않는다.
3. cache 재사용 전에 요청 identity metadata를 대조하고 secret 검사를 적용한다.
   캐시된 XML도 읽기 전용으로 분류 후 중단하며, 자동 recollection하지 않는다.
4. 기존 ZIP validator에서 CRC, 실제 파일 존재, 중복/암호화 member, 상대경로의
   traversal·절대/drive 경로 등을 검사한다. 디스크로 archive를 extract하지 않는다.
   이는 내부 안전 규칙이며 공식 원문의 확정된 member layout이라고 주장하지 않는다.
5. 새 성공 영수증에 정규화/허용된 Content-Type label과 UTC `retrieved_at`를 추가한다.
   미지의 헤더는 `unrecognized`로 기록하며 원문 헤더로 비밀을 노출하지 않는다.
   정확한 MIME이 계약에 없으므로 header만으로 ZIP을 승인/거부하지 않는다.
   retrieval timestamp는 현재 수신 시점 기록이지 과거 공시 지식의 PIT 증명이 아니다.

`collect_corporate_action_documents.py`:

- 기존 collector에 `--max-attempts {1,2,3}`만 추가했다. 기본 3은 유지하며,
  승인된 최소 확인에는 `--limit 1 --max-attempts 1`로 요청 상한을 제한할 수 있다.
- CLI는 `DartAPIError`의 안전한 code/category만 표시한다. 다른 예외의 원문은 기존처럼
  억제한다. 새 수집 시스템이나 키 검증 명령은 만들지 않았다.

재시도는 공식 보장이나 권고가 아니다. 현행 transport의 일시적 연결/timeout,
HTTP 429/5xx 재시도 정책(최대 3회, 초기 0.35초, 후속 1/2초)은 유지했다.
문서 API XML 오류(800·인증·한도·입력·unknown 포함)는 HTTP 상태에 관계없이
즉시 중단하고 inline retry하지 않는다. 점검 뒤 수동 재시도는 새로운 승인 범위다.
`--max-attempts 1`은 연결 실패도 재시도하지 않는다. 실패 XML은 성공 cache로 저장하지
않으므로 향후 원본 오류 증거를 별도 보존하려면 승인된 diagnostic 저장 절차가 필요하다.

## 합성 테스트 및 검증

실제 키·API 호출 없이 `httpx.MockTransport`만 사용한다. 신규
`tests/backtest/test_dart_document_transport.py`는 공식 코드 분류(800, 인증, 한도,
입력, 파일 누락, unknown), 메시지 누락, 모호한 XML, 정상 ZIP/Content-Type audit,
거짓 ZIP MIME, request/cache identity, CRC/구조/위험 member, timeout/연결 실패,
단일/기본 시도 예산, secret response/log/header 차단 및 CLI 전달/보고를 검증한다.
기존 정상 cache/replay, XML rejection, 문서 검토, semantic review, final coverage
테스트를 재사용했다. 수정 전 새 테스트 중 24개 실패 / 6개 통과를 확인했다.

로컬 Python 3.12.14 최종 결과:

- 신규 synthetic regression cases **42개**.
- DART transport/evidence 및 Corporate Actions 집중 회귀: **161 passed**.
- 전체 pytest: **1,645 passed, 3 deselected** (live tests 제외).
- Ruff check / format: 통과 (263 files).
- strict Mypy: 통과 (263 source files).
- sdist / wheel build 및 `git diff --check`: 통과.

Python 3.12·3.13·3.14 CI 결과는 PR 검증 기록과 완료 보고에 남긴다.

## 실제 정상 ZIP 확인을 위한 최소 승인 요청안 — 아직 실행하지 않음

이번 오프라인 진단에는 live 호출이 필요하지 않았다. 다음 단계에서 서비스 복구 여부와
정상 ZIP 수신을 확인하려면 아래 범위를 사용자에게 제시하고 **명시적 승인 후** 실행한다.

| 항목 | 제안 범위 |
| --- | --- |
| 이유 | 캐시에는 ZIP이 없고 현재 서비스·권한 상태는 offline 자료로 검증할 수 없음 |
| endpoint / 방식 | 위 공식 `document.xml` GET 하나 |
| 접수번호 | 기존 계획 offset 0의 `20240603800041` 하나; 다른 API 조회 없음 |
| 요청 수 / retry | 최대 1회; `--limit 1 --max-attempts 1`, redirect·자동 retry 없음 |
| 예상 크기 | 과거 오류는 175 bytes; 정상 ZIP 크기는 증거가 없어 사전 예측 불가 |
| 인증 | 기존 OpenDART 인증키만 요청 내부에 사용; 키 조회/변경/재발급이나 별도 인증 테스트 없음 |
| 권한 / 제한 | 사용자 계정·IP의 허용 범위와 현재 이용한도는 사용자가 확인; 20,000이라는 일반 설명을 사용 허가로 간주하지 않음 |
| 저장 | 기존 캐시와 분리된 새 ignored 로컬 경로에 정상 원문 gzip과 secret-safe receipt; endpoint, public receipt, HTTP status, attempts, bytes, SHA, safe MIME label, UTC retrieval time |
| 오류 | code/category만 보고하고 중단; 성공 cache 없음, 기존 status-800 자료는 보존 |
| 실행 이후 | offline replay 및 document review로 검증; Corporate Actions는 계속 blocked |

이 제안은 **정상 ZIP을 보장하거나 3,108건 재수집을 승인하는 요청이 아니다**.
정상 크기·오류 원본의 별도 보존 방식을 확정해야 한다면 그 조건도 승인 범위에 포함한다.
키·IP·한도 문제는 문서 1회 응답으로도 독립 인증되지 않을 수 있다.
