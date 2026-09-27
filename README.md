# DonghakStockVision 2.0

한국 주식의 가격·거래량·거래대금을 축적하고, 향후 시장 전환 신호 학습과 매매 판단으로 확장하는 Python 프로젝트입니다. **현재 Phase 1 시장 데이터, Phase 2 조건부 신호 분석, Phase 3 연구용 가상 판단을 구현했습니다.** iOS·웹·외부 프로그램에서 사용할 독립 Python 엔진이며, 운영 판단·HTTP 서버·실제 주문·백테스트·상시 스케줄러는 구현하지 않습니다.

| Phase | 목표 | 2.0 상태 |
| --- | --- | --- |
| 1 | 프로젝트 초기 설정·시장 데이터 수집·검증·저장 | 구현, KRX 실호출 검증은 인증 승인 후 |
| 2 | 상승·하락 신호 학습 | 구현, 실제 시장 성능·PIT 운영 검증은 미완료 |
| 3 | 매수·보유·매도·관망 판단 | 연구용 가상 판단 구현, 운영은 항상 차단 |
| 4 | 백테스트·모의거래 | 미구현 |
| 5 | 상시 실행 | 미구현 |

원격 저장소에는 이전 실험 스크립트와 모델이 이미 있습니다. 루트의 기존 Python/JSON 파일은 보존한 **레거시 자료**이며, 2.0 패키지에서 import하거나 실행하지 않습니다. 2.0의 설치·검사·빌드 대상은 `src/`, `tests/`입니다. 기존 Target 생성 코드와 전략·모델을 재사용하지 않았습니다.

## 설치

Python **3.12 이상**이 필요합니다. 저장소 루트에서 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,learning]'
cp .env.example .env
```

Windows에서는 `.venv\Scripts\activate`로 활성화합니다. `python -m donghak_stock_vision`도 `dsv`와 같은 진입점입니다. 배포 시 `python -m build`로 wheel/sdist를 생성할 수 있습니다. Phase 1의 기본 런타임 의존성은 HTTP 통신용 httpx와 `.env` 로딩용 python-dotenv입니다. Windows에서는 시간대 데이터베이스 tzdata도 설치합니다. SQLite는 표준 라이브러리를 사용합니다. 학습은 선택적 `learning` extra(scikit-learn 1.9.1 이상 1.10 미만, NumPy 2.3 이상 3 미만)를 설치합니다. 모델은 안전한 JSON 수치로 저장하므로 조회·추론에는 과학 계산 라이브러리가 필요하지 않습니다. 학습 실행마다 실제 라이브러리 버전·시드·코드 hash를 기록합니다.

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

## Phase 2: 학습·평가·분석

라벨 `reversal_barrier_v1`은 **직전 반대 추세 문맥에서 5개 미래 세션 내 단기 역방향 장벽 최초 도달**을 예측합니다. 지속적 추세 반전, 검증된 상승 확률, 수익 또는 5일 보유 지시가 아닙니다. H=5, 최소 장벽 2%, 변동성 배수 2, 점수 임계값 0.5는 승인된 초기 가설로 고정했습니다. 상승 모델은 직전 5관측 로그수익률<0, 하락 모델은 >0인 모집단만 학습·평가합니다. 문맥 밖 점수는 `null/context_mismatch`, 평탄 문맥은 `no_context`입니다.

두 모드를 명시적으로 선택합니다.

| 모드 | 입력과 시간 | 사용 범위 |
| --- | --- | --- |
| `historical-research` | 실제 수집 cutoff의 불변 스냅샷, 과거 서울 16:00를 가상 EOD anchor로 사용 | `research_only`; 사후 수정·기업행사·생존편향 한계 |
| `point-in-time` | 실제 `collected_at <= as_of`, 검증된 품질 manifest와 당시 가용 모델 | `pit_review_required`; 별도 검토·등록 이전 운영 조회 불가 |

과거 일괄수집 자료를 PIT로 바꾸지 않습니다. 연구 스냅샷에는 가격·거래량·실제 거래대금·메타데이터를 복사하고 SHA-256 `snapshot_id`로 고정합니다. 학습 이후 원래 시장 DB가 수정·삭제돼도 특징·라벨·평가·연구 추론은 저장된 스냅샷만 읽습니다. 새 시장 데이터는 새 학습 스냅샷/모델을 만들거나 PIT 추론으로 명시적으로 분석합니다. 연구 모델의 다른 스냅샷 추론은 거부합니다.

**실제 학습 최소량:** 전체 5종목·504개 적격 anchor 날짜; 방향별 train 1,000행·252날짜·양성/음성 각각 50개, validation/test 각각 200행·63날짜·양성/음성 각각 20개입니다. 특징은 최소 11개 적격 일봉이 필요합니다. 부족한 방향은 필요한 값과 실제 값, `insufficient_data`를 보고하며 점수를 만들어 내지 않습니다. 기본 Fake 데모 5일 데이터로 모델을 학습할 수 없습니다.

다음 명령의 종목·기간·시각은 형식 예시이며 해당 데이터가 로컬 DB에 있어야 합니다. `--snapshot-as-of`는 데이터를 실제로 확보한 timezone 포함 시각으로 지정합니다. 연구 품질 자료가 없으면 한계를 명시적으로 동의해야 하며, 알려진 불량 구간은 동의해도 제외합니다.

```bash
dsv --db .data/market.sqlite3 train --mode historical-research \
  --tickers 005930 000660 035420 035720 005380 \
  --start 2019-01-02 --end 2024-12-30 \
  --snapshot-as-of 2026-09-23T09:00:00+09:00 \
  --acknowledge-research-limitations --analysis-db .data/analysis.sqlite3

