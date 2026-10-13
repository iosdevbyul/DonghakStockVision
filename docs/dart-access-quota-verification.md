# OpenDART 접근 권한·호출량 사전 확인 — 2026-10-13

최신 `origin/main`을 fetch하여 `1b5799cf4f0d08cd746dcff842074616df11d8bf`
(PR #97)을 확인했다. 시작 작업 트리는 clean이었고,
`docs/dart-access-quota-verification` 브랜치를 생성했다.

**인증키를 사용하는 실제 OpenDART API 요청은 0회다. Live Pilot은 실행 보류한다.**
공식 공개 웹페이지 조회와 로컬 읽기 전용 검사만 수행했다. 계정에 로그인하지 않았고,
계정 설정·IP·키·기존 자료를 변경하지 않았다. Corporate Actions, Runner Tail,
Run Input Freeze는 blocked를 유지한다. Historical Identity와 기업행위 Coverage는
기존 0/993을 유지하며 이번에 다시 승인하거나 계산하지 않았다.

## 공식 근거와 확인 범위

아래 자료는 모두 2026-10-13 확인했다. 공개 문서의 정책과 현재 계정의 적용 상태는
별개다. 게시판 상세는 공개 목록의 form이 사용하는 POST로 본문을 읽었다.
이 요청에는 로그인·인증키가 없고 `/api/` endpoint를 사용하지 않았다.
상세 GET 링크가 열리지 않으면 목록에서 해당 제목을 선택한다.

| 공식 자료 | 확인한 내용 |
| --- | --- |
| [홈페이지](https://opendart.fss.or.kr/) | 인증키 관리·이용현황·FAQ·공지 메뉴 |
| [공시서류원본파일 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003) | GET document.xml, 필수 40자 키/14자 접수번호, 정상 binary ZIP, 오류 코드 |
| [이용약관](https://opendart.fss.or.kr/intro/terms.do) | 제10조 허용량 및 과도한 접속 제한, 제11조 무료 원칙, 제13조 중지, 제19조 인증정보 관리, 제20~21조 이용 제한 |
| [FAQ 목록](https://opendart.fss.or.kr/cop/bbs/selectArticleList.do?bbsId=B0000000000000000002) | 다음 네 FAQ의 공개 본문 확인 |
| [이용한도 FAQ, nttId=29](https://opendart.fss.or.kr/cop/bbs/selectArticleDetail.do?bbsId=B0000000000000000002&nttId=29) | 개인/기업별 일일 합산 한도 및 분당 접속 제한 |
| [IP 차단 FAQ, nttId=28](https://opendart.fss.or.kr/cop/bbs/selectArticleDetail.do?bbsId=B0000000000000000002&nttId=28) | 분당 1,000회 이상 수집 시 IP 1시간 차단, 문의 전화 |
| [개인/기업 구분 FAQ, nttId=30](https://opendart.fss.or.kr/cop/bbs/selectArticleDetail.do?bbsId=B0000000000000000002&nttId=30) | 기업은 사업자등록증·IP 등록 조건, 기업 담당자 검토 |
| [인증키 재발급 FAQ, nttId=17851](https://opendart.fss.or.kr/cop/bbs/selectArticleDetail.do?bbsId=B0000000000000000002&nttId=17851) | 로그인 후 이용현황에서 키 확인 가능. 재발급은 기존 키를 무효화하는 절차이므로 이번에는 수행하지 않음 |
| [2026-10-08 점검 공지, nttId=17997](https://opendart.fss.or.kr/cop/bbs/selectArticleDetail.do?bbsId=B0000000000000000001&nttId=17997) | 원문 파일 API를 포함한 일부 서비스의 예정 중단·재개 기간 |
| [공지 목록](https://opendart.fss.or.kr/cop/bbs/selectArticleList.do?bbsId=B0000000000000000001) | 실행 직전 다시 확인할 안내 경로 |
| [개발가이드 카탈로그](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001) | DS001~DS006 여섯 그룹의 서비스 목록 확인 |

웹 검색 도구에서는 이용약관 및 게시판 상세 조회가 실패했다. 인증 없는 HTTP 웹페이지
조회로 약관 HTTP 200과 게시판 상세 본문을 확인했다. 조회 실패를 정책 부재로 해석하지 않았다.

## 키 상태와 IP 확인 방법

운영자가 본인 계정으로 로그인한 뒤 다음 메뉴를 읽기 전용으로 확인한다.

| 경로 | 확인할 사항 | 이번 확인 결과 |
| --- | --- | --- |
| [인증키 신청/관리 → 인증키 관리](https://opendart.fss.or.kr/mng/userApiKeyListView.do) | 기존 로컬 키가 해당 계정의 현재 키인지, 승인·사용중지·만료 상태와 제한 안내 | 공개 접근은 로그인으로 이동; 계정 화면 미열람 |
| [인증키 신청/관리 → 오픈API 이용현황](https://opendart.fss.or.kr/mng/apiUsageStatusView.do) | 현재 키, 적용 한도, 사용량, 조회 기간·집계시각·잔여 표시 여부 | 공개 접근은 로그인으로 이동; 실제 표시 필드 미검증 |
| [마이페이지 → 사용자 정보조회](https://opendart.fss.or.kr/mng/selectUserInfoView.do) | 개인/기업 계정 구분, 승인 및 등록 IP 관련 정보가 제공되는지 | 계정 정보 미열람 |

재발급 FAQ는 이용현황에서 키를 확인할 수 있다고 명시한다. 키 값은 사용자가 로컬에서
직접 대조하며 Codex에 값·일부 문자·스크린샷을 보내지 않는다. 관리 화면의 정확한 상태
필드명과 IP 설정 위치는 로그인 후 확인해야 한다. 표시가 없다면 공식 담당자에게 문의한다.
현재 로컬 키의 활성 상태나 어느 계정의 키인지는 `.env` 형식만으로 알 수 없다.

기업 FAQ는 사업자등록증과 IP 등록을 명시하며, API 코드 012는 접근 불가 IP를 의미한다.
모든 개인 계정에도 동일한 IP allowlist가 의무라는 근거는 확인하지 못했다. 등록 IP 제한과
과도한 접속에 따른 임시 IP 차단은 서로 다른 조건이다. 운영자는 실행 Mac의 실제 외부
출구 IP가 현재 허용 범위에 들어가는지, VPN/NAT 변경이나 임시 차단이 있는지 확인해야 한다.
이번에 외부 IP 확인 서비스나 인증 시험 API를 호출하지 않았다. IP 값은 보고서에 기록하지 않는다.
IP 차단 FAQ의 공식 문의 전화는 02-3145-8620이다.

가이드의 010(미등록), 011(사용 중지), 012(IP), 901(개인정보 보유기간 만료) 코드는 실패의
의미를 설명하지만, 이번에는 이 코드를 받기 위한 시험 요청을 하지 않았다. 계정 페이지
확인은 설정상 상태를 확인하는 절차이고, 실제 document.xml 요청 성공은 별도의 미검증 항목이다.

## 공식 호출량과 잔여량의 한계

공개 이용한도 FAQ(등록일 2020-01-18, 이번 확인일 위와 같음)는 다음 정책을 안내한다.

- 개인: 전체 API 서비스 합산 **일 20,000건**.
- 기업(사업자등록증·IP 등록): 공시검색과 기업개황은 한도 예외, 그 외 서비스는 합산
  **일 20,000건**. 원문 파일 API는 예외 두 종류에 속하지 않으므로 이 합산 한도 대상이다.
- 분당 1,000회 이상의 과도한 접속은 일일 한도 이내라도 제한될 수 있다.
  IP 차단 FAQ(2020-09-08)는 해당 IP를 1시간 차단한다고 안내한다.

FAQ의 서비스 개수 83/81은 게시된 설명이며 현재 카탈로그 전체 개수와 동일하다고
가정하지 않는다. document 가이드의 020 설명은 통상 20,000건이며 별도 설정 한도가
있을 수 있다고 명시한다. 따라서 위 숫자를 현재 계정의 확정 한도로 사용하지 않는다.
약관 제10조는 변경 가능한 허용량과 서비스 안정성을 위한 이용 제한을 규정한다.
공개 자료에서 초기화 시각·시간대, 실패 응답의 과금/카운트 방식, 이용현황 집계 지연,
다른 프로세스와 공유하는 카운터 범위의 세부 규칙은 확인하지 못했다.

**잔여 호출량을 공개·비로그인 상태에서 확인할 수 없었다.** 이용현황 메뉴가 존재하는
것과 실제 잔여량 필드/실시간 값이 제공되는 것은 구분한다. 로그인 화면 내부의 필드는
미검증이다. 운영자가 적용 한도와 같은 기간의 사용량을 볼 수 있어도 단순 차감값은
집계 지연과 다른 작업의 요청 때문에 현재 잔여량의 독립 증명이 아니다. 최소 1회 가용성을
화면 또는 공식 담당자 안내로 확인하고 실행 시간까지 다른 수집 작업을 중지해야 한다.
관련 표시/안내가 없으면 확인 불가로 남기고 pilot을 보류한다.

확인한 DS001~DS006 공식 카탈로그 및 공개 가이드에서 **계정 키 상태·현재 사용량·잔여
호출량을 조회하는 공개 인증 API 계약은 찾지 못했다**. 존재하지 않는다고 전 세계적으로
단정하지 않으며, 로그인 내부의 구현 endpoint를 공인 API로 간주하지 않는다.
그런 endpoint를 추정하거나 호출하지 않았다. `document.xml` 응답 계약에도 quota 잔여 필드는 없다.

## 점검 공지와 과거 status=800

2026-10-08 공지는 10월 8일 20:00~10월 11일 18:00 중단 및 11일 18:00 예정 재개를
명시한다. 대상에 공시서류원본파일 API와 인증키 관리 웹페이지가 포함되어 있다.
현재 확인일은 예정 기간 이후지만 **예정 재개가 현재 정상 동작의 증명은 아니다**.
홈페이지의 2019년 점검 팝업도 현재 장애 증거로 사용하지 않는다.

기존 3,108건 status=800은 공식적으로 점검으로 인한 서비스 중지를 뜻한다.
이것은 현재 키 활성·IP 접근·잔여 한도·ZIP 가용성을 증명하지 않는다. 과거 캐시는 호출시각과
Content-Type을 입증할 기록이 없어 해당 10월 공지와 인과 관계를 확정할 수도 없다.
HTTP 200을 정상 ZIP 수집으로 재분류하지 않았다. 기존 분석은
[dart-document-api-failure-investigation.md](dart-document-api-failure-investigation.md)에 있다.

## 로컬 읽기 전용 검증

`.env`를 dotenv parser로 점검하되 원문·키·부분 문자·키 해시를 출력하지 않았다.

| 항목 | 결과 |
| --- | --- |
| 프로젝트 `.env` / 비어 있지 않은 DART_API_KEY | 존재 / 설정됨 |
| DART_API_KEY 정의 수 / dotenv parse 오류 수 | 1 / 0 |
| 로딩된 문자열 길이 / ASCII 영숫자 / 앞뒤 공백 | 40 / 해당 / 없음 |
| 검사 프로세스 환경변수 DART_API_KEY | 없음 |
| collector 키 로딩 경로 | `dotenv_values(root / ".env").get("DART_API_KEY")` |
| 기존 document plan / 고정 receipt 일치 수 | 3,108개 / 정확히 1개 |
| 신규 pilot 출력 경로 | 존재하지 않음; 생성하지 않음 |
| 현재 계정과 키 연결 / 활성 / IP / 사용량 | 미검증 |

실제 pilot은 프로세스 환경변수의 직접 fallback 없이 프로젝트 `.env`에서 키를 얻는다.
`dotenv_values`의 기본 보간 기능은 유지되어 있으므로 향후 환경이 변경되면 동일 로더로
재확인해야 한다. 40자/영숫자 형식은 인증 성공이나 권한의 증명이 아니다.
접수번호와 계획은 `docs/evidence/baseline-dart/artifacts/corporate-action-events.json` 및
`build_document_plan`으로 읽기 전용 검사했다. 환경 파일은 변경하지 않았다.

시장 DB와 snapshot의 SHA-256은 기존 PR #97 보고서와 일치한다.

- 시장 DB: `2b9e82be943607f6ec99c8e8fc9c4015be4bd668af914f9fa2df1150244ffd42`
- Snapshot: `71b875b358dd3e2ce821c0fb67b92ec29cad2cb4db306cbd7a61297275364940`

실패 cache는 읽기 전용으로 유지했다. 6,216개 파일을 상대 경로순으로 정렬하고 각 파일에
`상대경로 + TAB + SHA-256 + LF`를 연결한 이번 검사 manifest는
`830c6d07f1474012b8268d5c70670a50d032ff21f0a28bf50499bfe1fe60836b`이다.
이 manifest의 직렬화 방식은 이전 보고서와 직접 동일하다고 가정하지 않는다.

## 기존 pilot 보호 장치 점검

코드 결함을 발견하지 않아 **코드·테스트 변경은 없다**. 기준 파일은
`src/donghak_stock_vision/backtest/collect_corporate_action_documents.py`의 `pilot`과
`src/donghak_stock_vision/backtest/dart_transport.py`이다.

- `--access-confirmed`는 운영자가 계정/IP/한도를 확인했다는 선언이다. 자체 인증·잔여량
  조회·사용자 승인 생성 기능이 아니다. False이면 키 로딩·HTTP client 생성 전에 차단한다.
  이번에는 True로 호출하거나 CLI를 실행하지 않았다. 향후에도 별도 사용자 실행 확인이 필요하다.
- pilot 요청은 고정 document.xml GET 및 `20240603800041` 1개다.
  `max_attempts=1`, HTTPTransport retries=0, redirect 없음, `trust_env=False`다.
- 고정 새 출력 경로 `.data/dart-document-live-pilot-20261013`는 기존 경로나 symlink이면
  거부한다. 요청 전 `mkdir(exist_ok=False)`로 예약하므로 실패·중단 후 같은 경로로 재실행하지
  못한다. 일반 batch mode도 이 예약된 경로를 사용할 수 없다. 예약을 삭제하여 우회하지 않는다.
- HTTP streaming 16 MiB, ZIP 100 members/총 64 MiB/개별 16 MiB, 경로·이름 충돌·암호화·CRC
  검증과 비추출 정책을 유지한다. 정상 ZIP 검증 전 성공 영수증을 만들지 않는다.
- 감사 기록은 안전한 endpoint·receipt·상태·해시 등만 담고 키/전체 query URL/임의 오류 메시지를
  기록하지 않는다. 초기화 실패·close 실패도 정해진 안전 기록을 사용한다.
- 이것은 pilot 경로의 보호다. legacy 범용 collector의 batch/retry 기능 전체를 제거하거나
  전역 권한 체계를 도입한 것은 아니다. 해당 경로를 실행할 권한은 이번 작업에 없다.

기존 `tests/backtest/test_dart_live_pilot.py`, `test_dart_transfer_limits.py`,
`test_dart_document_transport.py`의 mock/fake-key 회귀를 재사용했다.

## 사용자가 확인해야 하는 최소 사항과 실행 판단

현재 판단: **pilot 실행 불가(보류)**. 로컬 준비는 되어 있으나 계정 권한·IP·실제 한도 가용성은
미확인이고 이번 작업은 인증 API 요청을 명시적으로 금지한다.

1. 본인 계정의 인증키 관리/이용현황에서 기존 `.env` 키와 일치하며 현재 사용 가능하고
   승인·만료·정지 문제가 없는지 확인한다. 키 값이나 계정 비밀번호는 공유하지 않는다.
2. 개인/기업 구분과 IP 제한 적용 여부, 실행 Mac의 출구 IP 허용 및 임시 차단 부재를 확인한다.
   설정을 변경하지 말고 확인 불가면 담당자에게 문의한다.
3. 이용현황 또는 공식 안내로 document.xml에 적용되는 한도와 현재 최소 1회 가용성을
   확인한다. 확인 시각·기간·집계 지연을 기록하고 다른 수집 작업과 동시에 실행하지 않는다.
4. 실행 직전 공지에서 점검/중단 안내를 확인하고, 위 세 조건을 확인한 뒤 **별도 사용자
   확인으로 고정 접수번호 1회, retry 0회 실행을 지시**한다. 이번 문서나 flag가 그 확인을 대체하지 않는다.

사용자가 제공할 최소 답변은 키가 아닌 **계정 키 사용 가능 여부, IP 접근 조건 충족 여부,
한도 최소 1회 가용 여부와 확인 시각/근거, 그리고 단일 pilot 실행 확인**이다.
표시가 없는 경우에는 무엇을 확인할 수 없었는지만 알려주면 된다. 시험 호출로 대체하지 않는다.
한 건 성공해도 3,108건 batch, historical identity, 기업행위 완전성·PIT, 모델/백테스트 실행을
승인하지 않는다. 배치에는 별도 범위·한도·운영 승인이 필요하다.

## 검증 결과

문서만 추가했으므로 신규 테스트는 0개다. 로컬 Python 3.12.14 결과:

- 전체 오프라인 pytest: **1,672 passed, 3 deselected, 2 warnings** (160.24초).
  live tests는 기본 설정대로 제외했다. 경고는 기존 Starlette 및 물리 CPU 감지 경고다.
- Ruff check / format check: 통과 (265 files).
- strict Mypy: 통과 (265 source/test files).
- 격리 sdist/wheel 빌드 및 `git diff --check`: 통과.
- Python 3.12/3.13/3.14 GitHub Actions 결과는 PR 완료 기록에 남긴다.

실제 인증 API 요청은 모든 검증 과정에서 0회다.
