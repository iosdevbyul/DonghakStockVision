# Phase 4 백테스트·모의거래 설계 초안

작성: 2026-09-28. 상태: **설계 검토용, 구현 및 정책 승인 전**. 이 문서는 구현 승인이나 거래 정책 확정이 아니다. 이번 변경은 문서뿐이며 엔진·체결·DB·CLI·테스트 코드를 만들지 않는다.

## 1. 조사 기준과 범위

원격 작업 없이 로컬에서 확인한 기준은 다음과 같다.

- 로컬 `origin/main`: `56a26e3f84eee5f57fee1f6491204e79eb780669` (PR #3 병합 기록). 이번 작업에서 fetch하지 않았으므로 현재 원격 최신 상태·후속 병합 여부를 주장하지 않는다.
- 최신 로컬 hardening: `b01780257edfc30f269df3ab7c6c024fca425edf`. 위 main 이력에 이 커밋을 더한 깨끗한 worktree에서 `feat/backtesting-design`을 생성했다. hardening 브랜치와 원본 worktree의 사용자 CI 수정은 보존했다. 로컬 main 브랜치는 이전 레거시 커밋에 머물러 있으며 수정하지 않았다.
- 따라서 이 설계의 선행 조건은 hardening 동작이다. 사용자는 후속 구현 전 실제 병합 기준을 정리해야 한다. 설계 브랜치에는 hardening 커밋이 조상으로 포함된다.

기존 자료: [아키텍처](architecture.md), [Phase 2 설계](phase2-design.md), [Phase 3 설계](phase3-design.md), [판단 계약](decision-contract.md), [시장 데이터 계약](data-contract.md).

| 조사한 구현 | 확인한 계약과 한계 |
| --- | --- |
| [MarketDataStore](../src/donghak_stock_vision/storage/base.py), [SQLiteStore](../src/donghak_stock_vision/storage/sqlite.py) | `read(ticker,start,end)`가 가격 입력 경계. 정제 bars는 최신 revision만 보관하며 raw 보관이 곧 PIT revision 조회 API는 아니다. 정정은 기존 값을 덮어쓰고 같은 값 재수집은 최초 collected_at을 보존한다. |
| [DailyBar](../src/donghak_stock_vision/data/schema.py), [normalize](../src/donghak_stock_vision/validation/bars.py) | 서울 기준 전일 이하 일봉만 허용. KRW/수량 정수, OHLC 관계, 메타데이터 혼합·충돌 중복 검증. 무거래 0 OHL/양수 참조 종가 허용은 체결 가능 의미가 아니다. 큰 가격 변화는 Phase 1에서는 경고다. |
| [snapshot](../src/donghak_stock_vision/data/snapshot.py), [features](../src/donghak_stock_vision/features/engine.py) | snapshot 불변 사본, 수신 cutoff, 품질 manifest. unknown/미검증 adjusted 차단. 특징 최소 11관측, 경계 품질 확인은 최대 12행. 무거래·의심 불연속·검증 세션 누락은 분석 제외. |
| [dataset](../src/donghak_stock_vision/signals/dataset.py), [TrainingService](../src/donghak_stock_vision/models/service.py) | 조건부 상승/하락, 미래 5세션 장벽 라벨, 날짜순 60/20/20과 label_end/가용 시각 purge. train만 fit, validation으로 선택. 최소 표본량 검사에는 test의 클래스 수까지 포함된다. |
| [SignalService/조회](../src/donghak_stock_vision/signals/service.py), [AnalysisStore](../src/donghak_stock_vision/storage/analysis.py) | research는 모델과 동일 snapshot_id만 허용, 해당 날짜 일봉이 있어야 하며 서울 16:00 anchor. PIT는 실제 모델 생성 시각과 전일 이하/수신 cutoff 검사. artifact 실제 생성 시각과 anchor는 다르다. |
| [불변 계약](../src/donghak_stock_vision/strategy/contracts.py), [decide](../src/donghak_stock_vision/strategy/engine.py), [정책](../src/donghak_stock_vision/strategy/policy.py) | canonical JSON 기반 불변 값, 순수 판단. long-only 현금주식, 추가 매수/공매도 없음. 모든 운영 요청 차단. 독립 임계값·Q2·TTL·비용·한도·재진입·disabled 리스크 규칙을 그대로 사용한다. |
| [adapter](../src/donghak_stock_vision/strategy/adapter.py) | 정확한 analysis_id를 읽기 전용으로 로드하고 모델·snapshot·계약·가격 기준을 검증. 운영 요청이면 registry도 읽지 않는다. 실제 artifact_available_at은 provenance에 있으나 Phase 3가 그 시각을 모든 연구 요청에 차단 기준으로 적용하지는 않는다. |
| [DecisionStore](../src/donghak_stock_vision/storage/decision.py), [hardening 테스트](../tests/strategy/test_storage.py) | 같은 ID의 6개 필드 완전 일치 후 감사만 추가, 불일치 전체 rollback. 조회는 UTC datetime 비교·동일 시각 ID 순·정렬 후 limit. bundle/identifier 재작성 없이 기존 checksum/replay 유지. |

[Phase 3 결정표/순수성 테스트](../tests/strategy/test_engine.py), [어댑터·CLI·원본 DB 삭제 후 replay 테스트](../tests/strategy/test_adapter_cli.py), [Phase 2 모델/PIT 테스트](../tests/learning/test_models_signals.py)도 조사했다. 테스트 fixture의 시각·가격·거래 가능 플래그는 합성 가정이지 실제 시장 적격성 근거가 아니다.

### 문서와 구현의 차이 및 추가 제약

1. Phase 3 설계의 상태 문구는 아직 “구현 진행”이지만 조사 기준에는 구현 및 hardening이 있다. 과거 문서의 작성 시점 기록을 현재 원격 상태로 해석하지 않는다.
2. 판단 계약의 “6자리 ticker”는 구현상 숫자 전용이 아니라 `[0-9A-Z]{6}`이다. Phase 4도 기존 검증을 사용한다.
3. Phase 2 설계에는 명시 날짜 분할 가능성이 기술되어 있으나 현재 `train(snapshot_id)`와 `split_dataset(samples,mode)`에는 외부 분할 경계 인자가 없다. walk-forward 설정을 전달할 수 있다고 가정하지 않는다.
4. Phase 2 설계의 운영 등록/철회 설명은 실제 시점별 이벤트 이력보다 넓다. 현재 registry는 방향별 행과 evidence 조회이고 불변 승인·철회 이력/공개 증거 검증 경로가 없다. Phase 4는 registry를 우회하거나 운영 적격성을 만들지 않는다.
5. Phase 3 문서의 청산 이력 시간 조건 외에 실제 `reentry`는 last_exit.received_at≤orders.received_at도 요구한다. 전체 history_complete 검사는 매수 후보의 재진입 검사에서 수행된다. Phase 4 계좌 원장은 이력 완전성을 지속 보장한다.
6. `DecisionInput` 생성자는 요청 식별을 주로 검증하고 상세 값은 `decide`에서 차단한다. 별도 체결 적격성 검증기가 이미 있다고 가정하지 않는다. market의 `session="open"`/`quality_status="verified"`는 입력 플래그 검사이지 거래소 상태를 조회한 증거가 아니다.
7. Phase 3의 주문 잔량은 terminal 상태에서 0이어야 한다. Phase 4의 취소 수량·미체결 종료 수량은 별도 원장에 남기고 Phase 3 projection의 remaining_quantity는 활성 잔량만 뜻하게 해야 한다.

기존 문서/코드는 이번 작업에서 교정하지 않는다. 아래는 이 실제 제약에 맞춘 제안이다.

## 2. 두 실행 방식과 정보 모드

| 구분 | 목적 | 진행 방식 | 허용 주장 |
| --- | --- | --- | --- |
| 과거 백테스트 | 고정된 과거 입력에서 판단→주문→가상 체결→계좌 변화를 재현하고 정책 가정을 비교 | 가상 clock을 사건 순으로 빠르게 전진. 전체 tape는 저장되어 있어도 전략에는 당시 공개된 prefix만 제공 | 사후 연구 또는 입증된 archived PIT 연구라는 범위 안의 결과 |
| 순차 모의거래 | 새 입력 도착·지연·중복·재시작·예약/부분 체결 처리를 검증 | 수동 step 또는 유한 배치 ingest로 전진, checkpoint를 지속. 과거로 되돌리지 않음 | 실제 자금 없는 paper 결과. 실거래/실제 체결 증거 아님 |

실행 방식(`backtest`/`paper`)과 정보 모드(`historical_research`/`point_in_time`), 출처(`real`/`synthetic`)는 독립 필드다. paper라는 이름만으로 PIT를 보장하지 않는다. 사후 snapshot을 순차 공급한 paper는 historical_research다. synthetic_test_only는 모든 주문·체결·보고서까지 전파하며 실제 데이터와 같은 run에 혼합하지 않는다.

초기 후보는 **명시 가정의 historical 고정 모델 replay와 수동 paper step**이다. 검증된 archived PIT 자료 또는 앞으로 실제 수신한 자료가 있으면 별도 PIT 연구 run을 허용하는 설계를 제안한다. 현재 DB만으로 자료가 충분하다고 선언하지 않는다. 자동 수집·재학습·스케줄러는 여기서 만들지 않는다.

## 3. 바꾸지 않는 경계와 제안 구조

```text
Phase 1 read → 고정 시장/품질/종목군 manifest ─→ 시각 제한 MarketView
Phase 2 고정 model/snapshot/analysis ─────────→ AnalysisView (정확한 ID)
Simulation ledger ─→ 가상 계좌/주문/시장 projection
                             ↓
                 Phase 3 assemble → decide → DecisionStore
                             ↓ (연구 결과 그대로, executable=false)
               SimulationAdmission → VirtualOrder
                             ↓
              별도 FillModel / 취소·거부 이벤트
                             ↓
              별도 SimulationStore 원장·checkpoint
                             ↓
                   Valuation / Metrics / Replay
```

그림의 Phase 4 컴포넌트/이름은 **제안 인터페이스**이며 이번에 생성하지 않는다. 오케스트레이터와 I/O를 분리하고 `admit`, `apply_event`, `evaluate_fill`, `project_account`, `calculate_metrics`는 고정 입력에 대한 순수 계산으로 설계한다. clock과 sequence는 외부에서 명시 전달한다.

`assemble`·`decide`·`DecisionStore`를 수정하지 않고 사용한다. 요청 scope는 research, snapshot origin은 virtual, policy는 승인된 명시 정책이다. Phase 2의 0.5·라벨 H=5를 매매 임계값/보유 기간으로 복사하지 않는다. HOLD의 청산 신호 부재·disabled 리스크 규칙·safety_assurance=false 진단을 주문·보고서가 지우지 않는다.

**실행 허가와 시뮬레이션 입력은 다른 계약이다.** Phase 4의 별도 연구 admission은 `status=virtual`, `decision_eligible=true`, BUY/SELL, 유효 정수 제안 수량, 고정 정책/계좌 revision이 일치하는 경우에만 가상 주문 후보를 만든다. 그때도 원본 `executable=false`, `operational_eligible=false`, `execution_blockers`를 보존한다. SimulationOrder에는 `virtual=true`, `broker_route=null`을 두며 실제 주문 코드·플러그인·승인 우회 경로는 없다. 운영 scope와 WAIT/blocked는 admission 불가다.

HOLD/WAIT는 주문 생성·체결·취소 명령이 아니다. 기존 가상 주문의 만료/취소/체결은 독립 사건과 사전 정책에 따른다. 시스템 입력 불완전/품질 불명으로 blocked일 때는 해당 종목 신규 admission/체결도 검증기가 막지만, 정상 WAIT만을 이유로 이미 접수한 주문을 취소하지 않는다.

## 4. 시각과 미래 정보 격리

### 시각 필드

모든 순간은 timezone-aware UTC 비교/저장, 거래일과 캘린더는 Asia/Seoul이다. 같은 순간은 단조 event_seq로 순서를 구분하고 timezone 문자열 사전순·float epoch 비교를 쓰지 않는다.

| 필드 | 의미 |
| --- | --- |
| event_at | 거래/시장 사건이 발생한 시각. 일봉 날짜만으로 장중 시각을 입증하지 않음 |
| source_received_at | 실제 원천 수신 시각. collected_at 등 기존 값을 보존 |
| available_at | 해당 run의 정보 정책상 사건이 공개되는 시각 |
| analysis_anchor_at / feature_cutoff | Phase 2 분석 기준. 실제 계산 완료 시각과 구별 |
| artifact_created_at | 실제 분석/모델 최초 생성 시각. 복사/재계산으로 과거화 금지 |
| decision_as_of | Phase 3에 제공하는 판단 cutoff |
| submitted_at / accepted_at | 가상 주문 요청/접수 시각 |
| fill_at / fill_known_at | 가상 체결 발생 가정과 그 사실을 원장에 반영할 수 있는 공개 시각 |
| recorded_at / computed_at | 이번 실행의 실제 감사 clock. 경제적 replay identity와 분리 |

같은 timestamp라도 `public event → account projection → decision → submission`의 인과 순서를 기록한다. 결정이 관측한 동일 가격 사건으로 즉시 체결을 역산하지 않는다. 체결 eligibility는 접수 이후의 사건/sequence이어야 하며 경매 등 예외가 필요하면 별도 사전 주문 계약과 승인 대상이다.

### Historical Research

실제 collected_at/모델 생성 시각은 과거 가상 clock보다 늦을 수 있다. 원본을 수정하지 않고 별도 `availability_assumption_id`에 어떤 일봉 필드를 언제 공개한다고 가정했는지 적는다. feature 입력은 anchor까지, 체결용 이후 OHLC/거래량은 격리된 실행 측 tape만 참조한다. 연구용 16:00 anchor는 공식 일봉 배포·체결 가능 시각이 아니다.

전체 일봉을 가상 시가 이벤트에 공개해서 고가/저가/종가/일 거래량을 결정·매수 수량 계산에 사용하지 않는다. 시가만 공개하는 가정도 당시 수신 가능성을 입증하지 못하므로 historical 가정으로 표시한다. 사후 수정주가·종목 생존·품질 필터 편향은 snapshot 고정만으로 제거되지 않는다.

### PIT backtest / PIT paper

자료·revision·품질 증거는 source_received_at/available_at≤cutoff, 모델은 실제 created_at≤feature_cutoff, 분석은 실제 artifact_created_at≤decision_as_of여야 한다. 학습·선택에 사용한 모든 라벨이 모델 가용 전 성숙했는지도 별도로 검사한다. Phase 3 어댑터의 provenance만 저장하는 동작을 PIT 가용성 입증으로 대체하지 않는다.

PIT 분석 요청 시각 f 이후 실제 계산이 끝나는 시각 a가 생긴다. 판단 d는 a 이후에만 하며 원본 anchor=f를 d로 덮어쓰지 않는다. f와 d의 차이는 기존 analysis TTL/max_skew로 검사한다. `_latest`의 anchor cutoff만으로 분석 가용성을 보장하지 않으므로 Phase 4는 고정 analysis_id와 원본 created_at을 확인한다.

DailyBar와 PIT inference의 **A1 전일 이하 정책을 유지**한다. 장 마감 뒤라는 이유로 당일 일봉을 입력하지 않는다. 현재 latest-only 시장 DB에서 과거 revision이 사라졌다면 raw를 임의 복원하거나 받은 시각을 앞당기지 않고 `pit_history_unavailable`로 중단/차단한다. 사후 모델을 PIT 과거로 backdate하지 않는다.

일봉만 있고 당일 quote·거래 가능 상태·시점별 캘린더/상장 정보가 없으면 실시간에 가까운 paper 체결을 입증할 수 없다. PIT 진단/WAIT만 기록하거나 run을 차단한다. historical로 자동 fallback하지 않는다. 추가 시장 문맥 입력 계약은 별도 승인/자료 확보가 선행되어야 하며 WebSocket 구현을 포함하지 않는다.

### 검증 절차 제안

1. 시작 전 manifest의 모델/feature/label/정책/캘린더/종목군/가격 기준 hash 및 정보 모드를 고정한다.
2. 각 step에서 모든 전략 입력의 최대 available_at와 market revision, 품질 증거, 모델 생성/선택 cutoff를 확인한다. 미래 참조는 해당 step 전체 rollback/명시 오류다.
3. account/orders는 이미 commit된 이전 step prefix에서만 projection한다. 미확정 fill·미래 청산을 sellable/cash/last_exit에 넣지 않는다.
4. 전략과 admission에는 관측 prefix만, fill evaluator에는 접수된 주문과 현재 평가 가능한 실행 사건만 제공한다. 이후 종가/고저/일 거래량을 admission에 노출하지 않는다.
5. 테스트 계획: cutoff 뒤 가격·거래량·labels·계좌 사건을 바꿔도 이전 decision/order hash가 동일함을 확인한다. 미래 주문 접수·동일 사건 체결·지연 수신·정정·timezone 경계를 포함한다.
6. 품질 실패·중단 구간·자료 부족을 분모에서 몰래 삭제하지 않고 보고한다. Phase 3 스코어/문맥을 재계산하거나 null을 0으로 바꾸지 않는다.

## 5. Phase 3 재사용의 장애와 대안

| 계약/장애 | 그대로 재사용하는 경로 | 별도 승인 없이는 하지 않는 것 |
| --- | --- | --- |
| research anchor는 EOD, decide는 session=open 필수 | 분석을 보존하고 이후 실제/가정상 열린 세션에서 decision. analysis TTL/max_skew/price TTL은 사용자가 명시 | 닫힌 세션을 open으로 위장, anchor/관측 시각 덮어쓰기, TTL 자동 연장 |
| 판단 가격과 체결 가격이 다름 | 관측된 reference로 Q2 제안량 산출, 이후 fill 조건/가격에서 현금/예약 재검사 | 같은 종가/시가 체결 보장, 갭 비용을 숨기기 |
| research는 모델과 동일 snapshot만 추론 | 동일 snapshot 내 고정 모델 탐색적 replay를 연구 제한으로 수행 | prefix 학습 모델에 다른 미래 snapshot을 몰래 붙이기 |
| 학습 API 분할 고정 및 test 표본량 gating | 실제 train/validation/label_end와 model 생성 근거를 공개하고 실행 전 모델 선택 고정 | 전체 snapshot 모델로 과거 전 구간 거래 후 clean OOS라고 주장 |
| PIT 모델/분석의 실제 생성 시각 | 이미 가용했던 artifact 또는 앞으로 생성한 모델부터 순차 paper 시작 | 현재 만든 모델의 시각 조작, post-hoc 연구를 PIT로 표기 |
| `cash+reserved+gross=equity`, 단일 기준가격의 종목 평가 | 모든 보유 종목의 as-of mark를 고정하고 현금 예약을 한 번만 차감하여 projection | 미수·부채·외화·알 수 없는 평가액을 0으로 채움 |
| `post_action_strict`는 SELL/HOLD에도 적용 | 한도를 여전히 위반하면 WAIT/blocked를 보존, 위험 지속을 보고 | 강제 축소 주문·손절·익절·기간청산을 Phase 4에서 암묵 활성화 |
| 동일 종목 pending은 차단, 추가 매수 금지 | 기존 BUY 주문의 잔여 부분 체결은 동일 주문의 진행으로만 처리 | partial 후 새 BUY 결정을 만드는 우회 |
| 서로 다른 DB의 자체 commit | 9절의 준비 artifact + SimulationStore 단일 step commit | DecisionStore 연결에 simulation 테이블을 추가하거나 분산 ACID라고 주장 |

**연구 검증 범위의 중요한 구분:** 현재 TrainingService는 test까지 최소 클래스 수를 확인하고 test 지표도 생성한다. 전체 동일 snapshot 모델의 test 구간에 주문을 붙이는 것만으로 독립적인 전략 OOS 검증이 완성되지 않는다. 모델 존재/선택과 정책이 미래 test 결과·표본량에 영향받았는지를 밝혀야 한다. fit/validation label_end 이후 거래라는 조건은 필요하지만 이것만으로 충분하지 않다.

깨끗한 walk-forward 대안은 (a) cutoff 이전 자료만으로 완료된 모델을 별도 이후 snapshot에서 frozen 추론하는 **Phase 2 계약 확장**, 또는 (b) 실제 생성 시각 이후 자료로 수행하는 prospective PIT paper다. (a)는 현재 research snapshot 동일성 계약과 충돌하므로 별도 설계·승인·회귀 PR 선행 사항이며 Phase 4가 비공개 `_infer` 호출/메타데이터 변경으로 우회하지 않는다. 최초 범위는 이 승인 여부에 따라 탐색적 historical replay 또는 준비된 PIT 자료로 제한한다.

## 6. 판단 시점과 체결 모델 선택지

다음은 비교 대상이며 채택된 거래 기본값이 아니다. 거래소 개장 시각·지연·가격 단위·거래 한도 숫자는 검증된 캘린더/규칙과 명시 정책으로 제공받는다.

| 후보 | 데이터와 인과 조건 | 한계/승인 항목 |
| --- | --- | --- |
| 열린 세션에서 결정, 접수 후 다음 적격 세션 시가 | 이전 분석과 알려진 reference로 결정. 다음 시가는 fill 측에서만 사용 | 구현이 단순한 초기 후보. 다음 날 시가를 본 뒤 결정했다면 그 시가에 체결하지 않고 더 뒤의 세션으로 미뤄야 함. 갭·예약 부족·거래량 처리 필요 |
| 열린 세션 결정, 이후 별도 관측 quote에서 체결 | 순차 quote의 수신/호가·수량 증거 필요 | 지연 paper에 자연스럽지만 현재 Phase 1 일봉만으로 자료 부족. HTTP/증권사/실시간 수집 구현은 별도 |
| 일봉 종가·장중 범위 기반 체결 | 접수 마감과 사건 순서·주문 종류를 사전 정의하고 해당 bar가 확정된 후 평가 | high/low만으로 touch 순서·체결을 보장하지 못함. 같은 봉 양쪽 조건·gap·queue는 모호 상태. 추가 설계 승인 전 제외 제안 |

다음 시가 후보의 예: t EOD 연구 분석 → t 다음 열린 세션의 결정(기존 anchor 보존) → 가상 접수 → **접수보다 뒤인** 지정 세션 시가 사건 → 가상 체결 검증. reference가 이전 종가이면 price_observed_at도 그 종가의 가상 공개 시각을 유지하고 TTL 통과 여부를 그대로 검사한다. 시가를 이미 결정에서 관측했다면 동일 시가 사건은 사용하지 않는다. 이 지연은 모델 라벨 시작 세션과 다를 수 있으며 성과 해석에 명시한다.

일봉 전체 volume을 다음 시가 체결량 상한으로 쓰는 것은 그 시점의 관측 유동성이 아니다. 대안은 사전 과거 volume 기반 주문 상한(실제 시가 체결 보장 아님), 별도 시가 유동성 증거, 명시 무제한 유동성 가정의 민감도 실험, 또는 일봉 확정 후 지연 체결 확인이다. 마지막 방식은 `fill_at`을 가정하더라도 `fill_known_at` 전 원장 현금/보유 변화와 당일 재매매를 허용하지 않는다. 어떤 대안도 자동 기본값으로 고르지 않는다. 일중 경로가 필요한 지정가·손절 체결 모델은 초기 범위에서 제외하는 안을 제안한다.

## 7. 주문·체결·원장 상태와 정책

### 상태 전이 제안

| 사건 | 가상 주문 상태 | 경제적 효과 |
| --- | --- | --- |
| 유효 BUY/SELL + 별도 admission 통과 | submitted → open | 접수 시점에 예약만 생성. decision 자체는 현금/보유를 변경하지 않음 |
| admission/규칙/자금 실패 | rejected | 새 예약 없음 또는 기존 접수 예약의 원자적 해제 |
| 적격 fill 일부 확인 | partial | 확인 수량만 현금·비용·보유 반영, 잔여 예약 유지/명시 규칙으로 조정 |
| 남은 수량 전부 확인 | filled | 잔량/예약 0, fill 및 원장 commit |
| 취소 요청 | cancel_pending | 취소 확인 전 예약 유지, 경합 fill 가능성은 사건 순서로 판단 |
| 취소 확인/정책 만료 | cancelled / expired | 미체결 잔여 예약만 해제. 이미 체결된 수량은 되돌리지 않음 |
| 결과 불명·불완전 기록 | unknown | 예약을 보수적으로 유지, 해당 판단 및 불명 영향의 후속 진행 차단 |

expired는 Phase 4 감사 사유를 유지하되 Phase 3 orders projection에서는 cancelled + remaining_quantity=0으로 투영한다. 취소/거부 총수량, 원래 주문량, 누적 체결량은 별도 필드다. `ordered = filled + active_remaining + cancelled_or_rejected_quantity`를 검사한다. 취소 요청과 fill의 동시 timestamp는 사전에 고정한 sequence/우선순위 없으면 모호 오류이며 유리한 쪽을 고르지 않는다.

중복 방지 키는 run_id/decision_id/action/가상 주문 슬롯이며 동일 키에 다른 내용은 오류다. 계좌 revision을 확인하고 다종목 판단·예약을 명시 순서로 직렬 처리하는 안을 제안한다. 동일 현금을 사용하는 독립 BUY 여러 개를 모두 승인하지 않는다. 순서(종목 고정 순서 등), 동률, 일괄 예산 배분 대안은 승인 사항이며 검증되지 않은 점수를 종목 간 공통 확률 순위로 사용하지 않는다.

### 명시 정책 계약 제안

| 항목 | 필요한 정책·검증 | 미확정/자료 없음의 동작 |
| --- | --- | --- |
| 가격/시점 | reference 근거, fill benchmark, 최초 가능 세션, latency, 주문 유효기간, 가격 단위/거래 제한 버전 | admission/체결 보류 또는 run 차단. 임의 가격·시각 생성 금지 |
| 비용 | buy/sell 수수료·세금·고정비·반올림·유효 기간·출처, 주문별/체결별 부과 단위 | 0으로 채우지 않음. Phase 3 예상 비용과 Phase 4 실제 가상 비용의 차이를 분리 보고 |
| 슬리피지 | 방향·benchmark·적용식·단위·가격 한계와 gap 처리 | 비용 중복 차감 금지. 체결가에 포함된 슬리피지는 별도 현금 비용으로 다시 차감하지 않음 |
| 유동성 | 수량 상한·참여율·근거 관측 구간·복수 주문 간 공유 용량·부분 체결·잔량 처리 | 일봉 volume/거래대금은 거래소 queue 증거 아님. 미래량으로 결정 수량 조절 금지 |
| 예약 | 수수료/세금 포함 매수 예약 상한, sellable 주식 예약, 갭 후 부족 시 거부/잔량/부분 처리 | Phase 3 제안량을 임의 재사이징하지 않음. 수량 일부 체결은 승인된 fill 규칙만 허용 |
| 결제 | 즉시 가상 결제 또는 지연 결제·매도대금 재사용 시각·sellable 정의 | 실제 결제 규칙을 추정하지 않음. 초기 즉시 가상 결제안은 승인 필요. 미수/미결제 자산을 Phase 3 cash로 숨기지 않음 |
| 기업행사/조정 | 시점별 상장·정지·폐지·분할/병합·배당 증거, 유효/수신 시각, 단위 변환 기준 | 관측 누락을 폐지/정지로 확정하지 않음. 미검증 구간 신규 체결 차단, 보유는 유지하되 run/평가 불완전 표시 |
| 종료 | 주문 잔량 만료/유지, 보유 평가, 강제 종료 청산 여부 | 마지막 종가 자동 청산 금지. 강제 청산 시나리오는 전략 신호와 분리하는 별도 승인 정책 |

수수료 고정액을 부분 체결마다 다시 부과하는지, 누적 주문 비용에서 이미 부과한 금액을 빼는지에 따라 결과가 달라진다. `cost_charge_id`로 한 번만 계상하고 gross notional·fee·tax·slippage 분석량을 분리한다. 비용 가정은 주문 접수 및 체결 유효 시각에서 모두 확인한다. Phase 3 예상 비용은 예약 검증의 참고이며 체결 확인 때만 원장 비용으로 인식한다.

예약 시 cash_available↓/cash_reserved↑, 자산총액은 불변이다. 매수 fill은 예약금에서 실제 notional+fee+tax를 차감하고 원가/수량을 늘린다. 매도 fill은 보유와 주식 예약을 줄이고 proceeds-fee-tax를 결제 정책에 따라 반영한다. 예약 해제만으로 손익이 생기지 않는다. 재시도/부분 체결에서 이 효과가 두 번 적용되어서는 안 된다.

long-only, 정수 수량, 보유 중 새로운 추가 매수/공매도 금지는 유지한다. 동일 주문 잔여 fill은 재진입이 아니다. 수량이 실제 가상 fill로 0이 된 확인 사건만 last_exit로 투영하고 부분 매도/SELL decision만으로 청산 완료를 만들지 않는다. 청산 수신 시각≤orders snapshot 수신 시각을 유지한다.

### 거래정지·상장폐지·수정주가

- 공식/검증 자료 없이는 no-trade bar를 거래정지로 확정하지 않는다. volume=0이면 거래량 기반 체결은 0이며 종가가 있다는 이유로 거래시키지 않는다. missing bar를 건너뛰어 유리한 다음 가격을 즉시 사용하지 않는다.
- 신규 상장은 Phase 2 warm-up 부족 상태를 보존한다. 종목군 포함/제외의 effective_at/known_at을 고정하며 오늘의 생존 종목만 사용한 연구는 생존편향을 명시한다.
- 폐지 시 현금 회수/정리매매/최종 평가를 근거 없이 0 또는 마지막 가격 체결로 만들지 않는다. 알려진 회수 사건은 별도 원장 사건, 알 수 없는 보유 가치는 unavailable로 표시하고 최종 성과를 완전하다고 발표하지 않는다.
- adjusted 신호 가격을 unadjusted 체결 가격/주식 수와 혼합하면 Phase 3 price_basis도 맞지 않는다. 초기 후보는 검증된 unadjusted·행사 없는 구간이다. adjusted 경제적 체결은 검증된 시점별 조정계수/주식·현금 행사 원장이 갖춰질 때 별도 승인한다. 종가 비율로 계수를 만들지 않는다.
- 행사 미확인 보유를 포트폴리오에서 삭제하지 않는다. frozen/stale 마지막 mark는 참고 표시만 가능하고 신선한 가격으로 재표기하지 않는다. 의사결정 TTL 및 성과 품질 규칙을 통과하지 못하면 차단/미완료다.

## 8. 데이터 계약과 평가 원장 제안

아래는 향후 불변 모델/테이블 역할이며 구현된 schema가 아니다. 금액/가격은 Decimal 문자열, 수량은 기존 정수 범위, 상태·시각·출처·원인 ID를 명시한다. 임의 pickle을 사용하지 않는다.

| 계약 | 주요 필드 |
| --- | --- |
| RunManifest | run_id, 실행 방식/정보 모드/origin/restriction, source snapshot 및 bundle hashes, 코드/환경/정책/캘린더/종목군 버전, 모델 계획, 기간·초기 현금/포지션·시점, 가정·승인 기록, 숫자 정밀도/난수 사용 시 seed |
| MarketEvent | event_id/seq, ticker, event_at/received_at/available_at, 원본 hash/revision, 공개 필드, 시장·세션·상장·정지 상태와 증거, 가격 단위/adjustment |
| SimulationAccount | account_id, revision, available/reserved cash, 결제 대기 항목(지원 시 별도), 보유·주식 예약·원가, valuation hash, observed/received_at, completeness |
| DecisionLink | run/step/decision_id, input_bundle_id/policy_id, 정확한 model/snapshot/analysis IDs, 계좌 revision, 원본 diagnostics·품질 flags, 가상 snapshot hashes |
| VirtualOrder | order_id/idempotency key, decision_id, side/원래 수량/잔량/누적 fill, 상태, 제출/접수/만료/취소 시각, 예약, 주문·체결 정책 ID, virtual=true |
| VirtualFill | fill_id/order_id, seq, quantity/price/notional/fee/tax, benchmark/slippage 진단, fill_at/fill_known_at, 사용 사건·유동성 증거, simulation_only 표시 |
| LedgerEvent | event_id, 이전 state hash, 현금·예약·수량·원가 증감, 원인 order/fill/corporate_action, effective/available/recorded_at, 비용 charge ID |
| Position/Valuation | ticker, quantity/sellable/reserved, 선택된 원가 방식, mark/가격 시각/기준, gross_exposure, 실현·미실현 손익, stale/unknown 상태 |
| Checkpoint/Report | 마지막 committed seq/state hash, manifest hash, running/paused/failed/completed/incomplete, metric 정의 버전·평가 범위·누락 사유·의존 ID |

평균원가 방식은 Phase 3 표시 평균가와 계산 일치가 쉬운 후보이며 FIFO 등 다른 방식은 대안/승인 사항이다. 초기 보유분은 수량·취득 원가·실제로 매도 가능한 수량·평가 근거를 명시한다. 알려지지 않은 원가를 0으로 두어 승률/손익을 만들지 않는다.

외부 입출금이 없는 실험을 초기 후보로 제안하되 사용자가 초기 현금/보유와 입출금 정책을 승인해야 한다. mark는 판단 reference와 동일 기준이어야 하고 다른 보유 종목도 cutoff 내 가격으로 평가한다. Phase 3에 전달하는 `equity=available_cash+reserved_cash+gross_exposure`가 성립하지 않는 결제/자산 모형이면 projection을 차단한다. 매도대금 미결제 자산을 지원하려면 별도 호환 설계가 필요하다.

## 9. 저장소 분리·원자성·replay

**제안:** 시장 DB, analysis DB, 기존 Decision DB, 신규 Simulation DB를 별도 파일/책임으로 유지한다. 경로·symlink·hardlink 동일성 검사를 Phase 4 경계에도 적용한다. 기존 DecisionStore는 두 테이블 외 schema를 거부하므로 여기에 주문/체결 테이블을 추가하지 않는다.

시장 입력은 MarketDataStore.read를 통해 고정 사본으로 캡처한다. 실행 시 최신 시장 DB를 다시 읽어 같은 과거 run의 값을 바꾸지 않는다. 이미 존재한 analysis/model의 created_at을 보존해야 할 때는 검증된 SQLite 일관 사본과 원본 provenance를 사용한다. `AnalysisStore.put`로 복제하면 생성 시각이 달라지므로 이를 원래 가용 시각처럼 쓰지 않는다. 신규 분석이 필요하면 run 전용 analysis 저장소에서 기존 서비스로 생성하고 실제 생성 시각을 기록한다. 원본 시장/분석/Decision DB의 기존 artifact를 덮어쓰지 않는다.

SimulationStore의 authoritative 기록은 append-only 사건과 불변 manifest/bundle, 가변 projection/checkpoint는 사건으로 재구축 가능하게 한다. 각 step은 다음 순서로 제안한다.

1. 관측 prefix와 이전 committed account revision으로 analysis/decision을 계산한다. DecisionStore.save로 불변 bundle을 먼저 준비하고 ID/hash를 확인한다.
2. Simulation DB 한 트랜잭션에서 기대 revision/seq를 검사하고 decision link 및 입력·정책·결과의 정확한 사본, admission/주문/예약 또는 fill/비용/포지션/평가 변화, 새 checkpoint를 함께 commit한다.
3. 실패 시 step 경제적 효과는 전부 rollback한다. Decision DB에 준비된 orphan artifact/실행 감사가 남을 수 있으나 가상 주문이나 체결로 간주하지 않는다. retry는 같은 step/이벤트 키와 같은 내용만 수용한다.
4. commit 뒤 성공 응답 전에 중단돼도 재개는 committed 키를 확인하여 감사 시도 외 경제적 효과를 중복 적용하지 않는다. 다른 내용의 동일 키는 명시 충돌이다. 충돌/실패 진단은 별도 attempt 기록이며 checkpoint를 앞당기지 않는다.

두 파일의 독립 commit을 하나의 분산 ACID로 표현하지 않는다. 이 준비 artifact 방식과 단일 Simulation DB 내 원자적 경제 원장을 권장안으로 제시하며 승인 전 구현하지 않는다. 여러 ticker를 한 step으로 묶는 경우 정책에 따른 순서를 먼저 고정하고 해당 step의 예약/체결 효과를 같은 트랜잭션으로 다룬다.

재현 수준은 세 가지로 구분한다.

- **Decision replay:** 기존 DecisionStore.replay로 저장된 입력/정책을 재계산하고 동일 결과 확인. hardening의 정확 일치와 최초 created_at 보존을 사용한다. query의 시간 동치가 다른 원문 bundle의 ID를 합친다는 뜻은 아니다.
- **Ledger replay:** 고정 사건·정책·순서로 매 step state hash/계좌/비용/주문 상태를 재구성. 최신 데이터·새 정책·다른 엔진으로 조용히 대체하지 않는다.
- **End-to-end rerun:** 고정 manifest/tape/model/analysis를 사용해 decision/order/fill/report의 경제적 hash를 비교한다. 실제 computed_at/recorded_at·시도 횟수는 별도 audit라 달라도 된다. 코드/라이브러리/부동소수점 허용오차를 명시하고 비용 원장은 고정 Decimal 반올림을 사용한다.

데이터 수정·정책 변경·새 모델·실행 순서 변경은 새 run/version이다. 기존 실패 구간을 사후 삭제해 수익률을 개선하지 않는다. 원본 DB 삭제 후에도 보존 bundle/tape로 replay 가능하도록 참조 artifact와 최초 가용 시각 증거를 함께 보관한다. 로컬 관리자 변조에 대한 서명·위변조 방지 저장소를 구현했다고 주장하지 않는다.

## 10. 평가 지표와 보고 한계

보고서는 반드시 모델 사건 점수 평가와 포트폴리오 성과를 분리한다. 비용·체결·기간·종목군·정보 모드·데이터 한계가 다른 run을 단일 순위로 합치지 않는다. 합성 fixture는 기능 검증에만 사용한다.

| 지표 | 정의 제안 및 계산 불가 조건 |
| --- | --- |
| 순자산/총수익률 | 모든 보유가 평가 가능하고 외부 현금흐름이 없는 경우 E_t=현금(예약 포함)+Σ(q×mark), R=E_end/E_start−1. E_start≤0 또는 unknown mark면 null+reason. 비용은 원장에 반영된 순자산으로 계산 |
| 기간 수익률 | 같은 평가 기준의 E_t/E_prev−1. 평가 간격·세션 목록을 명시. 현금흐름 지원 시 TWR 등 별도 승인 없이는 이 식을 그대로 사용하지 않음 |
| MDD | peak_t=max(E_0…E_t), drawdown_t=1−E_t/peak_t, MDD=max(drawdown). peak≤0/평가 결측이면 확정 수치 불가. 일별 평가는 장중 최대 손실을 알 수 없음 |
| 실현 손익 | 선택 원가 배분에 따른 매도대금−매도 비용/세금−배분 취득원가(매수 비용 포함). 부분 청산의 배분과 반올림 잔여를 명시 |
| 미실현 손익 | 보유 mark 금액−남은 원가. 추정 청산 비용 포함 여부를 별도 이름으로 표시하며 아직 발생하지 않은 비용을 실현 비용으로 중복 차감하지 않음 |
| 거래 횟수 | decision/admitted order/rejected/cancelled/partial/fill 건수와 완료된 position episode 수를 각각 보고. 하나의 주문에 여러 fill이 생겨도 episode 승률 분모를 부풀리지 않음 |
| 승률 | 완전히 종료된 position episode 중 순실현손익>0 비율. 0 손익은 별도 집계하고 분모 포함 여부를 사전 명시(포함안 제안). 미종료 포지션·원가 불명 제외 개수 공개, 완료 0이면 null |
| 비용 | 실제 가상 fee/tax 합계, fill benchmark 대비 방향성 slippage 금액과 거래금액 비율. slippage는 체결가 손익에 이미 포함되므로 순수익에서 다시 빼지 않음. 분모 0이면 비율 null |
| 노출/체결 품질 | 평가 가능한 시간의 gross/equity, 보유기간 분포, fill/ordered 수량 비율, 대기/거부/품질차단/모호 사건 수. 관측 빈도와 분모를 명시 |

연율화 수익률·Sharpe·회전율·벤치마크 상대성과는 초기 필수값이 아니다. 필요하면 연간 세션 수·무위험 기준·회전율 분모·벤치마크 배당 처리 등을 사전 승인한다. 거래일 수나 수수료·세율의 숫자 기본값을 발명하지 않는다. 현금 보유 또는 명시 벤치마크 비교도 동일 기간/비용/데이터 품질 범위로 별도 보고한다.

run 종료 시 미체결 주문·미종료 포지션은 그대로 보고한다. 종료 강제청산은 별도 정책/가상 사건이고 전략 SELL로 표기하지 않는다. 품질 불량 구간을 제외한 부분 지표는 coverage와 중단 사유를 함께 표시하고 전체 기간의 완전한 성과로 발표하지 않는다. 반복 정책 탐색·holdout 재사용·생존편향·사후 조정·유동성 가정·슬리피지 민감도를 공개하며 단일 수익률로 투자 적격성을 주장하지 않는다.

## 11. 승인 필요한 선택과 아직 없는 자료

다음 항목은 미정이며 숫자/기본값을 자동 생성하지 않는다. 핵심 프로파일 승인은 구현 착수 조건, 구체 수치는 각 run 시작 조건으로 나눈다.

| ID | 사용자 결정 또는 확보할 자료 | 권장 검토 방향(미확정) |
| --- | --- | --- |
| P1 | 첫 범위: 탐색적 historical replay / archived PIT / prospective paper, 인정할 성과 주장 | 우선 합성 기능 검증 및 제한을 명시한 historical 연구. 깨끗한 OOS 요구 시 Phase 2 확장 선행 |
| P2 | 결정 세션·체결 후보·latency·주문 종류/유효기간·동일 시각 사건 순서 | 열린 세션 판단 후 별도 미래 세션 체결 모델부터 검토. 같은 이벤트 체결 금지 |
| P3 | 시장 문맥/세션/상장·정지/기업행사 증거와 공개 시각, 품질 검증 책임 | 자료 없는 real 거래 가능 플래그를 자동 true로 만들지 않음 |
| P4 | 초기 현금/보유·취득원가, 매도 전량/일부·예산·한도, 다종목 순서 | Phase 3 정책을 그대로 명시, 순차 예약안 검토 |
| P5 | 거래비용·세금·slippage·rounding·부과 단위·예약 gap 상한/부족 동작 | 예상 비용과 fill 비용 분리, 숫자는 사용자가 제공 |
| P6 | 유동성 cap/자료·부분 체결·취소 경합·잔량 만료·지연 확인 | 일봉 전량 즉시 체결을 기본으로 두지 않음 |
| P7 | 즉시 가상 결제/지연 결제·매도대금 재사용·원가 방식·입출금 허용 | 초기 단순 결제/평균원가/무입출금안 검토, 실제 제도라고 주장하지 않음 |
| P8 | 평가 시각·mark TTL·결측/폐지 보유·종료 잔량·강제청산·승률 분모 | 불완전 상태 공개, 자동 마지막 종가 청산 금지 |
| P9 | 고정 모델 vs walk-forward, 사전 선택 cutoff·실험/holdout 격리 | 기존 research 동일 snapshot 제약 해소는 별도 Phase 2 승인 작업 |
| P10 | Simulation DB step 원자성·준비 artifact orphan 처리·재현 hash 범위 | 9절 제안 검토. 외부 artifact와 경제 원장의 분산 원자성 주장 금지 |

현재 KRX 키/승인과 실제 수집 품질, 과거 revision archive, 시간별 quote/유동성, 상폐·정지·기업행사·시점별 종목군의 완전성을 확인한 상태가 아니다. 모의거래를 설계했다는 이유로 이 입력이 있다고 간주하지 않는다. 추가 인증이나 데이터 확보는 구현 범위가 확정된 뒤 별도 요청한다.

## 12. 작은 구현 PR 순서 제안

아래는 향후 사용자가 승인·수행할 작업 순서이며 이번에 PR을 만들거나 코드를 추가하지 않는다. 제안 파일명은 아직 존재하지 않을 수 있으며 링크로 제공하지 않는다.

| 순서 | 목표/예상 모듈 | 주요 테스트 계획 | 선행 조건 |
| --- | --- | --- | --- |
| PR-A | Phase 4 불변 RunManifest/이벤트/정책 계약 및 사전 검증, `backtest/contracts.py`, `backtest/validation.py` | 필수 정책 누락, mode/origin 격리, timezone/sequence, 운영 불가, hash 안정성 | 이 설계 및 P1/P10 승인, hardening 통합 |
| PR-B | 고정 입력 tape/가용성 gate/캘린더·분석 ID 연계, `backtest/data.py`, `backtest/clock.py` | 미래 prefix 변조 불변성, PIT 실제 생성·수신 경계, 사라진 revision, 미래 test의 모델 선택 유입 거부 | P2/P3/P9, 실제 자료의 검증 범위 결정. 필요 시 별도 Phase 2 frozen inference 계약 PR 선행 |
| PR-C | 가상 원장·예약·checkpoint와 별도 `storage/simulation.py`, `backtest/ledger.py` | cash/예약/equity 불변식, 중복 키 충돌, 계좌 revision 경합, 부분 실패 rollback/restart, 원본 DB 분리 | P4/P7/P10, PR-A |
| PR-D | Phase 3 projection/읽기 전용 adapter 연계·admission, `backtest/decision_bridge.py` | BUY≠fill, HOLD/WAIT 주문 없음, 운영/합성 격리, pending·last_exit·HOLD diagnostics 보존, prepare orphan 재시도 | PR-B/C, 기존 Phase 3 계약 그대로 적용 가능함 확인 |
| PR-E | 승인된 하나의 fill/cancel/reject/partial 모델, `backtest/execution.py` | 접수 이전/동일 사건 체결 거부, gap·유동성 공유·0거래·부분 비용/잔량·취소 경합, 현금/주식 보존 | P2/P5/P6, PR-D. 기업행사 지원은 자료/별도 승인 없으면 차단만 |
| PR-F | 유한 backtest runner와 수동 paper step/resume, `backtest/runner.py` | 사건 순서, 결정→접수→체결 분리, 다종목 현금 중복 방지, 장애 후 같은 경제 결과, 원본 DB 변경/삭제 후 replay | PR-B~E, 모드별 실행 프로파일 승인 |
| PR-G | 평가·보고·최소 CLI, `backtest/metrics.py`, `backtest/report.py`, 후속 CLI 등록 | 손계산 비용/수익/MDD, 미종료·분모 0·mark 불명, 제한 표시, Phase 1~3 회귀, 독립 wheel에서 run/replay | P8, PR-F. 명령 이름/JSON 출력 계약은 이 단계 전 승인 |

각 PR은 해당 기능의 단위·회귀 테스트와 Ruff/mypy를 수행한다. 저장/엔진 변경은 전체 오프라인 회귀, 패키징/CLI 변경은 wheel 검증을 포함하는 안이다. 현재의 Phase 3 판단 조건·정책값과 Phase 1/2 공개 API를 바꾸는 변경은 위 PR에 몰래 포함하지 않는다. 실제 주문·실계좌·자동매매·HTTP/iOS·상시 scheduler·최적화·새 신호 모델은 제외한다.

## 13. 이번 문서 검증 범위

- 위 근거 코드/테스트와 설계의 시간·snapshot·Q2·null·운영 차단·DB 분리·hardening 계약을 대조했다. 재사용 불가/추가 승인 지점은 1·5·11절에 분리했다.
- 문서의 기존 파일 상대 링크와 경로, 코드 블록 짝, `git diff --check`를 로컬에서 확인한다.
- 문서만 변경하므로 pytest 전체 실행·wheel 빌드를 하지 않는다. 위 테스트는 미래 구현 계획이지 현재 실행 결과가 아니다.
- 문서만 로컬 커밋한다. push·PR 생성/수정/병합·Actions 조회/대기·main 변경은 하지 않는다.