# train JSON의 model_version, snapshot_id를 아래 셸 변수에 넣습니다.
MODEL_VERSION='<model_version>'
SNAPSHOT_ID='<snapshot_id>'
dsv evaluate --mode historical-research --model-version "$MODEL_VERSION" \
  --analysis-db .data/analysis.sqlite3
dsv infer --mode historical-research --model-version "$MODEL_VERSION" \
  --snapshot-id "$SNAPSHOT_ID" --anchor-date 2024-12-27 --tickers 005930 \
  --analysis-db .data/analysis.sqlite3
dsv signals --scope research --model-version "$MODEL_VERSION" \
  --snapshot-id "$SNAPSHOT_ID" --direction up --analysis-db .data/analysis.sqlite3
```

`evaluate`는 고정된 train-fit 모델과 스냅샷의 validation/test를 재현합니다. 공통 날짜축 60/20/20 분할 후 라벨 미래 구간이 경계에 닿으면 purge합니다. PIT는 라벨 수신 시각도 격리합니다. 표준화·가중치는 방향별 train에서만 계산합니다. C=0.1/1/10을 validation AP로 선택하고 최종 test로 재선택하지 않습니다. 조건부 빈도·단순 역방향 강도 기준 모델, Precision·Recall·AP·혼동행렬·Brier·미보정 구간 진단을 보고합니다. 계산 불가능한 지표는 null과 사유를 반환합니다. 반복 실험은 동일 holdout에 대한 독립 검증이 아닙니다.

PIT는 확인한 품질 자료와 지속적으로 수신 시각을 보존한 데이터가 필요합니다. manifest 형식은 [데이터 계약](docs/data-contract.md#phase-2-품질-manifest)을 따릅니다. 부족한 품질을 연구 동의로 우회할 수 없습니다.

```bash
dsv --db .data/market.sqlite3 train --mode point-in-time \
  --tickers 005930 000660 035420 035720 005380 \
  --start 2019-01-02 --end 2024-12-30 --as-of 2026-09-23T09:00:00+09:00 \
  --quality-manifest .data/quality.json --analysis-db .data/analysis.sqlite3
# PIT_MODEL은 위 PIT 학습 결과의 model_version입니다.
dsv --db .data/market.sqlite3 infer --mode point-in-time --model-version "$PIT_MODEL" \
  --tickers 005930 --as-of 2026-09-23T10:00:00+09:00 \
  --quality-manifest .data/quality.json --analysis-db .data/analysis.sqlite3
dsv signals --scope analysis --mode point-in-time --model-version "$PIT_MODEL" \
  --as-of 2026-09-23T10:00:00+09:00 --analysis-db .data/analysis.sqlite3
dsv signals --scope operational --direction up --as-of 2026-09-23T10:00:00+09:00 \
  --analysis-db .data/analysis.sqlite3
```

모든 모델은 `unregistered`로 생성합니다. `validation_qualified`(두 기준 모델의 AP 최댓값보다 0.02 이상 높음)는 운영 적격 판정이 아닙니다. 실제 데이터 품질·PIT·고정 최종 테스트·별도 승인 증거가 없으므로 현재 운영 조회는 `no_registered_model`과 빈 목록을 반환합니다. 강제 등록 CLI나 자동 승격은 없습니다. 연구·합성 모델은 운영 등록 대상이 아닙니다.

새 명령의 stdout은 JSON입니다. 성공/정상 빈 조회는 종료 코드 0, 데이터·모델 부족/모드 위반/분석 불가는 1, 인자·파일·저장소 오류는 2입니다. 모델 파일은 pickle로 로드하지 않습니다. 실제 분석 시각·모델 생성 시각·시장 날짜·입력 상태·문맥·미보정 점수·선형 기여도를 함께 반환하며 기여도는 인과 설명이 아닙니다. 분석 DB는 시장 DB와 다른 경로를 요구합니다. `DSV_CODE_REVISION`은 배포 코드 SHA를 명시하는 선택 환경 변수이며 생략 시 Git SHA 또는 `unknown`을 기록하고 소스 hash도 보존합니다.

오프라인 기능 검증용 합성 fixture에만 `train --synthetic-test`로 작은 표본 정책을 명시적으로 허용합니다. 소스가 전부 `fake`여야 하고 `synthetic_test_only` 제한은 영구적입니다. 실제 종목 추천이나 성능 증거로 사용할 수 없습니다.

## 검증과 CI

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
python -m build
```

