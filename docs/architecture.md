# DonghakStockVision 2.0 아키텍처

## 목표 구조와 구현 경계

장기 목표는 일봉과 향후 실시간 데이터를 축적하고 가격·거래량·거래대금으로 시장 전환 신호를 학습하여 매매 판단을 만드는 것입니다.

```text
Phase 1 [구현]
 CLI → Settings → MarketDataProvider.fetch → raw_pages (원본)
                        ↓ parse
                 검증 / 정렬 / 중복 제거
                        ↓
              MarketDataStore → SQLite bars (정제)
                        └────→ collection_runs (성공/실패/빈 결과)

Phase 2 [구현]
 MarketDataStore.read → 불변 snapshot (별도 analysis SQLite)
   → features → 조건부 label → 날짜순 purge split → models / evaluation
   → research replay 또는 strict PIT inference → immutable signals
   → 연구 조회 / PIT 진단 조회 / 별도 registry를 통과한 운영 조회

Phase 3~5 [미구현]
 strategy → backtest / trading → 상시 운영
```

`src/donghak_stock_vision/` 아래 `config`, `data`, `providers`, `ingestion`, `validation`, `storage`가 Phase 1 모듈입니다. `features`, `signals`, `models`, `data.learning/snapshot`, `storage.analysis`, `learning_cli`가 Phase 2를 구성합니다. `strategy`, `backtest`, `trading`은 docstring만 있는 빈 패키지입니다. 기존 루트 실험 파일은 그대로 보존하되 새 패키지의 의존성·배포·자동 검증에 포함하지 않습니다.

실시간 시장 이벤트는 일봉과 시간 의미·정합성 요구가 다르므로 이후 별도 이벤트 계약을 설계해야 합니다. 이번에는 WebSocket·메시지 브로커·전략 기반 클래스·스케줄러를 만들지 않습니다.

## 경계와 데이터 흐름

- `DailyBar`: 불변 dataclass, 정수 KRW/주 단위, 명시적 가격 수정 상태.
- `MarketDataProvider`: `fetch`는 원본 bytes와 시각을 반환, `parse`는 일봉으로 변환합니다. 인증·HTTP·제공업체 필드명이 이 경계 밖으로 퍼지지 않습니다.
- `MarketDataStore`: 조회, 마지막 날짜, 원본 보관, 일괄 저장, 감사 이력, 무결성 점검의 작은 Protocol. 다른 저장소 어댑터로 교체 가능합니다.
- `Pipeline`: 종목별 원본 보관 → 전체 범위 파싱·검증 → 정렬/중복 처리 → 종목 단위 원자적 저장. 실패한 종목의 정제 데이터는 변경하지 않고 다음 종목을 처리합니다.
- `RetryingHTTP`: timeout 20초, 전송 오류/408/429/500/502/503/504만 최대 3회. 인증 오류·일반 4xx·데이터 형식 오류는 재시도하지 않습니다. 로그에 URL·헤더·서버 응답·임의 예외 문자열을 남기지 않습니다.
- `RequestGate`: SQLite 트랜잭션으로 요청 슬롯과 일일 횟수를 미리 예약합니다. 대기는 DB 잠금 해제 후 수행합니다. 실패·중단으로 실제 요청을 못 보냈어도 예약은 소비되며 과소 집계하지 않습니다.

KRX API는 시장 전체의 하루 데이터를 반환합니다. Provider 인스턴스는 최대 128일의 응답을 LRU 캐시하여 같은 실행에서 여러 종목이 공유합니다. 캐시는 인스턴스 수명의 조회 스냅샷입니다. 새 수집 실행은 새 Provider 인스턴스를 사용하세요(CLI는 매번 새 인스턴스). 장기간·대규모 수집은 작은 기간으로 나누어 실행합니다.

## SQLite 선정

| 항목 | SQLite | Parquet |
| --- | --- | --- |
| 날짜/종목별 재조회 | 기본키와 인덱스로 간단 | 파티션·별도 조회 계층 필요 |
| 증분 정정·중복 방지 | 트랜잭션, upsert, 복합 기본키 | 파일/파티션 재작성과 원자적 교체 필요 |
| 의존성·로컬 실행 | Python 표준 라이브러리 | 별도 컬럼 엔진 필요 |
| 대규모 분석 | 향후 확장 필요 | 컬럼 분석·압축에 적합 |

Phase 1은 안전한 작은 증분 갱신이 중요해 SQLite를 선택했습니다. SQL에 데이터와 API 키를 문자열 보간하지 않습니다. 정제 payload는 타입 직렬화 JSON이고 종목/날짜는 별도 복합 PK입니다. 실제 데이터량이 커지면 분석용 Parquet export 또는 PostgreSQL 어댑터를 별도 Phase에서 검토합니다.

