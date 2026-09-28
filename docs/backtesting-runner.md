# Phase 4 PR-F: 결정적인 historical runner

## 책임과 기존 계약

`BacktestRunner`는 고정 기간의 공개 시장 사건을 순서대로 처리한다.
계좌 projection과 orchestration만 담당하고 전략·수량·체결가·비용·성과를
계산하는 새 정책은 추가하지 않는다. 경제적 변경은 기존 Ledger 경계를 통해서만 한다.

- [PR-A 계약](backtesting-contracts.md): manifest, 초기 계좌, 시각·출처·타입 검증.
- [PR-B](backtesting-time-boundaries.md): FrozenTape, FrozenAnalysis.view, VirtualClock.
- [PR-C](backtesting-ledger.md): 초기 상태, 예약·체결 적용, checkpoint/replay.
- [PR-D](backtesting-decision-bridge.md): 공개 DecisionInput과 DecisionBundle.calculate
  (기존 decide), admit. 수량 변경이나 BUY/SELL 생성 로직은 runner에 없다.
- [PR-E](backtesting-execution.md): accept_candidate → 예약 → 미래 execute → Ledger.

기존 코드는 변경하지 않는다. FrozenAnalysis를 사용하므로 DB 기반 assemble이나
DecisionStore 쓰기를 호출하지 않는다. Phase 3의 공개 DecisionInput/decide를
사용하고 판단 입력/결과를 불변 run 결과에 보존한다. 모델 학습이나 private
inference API도 호출하지 않는다.

## 명시 입력과 기간

생성자 입력은 tape, analyses(tuple), 초기 initialize된 LedgerState,
decision_policy, execution_policy, config(FrozenJSON)이다.
초기 현금·보유·원가·원장 정책은 manifest와 초기 Ledger에 명시되어 있어야 한다.
체결 정책은 PR-E의 모든 비용·슬리피지·세금·반올림 필드를 요구한다.

config의 모든 키는 필수다.

| 필드 | 계약 |
| --- | --- |
| run_id | FrozenTape manifest의 identifier와 일치 |
| start_at / end_at | timezone-aware 시각. UTC 비교, start≤end, manifest 범위 안 |
| tickers | 비어 있지 않은 명시적 종목 목록. 이 순서가 동일 step의 판단 우선순위 |
| event_clock | 명시적으로 available_at 선택 |
| decision_steps | 명시적 판단 기회 목록. 빈 목록은 관찰만 하는 run |

decision_steps 각각은 sequence, ticker, analysis_id, market,
admission_policy, reservation을 제공한다. market은 Phase 3의 명시적 가상 시장
snapshot이다. 종목 선택·판단 빈도·artifact 선택·거래 가능 플래그를 추정하지 않는다.
admission_policy의 ordered_tickers는 config 순서와 같아야 한다.
동일 sequence/ticker 중복은 거부하고 목록은 sequence와 종목 우선순위로 정규화한다.

start/end는 **사건 공개 시각 available_at의 양끝 포함 구간**이다.
해당 구간 밖 사건은 처리하지 않는다. 초기 상태의 known_at은 start 이하여야 한다.
이전 공개 가격은 명시 mark와 기존 TTL 정책이 허용하는 경우 현재 view에 존재할 수
있지만 이전 사건을 다시 실행하거나 주문을 소급하지 않는다.

매일 +1 day로 날짜를 생성하지 않는다. tape에 없는 날은 없다.
각 market event를 available_at과 sequence로 한 번 처리한다.
동일 timestamp는 sequence 순서를 따른다. clock이 역행하는 tape는 거부한다.
한 사건의 필드나 분석이 나중에 공개되더라도 가상의 새 사건을 만들지 않는다.
다음 tape 사건/명시 decision_step이 없으면 그 분석으로 판단하지 않는다.

## 한 event의 처리 순서

1. PR-B clock/view 생성.
2. 이전에 접수된 주문 중 해당 ticker의 미래 사건 체결을 PR-E로 시도.
3. 명시 decision_steps를 tickers 우선순위로 정렬.
4. FrozenAnalysis.view(clock)로 공개 여부 검사. 차단이면 점수를 판단에 넘기지 않음.
5. 현재 원장의 현금·예약·주식·주문을 읽어 Phase 3 가상 snapshot으로 projection.
6. 명시 marks의 공개 가격으로 판단에 필요한 gross/equity를 구성.
   이는 성과 계산이 아니다. 시장 기준가격과 공개 시각도 대조.
7. 기존 Phase 3 decide → PR-D admit → PR-E accept_candidate/예약.
8. 모든 staging이 정상 반환된 경우에만 이번 event의 불변 상태를 채택.