일반 테스트는 실제 키·네트워크 없이 실행되며 실제 TCP 접속 시도를 차단합니다. Mock HTTP 응답으로 KRX 헤더·필드 변환·재시도를 검증합니다. `tests/integration`의 일반 통합 테스트도 모두 로컬 SQLite/Mock을 사용합니다.

GitHub Actions는 `main` 대상 PR·main push·수동 실행에서 Python 3.12/3.13/3.14의 Ruff·mypy·pytest·패키지 빌드와 독립 가상환경 wheel 실행을 검사합니다. 기본 wheel에서 Phase 1, learning extra 설치 후 합성 학습→평가→시장 DB 삭제→추론→조회까지 검사합니다. 비밀키가 필요 없으며 live 테스트는 기본 제외됩니다.

승인 후 실제 API 테스트만 별도로 실행합니다. 이 테스트는 `.env`를 자동으로 읽지 않으므로 로컬 환경에 키를 export하세요(채팅·커밋에 키를 붙여넣지 마세요).

```bash
# KRX_API_KEY를 안전하게 환경에 설정한 후
DSV_RUN_LIVE=1 pytest -m live tests/integration/test_live_krx.py
```

이 테스트는 KOSPI 삼성전자의 2024-01-02 데이터를 수집·재조회하고 멱등성을 확인합니다. 일반 CI에서 실행하지 않습니다. 실제 데이터는 pytest 임시 경로에만 남으며 테스트 응답을 저장소에 커밋하지 않습니다.

## 구조와 정책

- [architecture.md](docs/architecture.md): 목표 구조와 Phase 1/2/3 경계, SQLite 선정 이유
- [phase2-design.md](docs/phase2-design.md): 승인된 v2 라벨·표본량·시간순 검증·운영 등록 계약
- [data-contract.md](docs/data-contract.md): 타입·시간대·원본·결측·거래정지·중복·수정주가 정책
- [providers.md](docs/providers.md): 공식 출처 기반 공급자 비교 및 KRX 매핑

`validate`는 SQLite·스키마·원본 체크섬·참조 연결을 검사합니다. 공식 거래일 캘린더나 종목 생애주기 마스터를 갖고 있지 않아 “모든 거래일이 빠짐없이 존재함”을 보증하지는 않습니다. 현재 종목 목록을 과거에 적용해 상장폐지 종목을 지우지 않습니다.


## Phase 3 연구용 가상 판단

[설계 v2](docs/phase3-design.md)와 [입력·정책 계약](docs/decision-contract.md)에 따라 Phase 2의 정확한 analysis_id를 소비합니다. 독립 임계값·예산·매도 방식·한도·TTL·비용·재진입 정책을 JSON으로 명시해야 합니다. 생략 시 WAIT/blocked이며 거래 수치 기본값은 없습니다. 손절·익절·최대 보유 기간은 각각 disabled/parameters=null만 허용합니다. 기본 패키지로 판단·조회·replay가 가능하며 새 모델 학습만 learning extra가 필요합니다.

아래 경로는 사용자가 준비한 불변 가상 snapshot과 명시 연구 정책입니다. 파일 구조와 합성 fixture는 계약 문서를 참고하세요. 날짜·ID·정책은 동일 연구 시나리오에 맞춰 지정합니다.

```bash
dsv decide --scope research --ticker 005930 --as-of '2024-01-08T16:00:00+09:00' \
  --analysis-id ANALYSIS_ID --analysis-db .data/analysis.sqlite3 \
  --decision-db .data/decisions.sqlite3 --policy research-policy.json \
  --account virtual-account.json --orders virtual-orders.json --market-context virtual-market.json

dsv decisions --decision-db .data/decisions.sqlite3 --scope research --ticker 005930
dsv decisions --decision-db .data/decisions.sqlite3 --scope research --decision-id DECISION_ID --replay
```

정상 연구 판단은 virtual/종료 코드 0, 차단은 WAIT/blocked/1, 파일·형식 오류는 2입니다. 기본 decisions 조회 scope는 operational이며 연구 결과로 대체하지 않습니다. 운영 decide는 항상 차단합니다. 모든 결과의 operational_eligible/executable은 false이고 실제 주문·계좌 변경은 없습니다. HOLD는 안전성 보장이 아니며 하락 문맥에서 청산 모델이 비적용인 경우 그 사실과 비활성 리스크 규칙을 diagnostics에 보존합니다.

별도 Decision SQLite에 입력·정책·결과를 원자적으로 저장합니다. 원본 시장/분석 DB를 수정하지 않으며 이후 DB·정책 파일 변경이나 삭제에도 replay는 최초 bundle을 사용합니다. 합성 테스트는 실제 매매 성능의 증거가 아닙니다. 당일 일봉, 운영 등록/철회 이력, 실제 리스크 청산 활성화는 후속 승인 대상입니다.