원본과 정제 테이블은 논리적으로 분리합니다. 원본은 파싱 전에 별도 커밋하여 잘못된 응답도 조사할 수 있게 합니다. 정제 테이블과 원본 참조는 같은 트랜잭션으로 저장합니다. 동일 값의 재수집은 정제행과 최초 수집 시각을 유지합니다. 신규 raw/audit는 의도적으로 누적됩니다. 따라서 멱등성은 정제 데이터에 대한 보장입니다.

파일시스템 전체가 쓰기 불가능한 경우 실패 이력 저장까지 보장할 수는 없습니다. 이때 CLI는 저장소 오류로 종료합니다. 프로세스 강제 종료 시 마지막 완료 이력이 없을 수 있지만 SQLite가 미완료 정제 트랜잭션을 롤백합니다. 원본 보관·정제 커밋·완료 감사 이력은 하나의 분산 트랜잭션이 아닙니다.

## 범위와 운영 제한

단일 로컬 DB·순차 수집이 대상입니다. 공식 휴장 캘린더, 전 종목 자동 발견, 시장 이동 이력, 기업행사 조정 엔진, 실시간 데이터, 장기 자동 운영은 미구현입니다. `update`의 최신 날짜+겹침 구간은 최적화일 뿐 완전성 증명이 아닙니다. 과거 구간 복구는 `collect`로 수행합니다. 기간 전체 오류는 부분 성공처럼 처리하지 않습니다.

일일 요청 제한은 같은 DB에 대해서만 공유됩니다. DB를 복제·삭제하거나 다른 앱에서 같은 키를 사용할 때는 총합 한도를 사용자가 관리해야 합니다. HTTP 클라이언트는 리다이렉트를 따라가지 않아 인증 헤더가 다른 호스트로 넘어가지 않습니다.

## Phase 2 서비스 경계

`TrainingService(AnalysisStore).train(snapshot_id)`는 불변 snapshot만 읽어 특징·조건부 라벨·공통 분할을 생성합니다. 상승/하락별 최소량·train-only scaler·겹침/날짜 가중치·기준 모델·고정 C 후보를 적용합니다. dataset과 model은 한 트랜잭션에 저장하고 실패 보고도 별도 보존합니다. 모델 SHA는 JSON 수치·특징/라벨 버전·입력/분할·정책·코드/의존성 버전을 포함합니다. 실제 실행 시각은 별도 audit에 기록합니다.

`EvaluationService.evaluate(version, mode)`는 snapshot에서 dataset을 다시 구성하여 hash를 비교하고 고정 모델의 holdout 지표를 재현합니다. `SignalService.research(...)`는 동일 snapshot의 anchor까지 읽고, `point_in_time(MarketDataStore, ..., as_of, quality)`는 실제 가용 데이터로 새 입력 snapshot을 만듭니다. 둘 다 과거 시장 DB를 다시 파싱하거나 원시 JSON에 우회 접근하지 않습니다. `SignalQueryService`는 연구/PIT 진단/운영 범위를 분리합니다. 연구 조회는 기본적으로 종목별 최신 anchor이며 Python의 `anchor_range=(start,end)`로 범위 안의 저장 결과도 읽을 수 있습니다.

analysis DB의 `analysis_artifacts`는 내용 hash 기본키, 수정/삭제 차단 trigger 및 조회 checksum 검사를 사용합니다. snapshot/model/dataset/evaluation/signal과 실행 감사 이력 `analysis_executions`를 구분합니다. `operational_registry`는 방향별 모델·등록시각·검토 증거를 위한 별도 경계이며 이 Phase의 서비스에는 등록 쓰기 기능이 없습니다. validation 통과나 합성 테스트로 채우지 않습니다. 운영 조회는 최신 분석을 먼저 고른 뒤 신선도·문맥·등록을 확인하며 과거 적격 결과로 되돌아가지 않습니다.

단일 머신 SQLite와 메모리 내 학습을 대상으로 합니다. 분석 artifact는 원천 일봉과 행별 특징·라벨·평가를 함께 보관하므로 시장 DB보다 저장량이 늘어날 수 있습니다. 프로세스가 직접 DB trigger를 제거하거나 파일을 변조하는 관리자 공격에 대한 서명 저장소는 아니며, 임의 SQL로 registry를 채우는 것은 지원하는 등록 절차가 아닙니다. 대규모 패널·PIT revision 저장소·운영 등록 워크플로는 향후 별도 검토 대상입니다.
