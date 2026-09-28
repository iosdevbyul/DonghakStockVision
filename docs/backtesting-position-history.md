# Phase 4 PR-F.1: 검증된 포지션·청산 이력

## 기존 차단과 선택적인 연결

PR-D는 fills가 있는 계좌에 대해 분석/청산 lineage를 확인할 수 없어서
exit_history_projection_unavailable로 차단했다. 단순히 이 조건을 제거하지 않는다.
[Runner](backtesting-runner.md)의 config에
`history_mode: "verified_execution"`을 명시하면 실제 판단·분석·접수·체결 receipt를
수집하고 다음 판단 시점에 검증된 projection을 전달한다.
이는 증거 수집 경로 선택이지 승인 플래그가 아니다. 증거 검증을 생략하지 않는다.
키가 없는 기존 run과 position_history를 제공하지 않는 PR-D 호출은 기존 차단을 유지한다.

새 주문/체결/계좌 타입은 만들지 않는다. FrozenJSON, VirtualOrder/VirtualFill,
Ledger/checkpoint 및 Phase 3 DecisionInput/Policy/Result를 재사용한다.
Phase 3의 정책·임계값·cooldown·새 분석 요구·no pyramiding은 변경하지 않는다.

## 공개 API와 검증

`build_position_history(ledger, tape, evidence, cutoff=...)`는 완전한 검증 결과인
FrozenJSON을 반환하거나 명시적인 검증 오류를 발생시킨다.
`validate_position_history(history, ledger, tape, cutoff=...)`는 현재 계좌/run/revision을
대조하고 evidence에서 다시 생성한 결과와 정확히 일치하는지 검사한다.

각 evidence packet은 다음을 보존한다.

- decision_input / decision_policy / decision_result와 판단 당시 ledger hash
- frozen analysis bundle
- PR-E acceptance: 후보, 접수된 주문, 예약 checkpoint, clock, 실행 정책
- PR-E execution: 확정 VirtualFill, 원장 명령, 공개 시장 사건 및 정책 참조

검증은 다음 순서다.

1. Ledger 전체 checkpoint를 restore하고 초기화부터 사건을 순서대로 apply한다.
2. 실제 ledger fill 각각에 대응하는 evidence가 정확히 있어야 한다.
3. 기존 decide로 판단을 재계산하고 decision→candidate→open order의
   ID/ticker/방향/수량을 대조한다.
4. 판단 당시 ledger prefix와 cash/quantity/revision, 이전에 검증된 last_exit를 대조한다.
5. FrozenAnalysis의 실제 공개 gate와 판단의 분석 본문/ID를 대조한다.
6. 기존 PR-E execute를 같은 접수·시장 사건·clock·수량으로 재실행한다.
   결과와 Ledger 상태가 실제 이력과 정확히 같아야 한다.

현재 시각, 네트워크, 모델 재학습, 영구 DB에 의존하지 않는다.
receipt가 누락됐거나 다른 계좌/run/ticker, 잘못된 fill/decision 참조이면
완전한 이력으로 인증하지 않는다. hash는 checksum이며 외부 데이터의 전자서명은 아니다.

## acquisition → exit lifecycle

초기 보유분은 명시적인 manifest.initial_account를 acquisition 근거로 기록하고
decision/fill ID를 발명하지 않는다. 이후 매수분은 검증된 BUY decision/order/fill을
acquisition으로 기록한다. 각 episode는 다음을 포함한다.

- 결정적인 position_id, ticker
- 초기 계좌 또는 acquisition decision/analysis/candidate/order/reservation/fill 참조
- SELL 참조 목록
- 현재 수량과 still_open / partial_exit / confirmed_full_exit 상태

SELL 주문이 존재하거나 quantity가 0이라는 이유로 청산을 추정하지 않는다.
실제 확정 SELL fill과 acquisition을 연결하고 Ledger replay 결과의 보유 수량,
예약 수량, 매도 가능 수량이 모두 0일 때만 confirmed_full_exit다.

기존 Phase 3 정책이 보유분 중 일부를 전량 체결 주문으로 매도한 경우
남은 보유분은 partial_exit다. 이는 partial fill 구현이 아니다.
부분 체결 자체는 거부하며, 불완전/unknown 이력은 오류로 차단한다.
잘못된 이력을 confirmed exit로 승격하는 fallback은 없다.

## 재진입

ticker별 latest confirmed exit는 Phase 3가 요구하는
status=completed, completed_at, received_at, analysis_id를 제공한다.
run/account/position/exit decision/exit fill 참조도 함께 보존한다.
시각은 실제 확정 fill의 fill_at/fill_known_at에서 가져온다.
새 BUY 판단에서 기존 Phase 3가 cooldown, 새 analysis ID, first-entry 설정 등을
그대로 검증한다. 보유 중 BUY는 기존 Phase 3의 HOLD/WAIT 의미를 유지한다.

PR-D는 현재 ledger hash/revision에 연결된 history를 다시 검증하고
입력 orders.history_complete 및 ticker별 last_exit와 정확히 대조한다.
다른 ticker의 청산 기록, 과거 revision, 누락/충돌 기록은 후보를 만들지 않는다.
SELL 미체결은 pending order로 남으며 confirmed exit가 아니다.

## Runner와 원자성

Runner는 성공한 접수 시 판단과 접수 receipt를 보존한다.
PR-E가 filled를 반환한 경우에만 execution을 붙이고, event 종료 전 history를 검증한다.
다음 decision cycle의 account는 여전히 Ledger projection이며 직접 수정하지 않는다.
실패한 event의 ledger·pending·후보 이력과 trade receipt는 함께 버려진다.
검증 실패 후 경제 상태만 성공한 것처럼 남기는 경로는 없다.

결과에는 position_history와 position_history_hash가 추가된다.
history에는 run/account/revision/ledger hash/known_at, episodes, last_exits,
모든 검증 evidence가 포함된다. 종료 시 마지막 포지션은 강제 청산하지 않는다.

## 멱등성과 replay

동일 execution ID/동일 evidence의 재전달은 한 번만 반영한다.
동일 ID/다른 내용은 history_id_conflict다.
중복 Ledger fill receipt도 새로운 episode나 청산을 만들지 않는다.
동일 입력에서 evidence 정렬, position ID, exit projection, 판단/체결/계좌 및
전체 run hash가 결정적이다. 출력 history를 수정하면 재생 결과 비교에서 거부한다.

## 범위와 검증

synthetic historical research의 기존 full-fill 경로만 지원한다.
Phase 3 정책, Phase 2 모델, 수익률·MDD, partial fill, 실제 PIT/유동성,
Simulation DB, 실거래 및 UI는 변경하거나 구현하지 않는다.
기존 동시 다종목 예약 제한도 그대로다. 작은 메모리 이력을 전체 재생하므로
대규모 실행 최적화나 durable resume를 제공하지 않는다.

[테스트](../tests/backtest/test_position_history.py)는 기존 무증거 차단 테스트와 별도로
실제 검증 자료가 있는 BUY→SELL→BUY 및 BUY→SELL→BUY→SELL을 실행한다.
합성 fixture에서 첫 흐름의 종료 상태는 현금 9,259.73원/10주,
두 왕복 종료 상태는 10,341.10원/0주다.
이는 회계·연결 검증 예시이며 투자 성과나 거래 기본값이 아니다.
