# DonghakStockVision 2.0

한국 주식의 가격·거래량·거래대금을 축적하고, 향후 시장 전환 신호 학습과 매매 판단으로 확장하는 Python 프로젝트입니다. **현재 구현은 Phase 1 시장 데이터 파이프라인입니다.** 매매 신호, 학습 모델, 주문, 백테스트, 상시 스케줄러는 구현하지 않습니다.

| Phase | 목표 | 2.0 상태 |
| --- | --- | --- |
| 1 | 프로젝트 초기 설정·시장 데이터 수집·검증·저장 | 구현, KRX 실호출 검증은 인증 승인 후 |
| 2 | 상승·하락 신호 학습 | 미구현 |
| 3 | 매수·보유·매도·관망 판단 | 미구현 |
| 4 | 백테스트·모의거래 | 미구현 |
| 5 | 상시 실행 | 미구현 |

원격 저장소에는 이전 실험 스크립트와 모델이 이미 있습니다. 루트의 기존 Python/JSON 파일은 보존한 **레거시 자료**이며, 2.0 패키지에서 import하거나 실행하지 않습니다. 2.0의 설치·검사·빌드 대상은 `src/`, `tests/`입니다. 기존 Target 생성 코드와 전략·모델을 재사용하지 않았습니다.

## 설치

Python **3.12 이상**이 필요합니다. 저장소 루트에서 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Windows에서는 `.venv\Scripts\activate`로 활성화합니다. `python -m donghak_stock_vision`도 `dsv`와 같은 진입점입니다. 배포 시 `python -m build`로 wheel/sdist를 생성할 수 있습니다. 런타임 의존성은 HTTP 통신용 httpx와 `.env` 로딩용 python-dotenv입니다. Windows에서는 시간대 데이터베이스 tzdata도 설치합니다. SQLite는 표준 라이브러리를 사용합니다.

## 인증 없이 실행

Fake Provider는 **실제 시세가 아닌 고정 합성 데이터**만 제공합니다. 종목은 `005930`, `000660`, 날짜는 2024-01-02/03/04/05/08입니다. 요청한 다른 날짜나 종목에는 데이터를 생성하지 않습니다. 실제 데이터와 혼합되지 않도록 별도 DB를 사용합니다.

```bash
dsv --db .data/demo.sqlite3 collect --provider fake --tickers 005930 000660 --start 2024-01-02 --end 2024-01-04
dsv --db .data/demo.sqlite3 update --provider fake --tickers 005930 000660 --start 2024-01-02 --end 2024-01-08
dsv --db .data/demo.sqlite3 query --tickers 005930 --start 2024-01-02 --end 2024-01-08
dsv --db .data/demo.sqlite3 validate
```

최초 collect는 6행, 다음 update는 4행을 추가합니다. 같은 명령을 반복하면 `changed: 0`입니다. stdout은 JSON, stderr는 UTC 시각(`Z`)을 포함한 표준 logging 로그입니다. 수집 요약의 `succeeded`/`failed`/`empty`는 종목 수, `changed`는 삽입·정정된 행 수입니다. `empty`는 `succeeded`에 포함되지만 데이터 확보나 완전성을 뜻하지 않습니다.

종료 코드는 정상 0, 수집 일부 실패 또는 무결성 오류 1, 설정·입력·저장소 오류 2입니다. 일부 종목이 실패해도 다른 종목을 수집하며 전체 명령은 1을 반환합니다.

## KRX 설정 및 수집

기본 Provider는 공식 **KRX Open API**입니다. **현재 인증키·API 승인이 없어 실제 호출을 검증하지 않았습니다.** 자동으로 비공식 사이트나 Fake Provider로 전환하지 않습니다.

1. [KRX 이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp)에 따라 회원가입 후 인증키 승인을 받습니다.
2. 사용할 시장의 주식 일별매매정보 API에 각각 이용 신청·승인을 받습니다.
3. `.env`의 `KRX_API_KEY`에 키를 설정합니다. 키는 `AUTH_KEY` 헤더로만 전송합니다.
4. 아래 명령을 실행합니다. `.env`는 현재 작업 디렉터리에서 읽으며 이미 설정된 환경 변수가 우선합니다.

```bash
dsv collect --tickers 005930 000660 --start 2024-01-02 --end 2024-01-31
dsv update --tickers 005930 000660 --start 2024-01-02 --end 2024-02-15
dsv collect --market KOSDAQ --tickers 035900 --start 2024-01-02 --end 2024-01-31
dsv query --tickers 005930 --start 2024-01-02 --end 2024-02-15
dsv validate
```

