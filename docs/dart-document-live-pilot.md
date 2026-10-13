# DART single-document live pilot — 2026-10-13

작업 시작 시 최신 origin/main을 fetch하여
`d4ffc70fdd5d65ec8458a3e715978d83ede33fd0` (PR #96)을 확인했고 작업 트리는 clean이었다.
브랜치는 `feat/dart-document-live-pilot`이다.

## 승인 범위와 현재 실행 상태

사용자는 `GET https://opendart.fss.or.kr/api/document.xml`, receipt
`20240603800041`, 기존 로컬 DART_API_KEY, 최대 1회, retry 0회를 승인했다.
동시에 **공식 이용 권한 또는 호출 제한을 확인할 수 없으면 실호출을 보류**하도록 했다.

공개 문서와 로컬 파일만으로 현재 계정/IP의 이용 권한·남은 호출량은 확인할 수 없다.
사용자에게 해당 상태의 확인을 요청했으며, 아직 확인 결과를 받지 않았다.
따라서 Phase A의 오프라인 준비를 완료하고 **Phase B는 보류**했다.
승인 범위를 확대하거나 키 검증 요청으로 이 조건을 우회하지 않았다.

| 실제 결과 | 상태 |
| --- | --- |
| OpenDART API request / retry | **0회 / 0회** |
| HTTP / DART status | 응답 없음 / 미검증 |
| 정상 ZIP 수신 | 아직 없음 |
| 실제 response byte length / SHA-256 | 해당 없음; 응답 자체를 받지 않음 |
| 예정 저장 경로 | `.data/dart-document-live-pilot-20261013/` |
| 실제 저장 / 경로 생성 | 없음 / 생성하지 않음 |
| 실제 원문 offline replay | 미실행; 원문 없음 |
| 합성 정상 ZIP offline replay | 테스트 통과; 실제 증거로 사용하지 않음 |
| Corporate Actions / Historical Identity | blocked / 0/993 유지 |
| Corporate Actions Coverage | 0/993 유지 |
| Runner Tail / Run Input Freeze | blocked 유지 |

## 공식 계약·권한·사전 점검

2026-10-13 확인한 [공식 document guide](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)는
GET document.xml, 필수 40자리 키와 14자리 receipt, 정상 binary ZIP을 설명한다.
800은 점검 중지, 020은 요청 제한이다. 통상 20,000회라는 설명은 계정별 실제 한도나
현재 남은 호출량의 증명이 아니다. 특정 계정/IP의 유효 권한도 이 페이지로 확인되지 않는다.

[현재 이용약관](https://opendart.fss.or.kr/intro/terms.do)의 제10조는 변동 가능한 사용량 제한,
제13조는 서비스 중지, 제11조는 일반 무료 원칙을 설명한다.
공식 문서에는 이 pilot의 16/64 MiB 및 100-member 정책이 명시되어 있지 않다.
이 한도는 사용자가 이번 실험에 지정한 로컬 안전 조건이다.

사전 점검 결과:

- 기존 collector는 `dotenv_values(root / ".env").get("DART_API_KEY")`로 로딩한다.
  동일 방식으로 **설정 여부와 길이 형식만 확인**했다. 값은 출력·문서화·변경하지 않았다.
- 로컬 키가 설정되어 있고 문서의 40자리 길이 형식에 맞는다. 실제 인증 성공을 뜻하지 않는다.
- receipt `20240603800041`이 기존 3,108개 document plan에 정확히 존재한다.
- 지정된 새 경로는 존재하지 않고 symlink도 아니다.
- 현재 계정/IP 권한 및 잔여 한도 1회 이상: **확인 대기**. 이 조건 때문에 호출하지 않았다.

## 재사용한 코드와 최소 변경

기준 경로: `src/donghak_stock_vision/backtest/`.

### dart_transport.py

`DartTransferLimits`는 immutable하며 pilot 기본값은:

- response: 16 MiB
- ZIP members: 100 (directory entries 포함)
- total uncompressed bytes: 64 MiB
- individual member: 16 MiB

기존 `DartEvidenceClient`에 optional `limits`를 추가했다. 기존 일반 호출은 이 한도를
자동 적용하지 않아 범용 수집의 size 계약을 유지한다. 실제 pilot만 명시적으로 적용한다.

`fetch`는 eager `get().content` 대신 HTTP streaming을 사용한다. Content-Length가
상한보다 크면 body를 읽기 전에 차단하고, 없거나 거짓인 경우에도 실제 청크 누적 길이로
차단한다. 보존 buffer는 16 MiB를 넘지 않는다. 상한 초과 탐지는 최대 64 KiB의 청크
단위이며 마지막 초과 청크는 보존하지 않고 즉시 stream을 닫는다. 예외는 retry하지 않는다.

bounded 모드는 `Accept-Encoding: identity`를 요청하고 다른 HTTP content encoding은
읽기/확장 전에 거부한다. HTTP gzip 자동 확장이 ZIP budget 밖에서 일어나지 않도록 한
pilot 방어다. ZIP 내부 압축은 별도로 검사한다.

공유 `_inspect_zip`는 member metadata의 수/개별/총 크기를 먼저 검사하고, 실제 member를
64 KiB 청크로 읽어 크기·CRC·secret을 검증한다. chunk 경계에 걸친 키도 탐지한다.
전체 member를 무제한 `read()`하지 않는다. path traversal, 절대/drive/backslash 경로,
암호화, 중복 및 정규화/casefold 이름 충돌을 거부한다. archive를 디스크에 추출하지 않는다.

bounded 성공 영수증은 binary/XML ZIP에 허용된 정규화 MIME label도 요구한다
(`application/zip`, `application/x-zip-compressed`, `application/octet-stream`,
`application/xml`, `text/xml`). 이는 **pilot의 내부 허용 목록**이며 공식 MIME 계약으로
주장하지 않는다. MIME과 별개로 실제 ZIP bytes·CRC·한도 검증은 항상 필요하다.
일반 bounded 아닌 legacy MIME 처리와 문서 XML 오류 분류는 유지했다.

새 body/receipt는 exclusive creation으로 쓰며 기존 파일을 덮어쓰지 않는다.
`read_cached_body`는 bounded replay에서 gzip 확장에도 response 상한을 적용한다.
성공 receipt는 기존 endpoint/receipt/HTTP/bytes/SHA와 safe MIME, UTC retrieval time을
보존한다. secret 값, 실제 query URL, 임의 server message/header는 저장하지 않는다.

### corporate_action_acquisition.py

기존 `inspect_document_sources`에 optional limits를 전달할 수 있게 했다. 기존 identity,
endpoint, HTTP, gzip/raw SHA, ZIP 검증을 그대로 재사용하며 bounded replay에는 동일한
response/member 한도를 적용한다. 기존 전체-universe/문서 계획을 바꾸지 않는다.

### collect_corporate_action_documents.py

새 수집 시스템 대신 기존 collector에 `pilot` 경로 및 `--live-pilot`을 추가했다.
이 경로는 receipt와 output을 위 승인 값으로 고정하며 batch/offset 선택을 사용하지 않는다.
`max_attempts=1`, HTTP transport retries=0, redirect 없음, 환경 proxy 사용 없음이다.

`--access-confirmed`는 운영자가 현재 권한/IP/잔여 한도를 확인했다는 명시적 선언이다.
키 존재나 SHA 일치로 이 선언을 만들지 않는다. 이 flag 자체가 독립 인증은 아니다.
확인되지 않았으면 키 로딩과 HTTP client 생성 전에 차단한다. 일반 배치 모드도
pilot 전용 출력 경로를 사용할 수 없도록 차단한다.

출력 디렉터리를 `exist_ok=False`로 예약한다. 빈 디렉터리든 파일이 있든 재사용하지 않으며,
실패/프로세스 중단 후에도 같은 경로로 자동 재요청할 수 없다. 성공 원문은 해당 디렉터리에,
안전한 결과 audit는 nested `audit/pilot-report.json`에 둔다. audit가 성공 source receipt로
replay되지 않도록 top-level source JSON과 분리했다.

성공 시 동일 한도로 selected 1-document scope 및 전체 3,108-document plan의 offline
replay를 각각 수행한다. 1개 scope의 complete는 전체 수집 complete가 아니다.
실패 시 HTTP 상태/시도 수/수신 시각/안전한 API 또는 local 오류를 기록하고 종료한다.
client 초기화 실패는 요청 0회로 기록하며, close 오류도 안전한 audit를 없애지 않는다.
완전하지 않은 응답은 원본 SHA를 주장하지 않는다. 실패 XML은 성공 source cache가 아니다.

## 테스트와 호환성

새 테스트는 전부 synthetic ZIP, fake key, `httpx.MockTransport`를 사용한다.
실제 `.env`나 API를 테스트에서 사용하지 않는다.

- `tests/backtest/test_dart_transfer_limits.py`: streaming/body-length, HTTP encoding,
  ZIP member count/individual/total bytes, exact boundary, chunk-crossing secret,
  gzip replay cap, normalized name collision, strict budget 및 bounded MIME 검사.
- `tests/backtest/test_dart_live_pilot.py`: fixed receipt/GET/identity encoding, 요청 1회,
  output latch, selected/full replay 구분, status 800/HTTP 503/timeout 무재시도,
  access 미확인·기존 빈 output 차단, 기존 실패 cache 보존 및 키 비노출.
- 기존 CRC, path traversal, encryption, duplicate names, DART error/auth/quota,
  source replay/document/semantic/final coverage, baseline/API/PIT 회귀도 재사용했다.

수정 전 budget 테스트 8개가 실패함을 확인했다. 신규 regression cases는 **27개**다.
로컬 Python 3.12.14 검증 결과:

- 집중 DART/pilot/Corporate Actions: **188 passed**.
- Phase A 전체 pytest: **1,672 passed, 3 deselected** (live tests 제외).
- Ruff check / format 및 strict Mypy: 통과 (265 source/test files).
- sdist/wheel package build 및 `git diff --check`: 통과.
- Python 3.12/3.13/3.14 CI 결과는 PR 검증 기록과 완료 보고에 남긴다.

## 기존 자료 보존

기존 실패 cache 6,216개 파일의 검사 전 canonical file-hash manifest:
`aea4dab42514119f9ac91437d0696e2a79ef2e137ccc35bcf73c17004f2cfc68`.

시장 DB:
`2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`.

연구 snapshot:
`71b875b358dd3e2ce821c0fb67b92ec29cad2cb4db306cbd7a61297275364940`.

검사 후 동일 해시를 재확인했다. 기존 실패 cache·시장 DB·snapshot은 수정하지 않았다. 실제 원문과 인증정보는 Git에 넣지
않는다. `.data/`는 기존 ignore 규칙으로 보호된다. AWS, API 배포, iOS 및 실행 승인과
무관한 변경은 하지 않았다.

## 다음 실행 및 배치 운영 조건

현재 계정/IP의 이용 권한과 잔여 한도 최소 1회가 확인된 뒤에만 Phase B를 수행할 수 있다.
그때도 요청은 승인된 receipt **1회**이며 실패하면 다른 receipt나 두 번째 요청을 보내지 않는다.
한 번 성공해도 전체 3,108건의 가용성, authoritative no-event semantics, historical identity,
기업행위 적용일/정정 체인, PIT 또는 backtest readiness를 승인하지 않는다.

향후 batch에는 별도 범위 승인이 필요하다: 정확한 receipt 목록/호출 상한, 실제 계정/IP
권한·일일 잔여 한도 확인, 서비스 점검 여부, rate/quota 중단 정책, 같은 bounded streaming/
ZIP/replay 적용, 신규 immutable 저장 경로, 실패 audit/재개 계획을 먼저 검토해야 한다.
legacy collector의 기본 25개/3-attempt 동작을 이 pilot 승인으로 실행하지 않는다.