새 후보는 2번 체결 단계 이후에 만들어지므로 동일 사건에서 체결하지 않는다.
PR-E의 sequence와 UTC timestamp의 엄격한 미래 조건도 유지한다.
HOLD/WAIT와 blocked 판단은 후보/예약을 만들지 않는다.
기존 주문을 덮어쓰거나 부족한 예약을 자동 증액하지 않는다.
거부된 execution은 거부 결과와 기존 예약 상태로 남는다.

## 현재 연결 가능한 범위와 차단

**synthetic + historical_research + backtest만 지원한다.**
실데이터/PIT/paper 요청은 실행 전에 거부한다.
합성 실행은 실제 예측 성능·유동성·시점별 데이터 증거가 아니다.

PR-E는 예약 당시 전체 ledger revision/checkpoint를 고정한다.
따라서 기존 예약이 있으면 다른 후보의 동시 예약을
concurrent_reservation_unsupported로 차단한다. 단일 pending 주문을 명시적으로
순차 처리하며 계약을 완화하지 않는다. 다종목 판단 순서는 결정적이지만 동시
다종목 예약 실행은 후속 revision 계약 확장이 필요하다.

PR-D는 체결 후 완전한 청산/분석 lineage projection이 없어, fills가 있는 원장의
새 후보를 exit_history_projection_unavailable로 차단한다.
runner도 이를 우회하지 않는다. snapshot history_complete를 허위 true로 만들지 않는다.
Phase 3 SELL이 생성되더라도 PR-D에서 막힐 수 있다.
현재 현금→BUY→미래 fill과 초기 보유→SELL→미래 fill은 각각 실행 가능하지만
하나의 run에서 BUY→SELL 왕복을 완성했다고 주장하지 않는다.
반복 매매 runner의 완성에는 기존 PR-D의 후속 공개 계약 확장이 선행되어야 한다.

## 불변 결과와 PR-G 경계

`run() -> FrozenJSON`의 identifier가 deterministic run hash다.
결과에는 다음이 포함된다.

- run_id, start_at/end_at, 전체 입력 hash, 처리 사건 수
- 사건별 cutoff/sequence/공개 view hash와 processed/failed 상태
- decision ID, 실제 입력/결과와 정책 ID
- 후보 결과, 접수 주문·예약 checkpoint, 체결 본문/hash·원장 명령
- 거부/차단 reason, 아직 pending인 주문
- 최종 account, 전체 ledger checkpoint와 그 hash

PR-G는 이 원시 기록을 사용한다. total return/CAGR/MDD/승률/Sharpe/순위/차트는 없다.
종료 시 보유 주식과 pending 주문/예약은 그대로 남긴다. 강제 청산·자동 취소·
암묵적 최종 평가 가격을 만들지 않는다. PR-G에는 별도 valuation policy가 필요하다.

## 재현성과 실패

동일 입력은 동일 결과/hash다. 시간·난수·네트워크·DB·모델 재학습에 의존하지 않는다.
고정 tape/artifact와 정책·config를 보존해 run을 다시 호출할 수 있다.
결과 checkpoint는 기존 restore로 경제 이력을 재생할 수 있다.
이는 runner 중간 재개나 영구 장애 복구 API는 아니다.

입력 hash에는 전체 고정 tape와 artifact도 포함하므로 미래 입력 변경 시 전체
run hash는 달라진다. 그렇다고 과거 판단에서 미래 필드를 읽는 것은 아니다.
테스트는 미래 비공개 가격/라벨 변경이 과거 DecisionResult를 바꾸지 않는지 확인한다.

설정 검증 실패는 처리 시작 전 예외로 거부한다.
각 사건은 local staging을 사용한다. 예외가 나면 그 사건의 후보·예약·체결·이력
변경 전체를 버리고 직전 상태의 checkpoint와 failed reason을 남긴 뒤 다음
사건으로 진행한다. 정상 reject/blocked는 실패 거래가 아니라 명시 결과다.
failed 사건이 있으면 run 상태는 completed_with_failures이다.
예상하지 못한 부분 실행을 성공한 경제 상태로 반환하지 않는다.

이 원자성은 메모리 내 불변 객체 반환 경계다. P10 Simulation DB transaction,
프로세스 간 동시성, durable resume, DecisionStore orphan 처리는 구현하지 않는다.

## 검증

[runner 테스트](../tests/backtest/test_runner.py)는 짧은 합성 BUY/SELL 기간,
HOLD/WAIT, 공개 경계, 다종목/동일 timestamp 순서, pending 중복 차단,
체결 거부, 예약·체결 직후 주입한 예외의 전체 event rollback,
종료 시 보유 유지, replay/hash, 미지원 모드 및 누락 정책을 검증한다.
합성 BUY 예시는 현금 10,000 → 10주 매수 → 현금 9,089.18이며,
별도 SELL 예시는 현금 9,500 + 초기 5주 → 현금 10,040.69 + 0주이다.
이 숫자는 테스트 fixture이며 운영 기본값이나 투자 성과가 아니다.
