# DART document MIME 검증 수정 — 2026-10-13

최신 `origin/main`은 `8a1d04a0648bdf062e00e4c703b680ae3abb8207`
(PR #98 포함)이었고 작업 트리는 clean이었다.
`fix/dart-document-mime-validation`에서 수정했다. **이번 작업의 실제 OpenDART API 호출은 0회**다.

## 실제 pilot 결과와 원인

2026-10-13 기존 pilot은 고정 접수번호 `20240603800041`로 요청 1회, retry 0회,
HTTP 200, 응답 1,492바이트를 기록했다. 수신 응답 SHA-256은
`fe5de82512d5cd838d7439025ca3b454282f6ac01daba348c71bff4275b9c526`이다.

`DartEvidenceClient.fetch`는 HTTP 성공과 ZIP 검증을 통과한 다음 bounded binary MIME
허용 목록을 검사했다. 이 마지막 검사 때문에 `DART_DOCUMENT_MIME_BLOCKED`가 발생했다.
`last_response`의 MIME 분류는 `unrecognized`다. 실제 원래 Content-Type 값은 보존되지
않았으므로 어떤 문자열이었다고 추정하지 않는다. 원문과 성공 영수증도 저장되지 않았고
실제 원문 offline replay는 수행할 수 없다. 구조 검사가 통과했다는 사실은 본문의 내용이나
기업행위 의미·완전성이 검증되었다는 뜻이 아니다.

기존 `.data/dart-document-live-pilot-20261013/audit/pilot-report.json`은 실패 기록이다.
이 파일을 변경하거나 성공으로 재분류하지 않았고 출력 경로를 삭제·재사용하지 않았다.

## 공식 계약

2026-10-13 다시 확인한
[OpenDART 공시서류원본파일 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)는
GET document.xml과 필수 40자 인증키·14자 접수번호, 정상 출력 binary ZIP을 명시한다.
특정 HTTP Content-Type 값이나 MIME 허용 목록은 명시하지 않는다. XML 오류 코드와
브라우저 다운로드 확장자 안내도 정상 ZIP의 MIME 강제 근거가 아니다.

## 최소 수정과 검증 순서

`src/donghak_stock_vision/backtest/dart_transport.py`의 `DartEvidenceClient.fetch`에서
**document.xml만** 별도 MIME 허용 목록의 수용 조건에서 제외했다. 다른 bounded binary
endpoint와 legacy 처리 계약은 유지한다. 성공 판단은 여전히 실제 응답 바이트로 수행한다.

1. bounded HTTP streaming 크기·Content-Encoding 정책과 비밀정보 검사를 적용한다.
2. HTTP 200이라도 XML API 오류 envelope는 안전한 코드로 분류하고 실패 처리한다.
3. HTTP 성공을 요구하고 ZIP magic/구조·member 경로·이름 충돌·암호화·CRC·개별/전체
   압축 해제 한도를 모두 검사한다. HTML, JSON, 비ZIP, 손상/악성 ZIP은 수용하지 않는다.
4. 검증된 document ZIP은 MIME이 알려지지 않았거나 누락되어도 원문과 영수증을 보존한다.

pilot의 16 MiB 응답, 100 members, 개별 16 MiB/총 64 MiB, disk extraction 금지,
고정 접수번호·endpoint, 1 attempt, retry 0, 신규 경로 및 일회성 잠금은 변경하지 않았다.
정상 파일 수용은 기존 성공 저장과 offline replay 경로를 그대로 사용한다.

## 향후 안전한 MIME 진단

기존 `response_content_type`을 재사용한다. 알려진 MIME은 정규화 label만, 누락/빈 값은
`not_recorded`, 그 외는 `unrecognized`로 영수증과 `last_response` 감사 정보에 남긴다.
미인지 MIME도 정상 ZIP이면 성공 영수증에 해당 분류가 남게 된다. 원래 헤더·parameters·
전체 query URL은 임의 비밀정보를 포함할 수 있으므로 출력/저장하지 않는다.
정확한 헤더를 복구하거나 미인지 헤더의 일부·해시를 추가하는 방식은 채택하지 않았다.
이 분류는 MIME 진단이며 문서 진위나 기업행위 승인 정보가 아니다.

## 오프라인 회귀

전부 합성 bytes·fake keys·MockTransport를 사용한다. 기존 MIME 거부 기대 테스트를
정상 ZIP 수용 계약으로 교체하고 순증 **32개** parameterized regression cases를 추가했다.
수정 전 7건 실패로 실제 bounded/pilot 결함을 재현했다.

- 정상 ZIP: application/zip, application/octet-stream, 미인지 MIME, 누락/빈 MIME,
  text/xml 및 text/html label. Header label과 실제 본문 형식을 구분한다.
- XML 800, 실제 HTML/JSON, 비ZIP, 손상된 구조, CRC 오류, 경로 순회, 총 압축 해제 초과를
  ZIP/미인지/누락 MIME 조합에서도 차단한다. 실패는 성공 cache를 생성하지 않는다.
- 미인지 헤더에 fake secret을 넣어 audit/영수증 비노출을 검증한다.
- pilot의 미인지/누락 MIME 정상 ZIP은 selected offline replay 성공, 전체 계획 불완전,
  요청 1회와 기존 경로 재실행 차단을 함께 검증한다.
- 다른 bounded binary endpoint의 MIME 계약은 유지됨을 검증한다.
- 기존 ZIP member 수/개별 크기·CRC·암호화·이름 충돌, HTTP 오류·timeout, access 미확인,
  기존 실패 cache 보호와 Corporate Actions 회귀를 재사용한다.

로컬 Python 3.12.14 검증 결과:

- 집중 transport/pilot 회귀: **101 passed**.
- 전체 pytest: **1,704 passed, 3 deselected, 2 warnings**, 169.62초.
  live tests는 제외했으며 경고는 기존 Starlette/물리 CPU 감지 경고다.
- Ruff check / format check, strict Mypy (265 files): 통과.
- sdist/wheel 격리 빌드와 `git diff --check`: 통과.
- Python 3.12/3.13/3.14 CI는 PR 완료 기록에 남긴다.

## 운영 상태

실제 API 재호출, 기존 실패 기록 변경, AWS 작업은 하지 않았다. Corporate Actions,
Runner Tail, Run Input Freeze는 blocked를 유지한다. 실제 정상 문서 보존 여부는 이번
합성 테스트로 확인되지 않는다. 추가 실호출에는 별도 사용자 승인 및 기존 잠금과 충돌하지
않는 저장 경로에 대한 별도 결정이 필요하다. 이 수정은 기존 pilot 경로를 재사용할 권한이나
3,108건 batch 승인으로 이어지지 않는다.
