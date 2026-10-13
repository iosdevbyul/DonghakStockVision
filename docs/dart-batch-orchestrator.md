# DART batch orchestrator — 2026-10-14

최신 origin/main `c134c4f69df9ad01d5365359f08fa80005a3681e` (PR #101 포함)을 확인했다.
작업 트리는 clean이었고 `feat/dart-batch-orchestrator`에서 작업했다.
**이번 작업은 오프라인 Prepare/Status뿐이며 실제 OpenDART API 요청은 0회다.**

## 구조와 기존 계약 재사용

`src/donghak_stock_vision/backtest/dart_batch_orchestrator.py`는 기존 모듈을 조합한다.

| 기존 함수 | Orchestrator에서 사용하는 역할 |
| --- | --- |
| `plan_for` | 기존 3,108-document plan과 fingerprint 로드 |
| `inventory` | pilot·batch 원본/영수증·예약·result/complete 무결성 재검증 |
| `summary` | 상태별 건수 계산; 전체 documents map은 출력하지 않음 |
| `approval_check` | 기존 완료 manifest와 외부 approved 파일의 정확한 승인 scope 검증 |
| `execution_lock` | Prepare를 기존 실행/다른 Prepare와 직렬화; 실행 자체는 호출하지 않음 |
| `write_once` | 미승인 draft를 exclusive 생성하고 fsync; 기존 파일 덮어쓰기 금지 |
| `source_hashes` / `inspect_document_sources` | inventory를 통해 bounded SHA·identity·ZIP·CRC·기존 source replay 계약 재사용 |

새 수집 엔진·HTTP client·승인 승격·Execute 자동 호출 경로를 만들지 않았다.
기존 `execute`와 collector CLI, transport 및 Live Pilot 동작은 변경하지 않았다.
Status는 기존 kernel lock을 read-only/shared로 열며 파일/디렉터리를 생성하지 않는다.
Prepare는 read-only preflight 후 기존 exclusive execution lock 안에서 다시 검사하고 draft만 쓴다.
실제 환경에서는 기존 lock의 내용을 변경하지 않았다.

## 번호와 Pending 선정

단순히 가장 큰 번호를 증가시키지 않는다. 먼저 다음 조건을 확인한다.

1. 모든 기존 source·receipt·claim·attempt·event·complete를 기존 inventory 계약으로 검증한다.
2. Attempted/Failed/Blocked 문서가 하나라도 있거나 complete marker 없는 실행이 있으면 차단한다.
   원본+receipt가 정상이어도 기존 실행의 완료 선언이 없으면 새 batch로 넘어가지 않는다.
3. 완료된 batch ID가 batch-0001부터 연속인지, 승인 대상이 서로 겹치지 않는지 확인한다.
4. `.data/dart-batch-NNNN-approved.json`은 같은 unit manifest와 일치해야 한다.
   승인만 있고 실행되지 않은 단위, 다른 번호의 orphan 승인, scope 불일치는 차단한다.
5. 기존 historical draft는 해당 완료 manifest의 scope와 일치하고 미승인 상태여야 한다.
   다음 draft가 이미 있으면 같은 번호에 머무르며 Prepare 재실행은 거부한다.
   더 뒤 번호의 orphan draft나 stale fingerprint는 새 번호로 우회하지 않는다.
6. 검증된 문서 계획의 순서에서 Pending이고 기존 승인 scope/claim에 없는 문서만 고른다.
   기본 25, 허용 1~25다. 기존 pilot과 Verified Local 문서는 선택하지 않는다.

미완료·모호한 실행은 기존 실행기의 별도 검토/재개 절차로 처리해야 한다. Orchestrator는
예약 초기화, 손상 원본 재수집, 실패 retry, 기존 승인 편집을 제공하지 않는다.

## Prepare / Review / Execute 구분

| 단계 | 이 PR의 범위 |
| --- | --- |
| Prepare | 기존 증거 검증·다음 ID/목록 계산·미승인 초안 저장·짧은 결과 |
| Review | 사용자가 초안 25건 이하와 현재 키/IP/한도를 확인하고 별도 승인하는 운영 단계 |
| Execute | 이 모듈에 없음. 별도 승인 후 기존 `execute`/collector CLI만 사용 |

새 draft는 기존 approval schema의 정확한 11 fields를 사용한다.
`approved_at=null`, `access_confirmed=false`, `quota_confirmed=false`이며,
`max_requests`는 실제 선정 개수, `interval_seconds=1`, `retries=0`이다.
가짜 승인 시각이나 true 확인 flag를 생성하지 않는다. draft를 기존 실행기에 전달하면
`batch_access_unconfirmed`로 키 로딩/네트워크 전에 거부한다.

운영자가 실제 승인을 한 뒤에는 별도 approved 파일을 기존 계약으로 준비해야 한다.
이 PR은 그 확인을 대신하거나 `.env`의 키 존재를 활성/권한 증명으로 사용하지 않는다.
초안은 `.data/` ignore 대상이며 실제 파일을 Git에 넣지 않는다.

## CLI와 결과

```bash
.venv/bin/python -m donghak_stock_vision.backtest.dart_batch_orchestrator \
  --root . --status

.venv/bin/python -m donghak_stock_vision.backtest.dart_batch_orchestrator \
  --root . --prepare-next
```

필요하면 `--limit 1`~`--limit 25`를 지정한다. 기본도 read-only Status다.
`--execute`, 자동 승인, HTTP 시험 요청 옵션은 없다.
결과는 상태별 건수, 다음 batch ID/선정 개수, fingerprint, 실행/draft 경로, 승인·실행·
replay 상태와 차단 이유만 출력한다. 전체 3,108 JSON map이나 receipt 목록은 출력하지 않는다.
선정 receipt 목록은 draft 파일에서 검토한다. 기존 next draft의 크기를 명령으로 바꾸지 않는다.
검증에 실패하면 종료 코드 1과 안전한 이유를 출력하며, 확인 못한 건수는 UNVERIFIED로
표시한다. 임의 예외 message, key, 전체 query URL을 출력하지 않는다.

`Offline Replay: VERIFIED`는 현재 보존된 source·receipt가 재검증되었다는 의미이며
전체 3,108건 완료 또는 기업행위 승인이라는 뜻이 아니다.

## 실제 오프라인 Prepare 결과

읽기 전용 검증 결과:

- Total 3,108; Verified Local **26**; Pending **3,082**.
- Attempted / Failed / Blocked: **0 / 0 / 0**.
- 완료된 batch-0001 및 성공 pilot 1건을 재검증했다.
- Plan fingerprint: `74540d0b223a8da4f375eef59feef49ca0da892d640434c085c27f9dd917e7ab`.

새 미승인 draft를 생성했다:

- ID: **batch-0002**.
- 대상: plan/inventory로 결정한 다음 Pending **25건**. 하드코딩하지 않았다.
- Draft: `.data/dart-batch-0002-draft.json`.
- 예정 실행 경로: `.data/dart-document-batch-runs/runs/batch-0002`.
- Approval: **REQUIRED**; Execution: **NOT STARTED**.
- 기존 batch-0001의 25건과 pilot은 선정에서 제외됐다.
- 새 실행 디렉터리나 approved 파일은 만들지 않았다.

기존 cache·pilot·batch-0001·승인/draft·시장 DB·snapshot **6,351개 파일**의 작업 전후
SHA-256은 동일하다. 기존 파일을 수정·삭제·덮어쓰지 않았으며 추가한 실제 로컬 파일은
위 미승인 draft뿐이다. AWS, FastAPI, iOS는 변경하지 않았다.

Corporate Actions Coverage는 **0/993**, Corporate Actions·Runner Tail·Run Input Freeze는
**blocked**다. 수집 준비/로컬 replay로 historical identity, 이벤트 의미·적용일·정정·취소·
무사건·PIT, AI 학습/추론/백테스트 실행을 승인하지 않는다.

## 검증과 다음 단계

신규 regression cases는 27개다. fake key/MockTransport로 만든 첫 batch 25건과 pilot
fixture는 합성 자료이며 실제 공식 증거가 아니다. 부족/완료 선정 테스트의 mocked inventory도
실제 source 검증을 대체하지 않는다. 전역 offline socket 차단을 유지한다.

26건 인식, 다음25/번호2·3, 중복 scope, 미승인 gate, 기존 draft 불변성, 경로 충돌,
미완료·실패·모호한 예약, 원본/receipt 손상·누락, fingerprint drift, budget >25,
부족/완료 종료, read-only status, active lock, CLI compact output 및 secret 비노출을 검증했다.
기존 batch/pilot/transport 회귀도 함께 검증했다.
로컬 Python 3.12.14 결과:

- 신규 orchestrator **27 cases**, 기존 포함 집중 **195 passed** (75.43초).
- 전체 pytest **1,798 passed, 3 deselected, 2 warnings** (235.06초).
  live tests 제외, 기존 Starlette/물리 CPU 감지 경고 2개.
- Ruff check/format, strict Mypy (269 source/test files), sdist/wheel 빌드 및 diff check 통과.
- Python 3.12/3.13/3.14 CI는 PR 완료 기록에 남긴다.

실제 batch-0002 실행 전에는 draft의 fingerprint/25건/경로를 검토하고 현재 OpenDART
키 활성·IP 조건·최소 요청 예산 25회 가용성, 점검/제한 및 다른 작업과의 예산 공유를 확인해야
한다. 그 정확한 scope에 대한 사용자 명시적 승인과 실제 UTC 승인 시각이 필요하다.
확인 전에는 false/null 상태를 유지한다. 과거 배치 승인은 다음 단위에 재사용하지 않는다.