기간은 양끝 포함이며 종료일은 **Asia/Seoul 기준 어제까지**입니다. KOSPI/KOSDAQ은 2010-01-04부터, KONEX는 2013-07-01부터 지원합니다. 주말은 요청하지 않고, 평일 휴장 여부는 응답에 맡깁니다. 거래일 캘린더를 추정하거나 빈 날짜를 채우지 않습니다.

`update`는 지정 범위 내 마지막 저장일에서 기본 7일 전부터 재조회합니다(`--overlap-days N`). 신규 종목에는 전체 지정 기간을 요청합니다. 7일보다 오래된 누락·정정이나 앞쪽 과거 구간 추가는 `collect`로 명시적으로 다시 수집합니다. 수정주가의 과거 전체 소급 보정은 지원하지 않습니다.

| 환경 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `KRX_API_KEY` | 없음 | 승인된 KRX 키. Fake에는 불필요 |
| `DSV_DB_PATH` | `.data/market.sqlite3` | raw·cleaned·audit·요청 제한 저장소. `--db` 우선 |
| `DSV_KRX_MARKET` | `KOSPI` | `KOSPI`, `KOSDAQ`, `KONEX`; `--market` 우선 |
| `DSV_REQUEST_INTERVAL` | `1.0` | HTTP 시도 간 최소 초, 1 이상 |
| `DSV_DAILY_REQUEST_LIMIT` | `9500` | DB를 공유하는 프로세스의 서울 날짜별 요청 한도, 1~9500 |

KRX 키당 일 10,000회 이하 제한에 여유를 두어 로컬 기본값은 9,500회입니다. 재시도도 횟수에 포함하고, 재실행·같은 DB를 사용하는 프로세스 간 제한을 유지합니다. **같은 키를 쓰는 수집 프로세스는 같은 DB를 사용해야 합니다.** 다른 DB·다른 앱의 키 사용량까지 추적할 수 없으므로 공유 사용 시 한도를 낮춰야 합니다. 429/일시적 5xx/네트워크 오류는 최대 3회 시도하고 지수 백오프와 Retry-After를 적용합니다. 60초를 초과하는 서버 대기 요청은 일찍 재시도하지 않고 실패시킵니다.

실제 데이터 화면에는 “한국거래소 통계정보” 출처를 표시해야 합니다. KRX 약관의 비상업적 이용·제3자 제공 제한이 적용되므로 원본/DB를 Git에 넣지 않습니다. 향후 상업 서비스 또는 데이터 배포에는 별도 이용 권리 확인이 필요합니다. [제공업체 비교](docs/providers.md)에서 제한과 선택 근거를 확인하세요.

## 검증과 CI

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
python -m build
```

일반 테스트는 실제 키·네트워크 없이 실행되며 실제 TCP 접속 시도를 차단합니다. Mock HTTP 응답으로 KRX 헤더·필드 변환·재시도를 검증합니다. `tests/integration`의 일반 통합 테스트도 모두 로컬 SQLite/Mock을 사용합니다.

GitHub Actions는 `main` 대상 PR에서 Python 3.12/3.13/3.14의 Ruff·mypy·pytest·패키지 빌드와 독립 가상환경 wheel 실행을 검사합니다. 비밀키가 필요 없으며 live 테스트는 기본 제외됩니다.

승인 후 실제 API 테스트만 별도로 실행합니다. 이 테스트는 `.env`를 자동으로 읽지 않으므로 로컬 환경에 키를 export하세요(채팅·커밋에 키를 붙여넣지 마세요).

```bash
# KRX_API_KEY를 안전하게 환경에 설정한 후
DSV_RUN_LIVE=1 pytest -m live tests/integration/test_live_krx.py
```

이 테스트는 KOSPI 삼성전자의 2024-01-02 데이터를 수집·재조회하고 멱등성을 확인합니다. 일반 CI에서 실행하지 않습니다. 실제 데이터는 pytest 임시 경로에만 남으며 테스트 응답을 저장소에 커밋하지 않습니다.

## 구조와 정책

- [architecture.md](docs/architecture.md): 목표 구조와 Phase 1 경계, SQLite 선정 이유
- [data-contract.md](docs/data-contract.md): 타입·시간대·원본·결측·거래정지·중복·수정주가 정책
- [providers.md](docs/providers.md): 공식 출처 기반 공급자 비교 및 KRX 매핑

`validate`는 SQLite·스키마·원본 체크섬·참조 연결을 검사합니다. 공식 거래일 캘린더나 종목 생애주기 마스터를 갖고 있지 않아 “모든 거래일이 빠짐없이 존재함”을 보증하지는 않습니다. 현재 종목 목록을 과거에 적용해 상장폐지 종목을 지우지 않습니다.
