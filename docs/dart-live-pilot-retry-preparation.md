# DART Live Pilot retry 준비 — 2026-10-13

최신 origin/main은 `5b2b709a1d5dc3015ae00469ac4f50949d4634fa` (PR #99 포함)였다.
시작 작업 트리는 clean이었고 `fix/dart-live-pilot-retry-preparation`을 생성했다.
**이번 준비 작업의 실제 OpenDART API 요청은 0회다. 추가 실호출 승인 대기 상태다.**

## 이전 실행과 잠금 보존

이전 `.data/dart-document-live-pilot-20261013/`에는 실패 audit가 존재한다.
실제 요청은 1회였으며 HTTP 200, 1,492 bytes, 응답 SHA-256
`fe5de82512d5cd838d7439025ca3b454282f6ac01daba348c71bff4275b9c526`이었다.
ZIP 검증 뒤 MIME 제한으로 실패했고, 원문/성공 영수증은 저장되지 않았다.
PR #99에서 document MIME 검증은 수정됐지만 이 실패 기록을 성공으로 재분류하지 않는다.
이전 경로는 읽기 전용으로 보존했고 삭제·수정·재사용하지 않았다.

기존 pilot은 `PILOT_DIRECTORY` 한 경로를 사용했다. 키와 계획을 확인한 후 네트워크 client
생성 전에 `mkdir(exist_ok=False)`로 새 경로를 예약한다. 이미 존재하는 빈 디렉터리·파일·
symlink도 거부한다. 실패나 프로세스 중단 후 예약된 경로가 남으므로 재요청할 수 없다.
이 잠금 구조를 유지한다. 새 경로는 검사 시 존재하지 않았으며 준비 과정에서 생성하지 않았다.

## 최소 변경

`src/donghak_stock_vision/backtest/collect_corporate_action_documents.py`:

- 이전 경로를 `PREVIOUS_PILOT_DIRECTORY`로 명시적으로 남긴다.
- `PILOT_DIRECTORY`는 `.data/dart-document-live-pilot-retry-20261013`로 고정한다.
- 일반 batch collector가 이전/새 pilot 경로와 그 하위 경로를 사용할 수 없도록 차단한다.
- pilot CLI의 기존 고정 output 검사는 새 상수를 사용한다. 이전 경로나 임의 경로는
  pilot을 dispatch하기 전에 거부한다. 자유로운 output 선택이나 잠금 초기화 옵션을 추가하지 않는다.

고정 receipt `20240603800041`, GET document.xml, max_attempts=1, HTTP transport retries=0,
redirect 금지, 환경 proxy 비사용, 16 MiB 응답 streaming, ZIP 100 members/개별 16 MiB/
전체 64 MiB, 경로·CRC·이름 충돌·암호화 검사와 비추출 정책은 변경하지 않았다.
`--access-confirmed`는 운영자 선언이며 새로운 실호출 승인을 자동 생성하지 않는다.
False이면 키 로딩과 client 생성 전에 차단한다. 이번 작업은 이 flag로 실제 pilot을 실행하지 않는다.

## 오프라인 검증

`tests/backtest/test_dart_live_pilot.py`에 10개 regression cases를 추가했다.
최초 수정 전 3건 실패를 재현했으며, 추가 경로 검토에서는 batch 하위 경로 우회 2건도
재현해 차단했다. 기존 transport/ZIP/MIME 회귀를 함께 검증했다.

- 이전 실패 audit가 있어도 새 경로의 합성 정상 ZIP은 비표준 MIME으로 저장된다.
  성공 영수증, byte length, SHA-256과 selected offline replay를 확인한다.
- 이전/새 경로의 batch 우회 및 CLI의 이전/임의 output을 차단한다.
- 새 경로는 성공·XML 800 실패 이후에도 두 번째 요청을 차단한다. 기존 빈 경로 및
  dangling symlink도 네트워크 client 생성 전에 차단한다.
- 합성 후보 3,108개 계획에서 1개 selected replay만 complete이고, 전체 missing=3,107,
  full_plan_complete=False임을 확인한다. 이것은 실제 기업행위 Coverage 승인이 아니다.
- 기존 정상/미인지/누락 MIME, XML 오류, HTTP 오류·timeout 무재시도, ZIP 구조·CRC·압축 해제
  한도 및 secret 비노출 회귀를 재사용한다.

테스트는 임시 디렉터리·fake keys·MockTransport를 사용하며 실제 `.env`나 원문 증거를
사용하지 않는다. 저장 경로 거부 테스트는 client 생성/dispatch도 차단한다.
기존 `tests/conftest.py`의 offline socket 접속 차단도 유지한다. 합성 파일은 실제 cache나
공식 증거로 승격하지 않는다. 실제 캐시·시장 DB·snapshot·기존 audit 6,219개 파일의
검사 전후 SHA-256을 비교한다. AWS 설정은 변경하지 않는다.

## 다음 실호출의 정확한 범위

| 항목 | 별도 승인 후 실행할 범위 |
| --- | --- |
| Endpoint | GET `https://opendart.fss.or.kr/api/document.xml` |
| 접수번호 | `20240603800041`만 |
| 요청 상한 / 자동 retry | 최대 1회 / 0회 |
| 신규 출력 경로 | `.data/dart-document-live-pilot-retry-20261013/` |
| 이전 실패 경로 | 그대로 보존; 재사용/삭제/수정 금지 |
| 기존 키 | 로컬 `.env`의 DART_API_KEY; 값/일부 문자/전체 query URL 비노출 |
| 새 경로가 존재할 때 | 덮어쓰거나 삭제하지 않고 네트워크 전에 중단 |
| 정상 응답 | ZIP 검증·SHA·성공 receipt·selected/full offline replay |
| 오류 | 안전한 audit만 남기고 종료; 두 번째 요청 및 다른 receipt 금지 |

**아래 명령은 실행하지 않았다. 별도 명시적 사용자 실호출 승인 후에만 실행한다.**
프로젝트 루트에서:

```bash
.venv/bin/python -m donghak_stock_vision.backtest.collect_corporate_action_documents \
  --root . \
  --output .data/dart-document-live-pilot-retry-20261013 \
  --live-pilot \
  --access-confirmed
```

이전에 승인된 1회 요청은 이미 사용됐고 이번 실행에 재사용하지 않는다. 준비 작업의 권한은
추가 요청을 포함하지 않는다. 사용자는 위 endpoint/receipt/새 경로/1회/retry 0 범위를
별도로 명시해 승인해야 한다. 요청 직전 새 경로 부재와 같은 보호 장치도 다시 확인한다.

## 인증키·IP·호출량의 유효성

사용자가 2026-10-13 확인해 제공한 정보는 키 승인, IP 입력 공란(등록 IP 제한 없음으로
판단), 계정 화면 일일 이용 건수 40,000·당시 현재 호출 0이다. 이 정보는 운영자 제공
자료이고 API 인증 성공이나 현재 시점의 남은 한도에 대한 독립 검증은 아니다.
그 이후 실제 pilot 1회가 있었으므로 현재 호출 건수가 0이라고 유지하거나, 오류 요청이
차감되는 방식을 추정하여 정확한 잔여량을 계산하지 않는다.

이번 로컬 읽기 전용 검사는 기존 키가 40자 문자열로 설정됨을 확인했다. 키 값/부분 문자/
해시는 출력하지 않았다. 설정 존재는 현재 키 활성·IP·quota 증명과 다르다.
추가 실행 승인 시 키 승인 상태·IP 조건이 여전히 유효하고 최소 1회 가용 한도가 있는지
운영자가 재확인해야 한다. 인증 시험 endpoint 또는 quota 조회 API를 대신 호출하지 않는다.
API 키나 로그인 자격증명을 요청하지 않는다.

Corporate Actions, Runner Tail, Run Input Freeze는 blocked 유지다. Historical Identity와
기업행위 Coverage는 기존 0/993 상태를 승인하거나 변경하지 않는다. 성공하더라도 한 건의
가용성 확인일 뿐 3,108건 batch·PIT·기업행위 완전성·모델/백테스트 실행 승인이 아니다.

## 전체 검증 결과

로컬 Python 3.12.14 최종 결과:

- 집중 transport/pilot 회귀: **111 passed**.
- 전체 pytest: **1,714 passed, 3 deselected, 2 warnings**, 150.88초.
  live tests 제외, 기존 Starlette/물리 CPU 감지 경고 2개.
- Ruff check/format, strict Mypy (265 files): 통과.
- 최종 sdist/wheel 빌드 및 `git diff --check`: 통과.
- 기존 자료 6,219개 파일의 SHA-256 동일, 새 실제 output 미생성 확인.
- Python 3.12/3.13/3.14 CI는 PR 완료 기록에 남긴다.
