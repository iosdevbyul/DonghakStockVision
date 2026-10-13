# DART document batch acquisition readiness — 2026-10-14

검증 완료일은 사용자 시간대(Asia/Seoul) 기준 2026-10-14다. 기존 pilot은 2026-10-13 기록이다.

최신 origin/main `f02819611a1d251d2c3f4512c820a3a105ee7c83` (PR #100 포함)을 확인했다.
작업 트리는 clean이었고 `feat/dart-batch-acquisition-readiness`에서 작업했다.
**이번 작업의 실제 OpenDART API 요청은 0회다. 배치 수집 승인은 없으며 실행하지 않았다.**

## 실제 로컬 증거와 연구 상태

성공 pilot 경로 `.data/dart-document-live-pilot-retry-20261013/`의 원본 gzip custody와
영수증을 기존 inspector 및 bounded ZIP 계약으로 다시 읽기 전용 검증했다.
접수번호 `20240603800041`, HTTP 200, 수신 원본 1,492 bytes, SHA-256
`fe5de82512d5cd838d7439025ca3b454282f6ac01daba348c71bff4275b9c526`이다.
원본과 영수증, 성공 audit가 있으며 selected offline replay가 성공했던 기록을 보존했다.
이전 실패 경로 `.data/dart-document-live-pilot-20261013/`와 status-800 cache도 보존했다.

현재 plan fingerprint:
`74540d0b223a8da4f375eef59feef49ca0da892d640434c085c27f9dd917e7ab`.

전체 문서 계획 3,108건의 읽기 전용 inventory:

| 상태 | 건수 |
| --- | ---: |
| Verified Local | 1 |
| Pending | 3,107 |
| Attempted / Failed / Blocked | 0 / 0 / 0 |

Verified Local은 원본/영수증의 로컬 계약 검증이다. 파일 존재나 SHA 일치만으로
공식 원본의 독립 진위 증명·기업행위 의미 검증·전체 커버리지 승인을 주장하지 않는다.
성공 pilot은 이동하거나 복사하여 batch source로 만들지 않고 기존 경로에서 참조한다.

Corporate Actions, Runner Tail, Run Input Freeze는 blocked를 유지한다. 최종 기업행위
Coverage는 별도 의미/적용일/정정·취소/무사건/역사적 식별/PIT 검토와 독립 승인 전까지
기존 0/993이다. AI 학습·추론·백테스트 실행은 승인하지 않는다.

## 재사용과 필요한 운영 계층

- `corporate_action_acquisition.build_document_plan`: 기존 wrapped candidate artifact,
  parent fingerprint, 정렬된 접수번호 목록을 그대로 사용한다.
- `inspect_document_sources`: 기존 request identity, raw SHA, HTTP 및 ZIP 검증과
  source completeness 계산을 재사용한다. 배치는 identity metadata 전체 일치 및 symlink
  검사도 적용한다.
- `dart_transport.DartEvidenceClient`: streaming, 안전한 XML 오류 분류, 원본/영수증
  exclusive creation, 안전한 MIME label, UTC 수신시각을 그대로 사용한다.
- `DartTransferLimits`: 16 MiB response, 100 ZIP members, 개별 16 MiB/총 64 MiB,
  CRC·경로·암호화·이름 충돌 검증·비추출 정책을 그대로 사용한다.
- `collect_corporate_action_documents.pilot`: 기존 단일 receipt, retry 0, 새 경로 및
  일회성 잠금은 유지한다.

기존 일반 `collect`는 승인 기록 없이 0.35초/최대 3-attempt 수집을 시작할 수 있었다.
이 네트워크 dispatch는 이제 `batch_approval_record_required`로 차단한다. 기존 함수
signature와 pilot path 보호를 유지하며 승인 실행은 같은 collector CLI의 새
`--batch-approval` 분기로 보낸다. 범용 transport의 다른 데이터 수집 계약은 변경하지 않는다.

새 `dart_document_batch`는 별도 HTTP collector를 중복 구현하지 않는다. 기존 transport
위에 승인 검증, 요청 전 기록, 고정 ledger 경로, 재개 검증 및 inventory를 구성하는 운영
계층이다. 운영 승인으로 legacy batch/retry 플래그를 사용하지 않는다.

## 승인 계약과 예산

`dart_document_batch.approval_check`는 다음 exact JSON schema를 검증한다.
미등록 필드·중복 JSON key·불일치·placeholder는 거부한다. 승인을 자동으로 작성하지 않는다.

```json
{
  "version": "dart_document_batch_approval_v1",
  "endpoint": "https://opendart.fss.or.kr/api/document.xml",
  "plan_fingerprint": "검토한 계획의 실제 fingerprint",
  "receipts": ["승인할 14자리 접수번호의 오름차순 목록"],
  "max_requests": 25,
  "execution_path": ".data/dart-document-batch-runs/runs/batch-0001",
  "interval_seconds": 1,
  "retries": 0,
  "approved_at": "명시적 승인 시각의 UTC ISO-8601 문자열",
  "access_confirmed": false,
  "quota_confirmed": false
}
```

위 예시는 **실행 가능한 승인 기록이 아니다**. 실제 승인 후 정확한 receipt 목록과 시각,
권한/예산 확인을 채워야 한다. 로컬 파일과 boolean은 운영자의 승인 진술이며 독립적인
계정 인증 또는 신뢰할 수 있는 전자서명을 뜻하지 않는다. Codex가 사용자 승인 없이 이를
true로 만들거나 실행해서는 안 된다.

- 전체 plan count는 3,108과 일치해야 한다. 승인 대상은 이 계획 안의 중복 없는 오름차순
  1~25건이며 성공 pilot receipt는 승인 대상에서 제외한다.
- 승인 maximum은 정수 1~25다. 기본 제안 25는 실제 호출 승인이 아니다.
- 새 Pending 수 + 동일 승인에서 이미 예약한 수가 maximum 이내여야 한다.
  기존 성공 원본은 검증 후 재사용하며 새 요청 예산을 소모하지 않는다.
- 모든 실제 요청은 1 attempt, retry 0이며 이전 응답 이후 최소 승인 interval(1초 이상)을
  기다린다. transport의 기존 지연을 추가로 유지한다. 재개 첫 요청에도 대기한다.
- endpoint, plan fingerprint, receipt, output path, UTC 승인 시각, access/quota 확인을 검증한다.
  UTC 미래 시각·비결정적 목록·계획 밖 receipt·pilot receipt는 거부한다.
- 승인 경로는 `.data/dart-document-batch-runs/runs/batch-NNNN`로 제한한다.
  CLI output은 그 경로와 일치해야 한다. 승인 복사 후 다른 경로 지정으로 우회하지 못한다.
- 승인 dict는 실행 전에 깊은 복사하여 호출 중 외부 변경이 범위를 늘리지 못하게 한다.

## 영속 기록·중복 방지·재개

고정 ledger: `.data/dart-document-batch-runs/`.
준비 작업에서는 해당 디렉터리를 만들지 않았다. 실제 승인 실행 시에만 생성한다.

| 저장 위치 | 역할 |
| --- | --- |
| `execution.lock` | Mac/Linux kernel exclusive flock; 동시 실행 차단, 프로세스 종료 시 해제 |
| `claims/RECEIPT.json` | 실행 경로와 무관한 문서별 영구 요청 예약 |
| `runs/batch-NNNN/approval.json` | exact 승인 기록, 불변 저장 |
| `runs/batch-NNNN/attempts/RECEIPT.json` | 요청 전 예약의 단위별 독립 사본 |
| `runs/batch-NNNN/sources/` | 기존 transport가 쓰는 gzip 원본 custody·영수증 |
| `runs/batch-NNNN/events/RECEIPT.json` | 검증 성공 또는 안전한 실패 결과 |
| `runs/batch-NNNN/events/complete.json` | 완료 선언과 승인 대상의 source SHA 집합 |

claim과 attempt marker를 **네트워크 전에** exclusive 생성하고 file/directory fsync한다.
예약 수는 실제 요청 수의 보수적 상한이다. 예약 이후 sleep 중 종료되었거나 연결이
성립되지 않아도 자동으로 예산을 환급하거나 해당 receipt를 다시 요청하지 않는다.
claim은 여러 승인·경로를 통틀어 동일 문서의 중복 요청을 차단한다.

기록은 덮어쓰지 않는 개별 파일로 추가한다. 부분 기록·hash/identity 불일치·유실·symlink는
fail closed다. 완료 원본과 SHA 집합을 다시 검증하고, claim/attempt/result의 상호 연결도
검증한다. 파일 시스템 관리자가 전체 ledger를 삭제하거나 조작하는 행위를 독립적으로
증명하는 시스템은 아니다. ledger를 보존·백업하고 사용자 승인 없이 손대지 않아야 한다.
AWS나 외부 감사 서비스를 사용하지 않는다.

상태 해석:

- Pending: 검증된 원본도 요청 예약도 없는 문서.
- Verified Local: 원본과 영수증을 기존 계약으로 재검증한 문서.
- Attempted: 요청 예약이 있으나 완료 증거/확정 결과가 없는 문서. 자동 재요청 금지.
- Failed: 안전한 실패 결과가 기록된 문서. 동일 승인으로 재실행하지 않는다.
- Blocked: orphan body 등 무결성/모호성 조건으로 진행 불가. 무결성 검사 자체가 실패하면
  read-only CLI도 Blocked를 반환하고 검증되지 않은 건수/상태를 만들어 내지 않는다.

새 실행 경로가 이미 있으면 일반 실행은 거부한다. 명시적 `--resume`은 기존 동일 승인만
수용하고 완료된 실행은 거부한다. 재개 전에 모든 관리 source와 영수증/기록을 재검증한다.

- 프로세스 종료 후 source+receipt가 모두 정상이고 result만 없으면 불변 result를 추가해
  복구하고 다음 Pending 문서로 진행한다. 같은 receipt를 재요청하지 않는다.
- 원본만 있고 영수증이 없거나, 응답 후 보존 전에 종료되었거나, 파일이 유실/손상되면
  차단한다. 자동 재수집·receipt 생성·실패 성공 승격은 하지 않는다.
- Failed/Attempted/Blocked target이 있으면 기존 승인의 재개를 거부한다. 필요한 복구 또는
  재호출은 별도 근거 검토와 별도 사용자 승인이 필요하다. 이번 구현에는 claim 삭제나
  실패 reset/retry 옵션이 없다.
- 다른 승인 단위에서 검증된 source는 원래 경로에서 재사용한다. 복사/이동/덮어쓰지 않는다.

새 로컬 실행기는 Mac/Linux(POSIX flock)에 한정된다. 해당 lock을 사용할 수 없는 플랫폼은
실행을 차단하며 보호를 약화한 fallback을 두지 않는다. 기존 pilot 및 다른 경로에는 이
플랫폼 제한을 추가하지 않았다. Python 3.12/3.13/3.14 Linux CI는 유지한다.

## 오류 및 감사

첫 오류 즉시 단위를 중단한다. HTTP 429/5xx, timeout, DART 020·800·인증/IP 오류,
비ZIP·손상/악성 ZIP·원본 검증 실패 모두 이후 요청과 자동 retry를 차단한다.
예외의 임의 message·전체 query URL·원래 Content-Type/header·키는 기록/출력하지 않는다.
기존 안전한 HTTP/API status/category, bytes, MIME label, UTC 시각, response-complete 및
SHA 정보를 result에 남긴다. 네트워크 이후 감사 저장 자체가 실패해도 예약은 남아 모호한
문서의 자동 재호출을 막는다. 오류를 기록했다고 성공 source로 취급하지 않는다.

## 운영 명령 — 이번에는 실행하지 않음

읽기 전용 준비 명령(네트워크 client/ledger 생성 없음):

```bash
.venv/bin/python -m donghak_stock_vision.backtest.dart_document_batch --root .
```

이 명령의 `unapproved_template`은 기본 첫 Pending 25건을 제시하되 approved_at=null,
access/quota=false이므로 실행 승인이 아니다. 기존 batch-0001이 있다면 운영자가 새로운
비충돌 unit 번호를 승인 경로에 지정해야 한다.

별도 사용자 승인 기록을 검토한 뒤에만 실행할 명령 형태:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.collect_corporate_action_documents \
  --root . \
  --output .data/dart-document-batch-runs/runs/batch-0001 \
  --batch-approval /절대경로/운영자가-승인한-record.json
```

검토된 중단 실행의 재개에만 동일 명령에 `--resume`을 붙인다. 승인 scope/예산을 늘리거나
삭제된 원본을 다시 요청하는 수단으로 사용하지 않는다.

실제 시작 전 필요한 승인:

1. 현재 plan fingerprint와 정확한 Pending receipt 25건 이하 목록, 최대 새 요청 수.
2. 위 고정 형식의 신규 실행 경로, interval 1초 이상, retry 0, UTC 승인 시각.
3. 실제 실행 계정의 키 활성·IP 접근 조건·계정별 한도 및 최소 승인 예산 가용성 확인.
   과거 40,000/호출 0 화면이나 한 건 성공을 현재 잔여량으로 재사용하지 않는다.
4. 서비스 점검/제한 여부, 다른 수집 작업과 예산 공유, 중단/모호성에 대한 운영 처리 확인.
5. 그 정확한 단위에 대한 별도 사용자 실호출 승인. 전체 3,107건 승인을 추정하지 않는다.

## 테스트·보존

합성 3,108-document plan과 fake key/MockTransport로 정상 25건, 승인 budget 초과,
첫/중간/마지막 실패, XML 800/020/IP, HTTP 429/503, timeout·bad ZIP, 잠금·경로·중복 승인,
원본/receipt/claim 유실, 응답 전후 및 저장 중 interruption, 정상 cache 재사용을 검증했다.
기존 ZIP CRC/경로/member/확장 크기·MIME·비밀정보·pilot 회귀도 함께 실행했다.
전역 offline socket 차단을 유지했다. 합성 증거는 실제 `.data`에 저장하지 않는다.

기존 캐시·두 pilot·시장 DB·snapshot 6,222개 파일의 작업 전후 SHA-256을 비교한다.
실제 원본·키·로컬 audit·DB·snapshot은 Git에 넣지 않는다. AWS 설정은 변경하지 않는다.
신규 배치 회귀는 **57 cases**, 기존 포함 집중 회귀는 **168 passed**다.
로컬 Python 3.12.14 전체 pytest는 **1,771 passed, 3 deselected, 2 warnings**, 179.47초였다.
Ruff check/format, strict Mypy(267 source/test files), sdist/wheel 빌드와 diff check는 통과했다.
경고 2개는 기존 Starlette/물리 CPU 감지 경고다.
PR의 Python 3.12/3.13/3.14 CI 결과는 완료 기록에 남긴다.
