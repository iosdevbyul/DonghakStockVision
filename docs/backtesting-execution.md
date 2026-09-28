# Phase 4 PR-E: 결정적인 가상 전량 체결

## 범위와 lifecycle

[PR-D 후보](backtesting-decision-bridge.md) → 명시적 접수 →
[PR-C 원장](backtesting-ledger.md)의 예약 → 미래 공개 사건 → VirtualFill →
Ledger.apply 순서다. 순수 함수는 불변 결과와 새 LedgerState를 반환한다.
기존 객체는 변경하지 않으며 실패는 rejected + 기계적 reason과 원래 상태를 반환한다.
어떤 단계도 운영 적격이나 executable을 true로 만들지 않는다.

기존 VirtualOrder/VirtualFill, FrozenTape/MarketEvent/VirtualClock,
LedgerCommand/LedgerState/checkpoint를 재사용한다. 새 주문·체결 타입이나
DB, runner, CLI는 만들지 않는다. PR-A~D와 Phase 1~3는 변경하지 않는다.

## 공개 함수와 PR-F의 책임

`accept_candidate(admission, ledger, tape, decision_clock, candidate_clock, clock,
policy, reservation, ledger_sequence=...)`:

- admission은 신뢰된 PR-D admit의 불변 출력이다. admitted 연구 BUY/SELL만 접수한다.
- 후보 hash, 출처, run, 계좌 revision, 판단 공개 view hash와 시각을 검증한다.
- 기존 submitted 후보에서 open 주문을 만들고 accepted_at을 명시 clock으로 지정한다.
  policy_id는 PR-C가 요구하는 원장 정책 ID로 연결한다.
- 후보 ID와 접수 후 주문 ID는 다르며 반환 결과가 두 ID를 보존한다.
- 기존 Ledger.apply로만 현금/주식 예약을 생성한다. 체결이나 자산 증감은 없다.
- reservation의 max_notional_krw와 max_cost_krw는 필수다. BUY 합계는 PR-D의
  명시 예산 이하여야 한다. SELL은 두 값을 명시적으로 0으로 제공한다.
- 체결 정책 내용/hash, admission과 세 clock을 예약 사건 ID에 고정하고,
  반환 결과에 접수 주문과 예약 checkpoint를 보존한다.

`execute(acceptance, ledger, tape, event, clock, expected_revision=...,
ledger_sequence=..., quantity=...)`:

- 접수 checkpoint replay, 정책·clock binding, 주문 binding을 검증한다.
- expected_revision은 예약 완료 당시 revision이다. 첫 실행에서 현재 ledger는
  그 checkpoint와 정확히 같아야 한다. 중간에 다른 변경이 있으면 재검토가 필요하다.
- 지정된 하나의 미래 사건에서 전량 체결을 계산해 VirtualFill과 LedgerCommand를
  만들고 Ledger.apply에 전달한다. 잔액/주식/원가/예약/실현손익 계산은 원장의 책임이다.
- 반환 filled 결과는 fill 본문/hash, 안정적인 실행 키 fill_id, 원장 명령,
  정책 hash, 시장 view hash/sequence와 후보·주문 ID를 포함한다.

PR-F는 반환된 상태와 접수 결과를 보존하고 순서를 명시해야 한다.
ledger_sequence는 원장 명령 순서, clock.sequence는 시장 사건 순서다.
둘은 별개이며 자동 증가시키지 않는다. 재접수는 같은 원장 revision에서만 가능하고,
이미 예약한 상태에 후보를 다시 접수하면 revision 충돌로 거부한다.
체결의 정확한 재전달은 기존 성공 결과와 경제 상태를 유지한다.
순수 함수의 반환은 영구 commit이 아니며 프로세스 간 원자성/복구는 P10 후속이다.

## 시간과 공개 경계

시장 사건 sequence와 event_at(UTC)은 판단·후보 생성·접수 clock보다 모두
엄격히 커야 한다. timestamp가 같으면 sequence가 커도 체결하지 않는다.
실행 clock.sequence는 선택한 사건과 같고 event_at은 cutoff 이하여야 한다.
가격은 FrozenTape.view(clock)에 이미 공개된 지정 OHLC 필드만 사용한다.
이후 공개될 고가·저가·종가·거래량·라벨을 계산에 사용하지 않는다.
revision 정정 사건을 새로운 거래 기회로 해석하지 않는다.

fill_at = fill_known_at = 명시 실행 cutoff이다. 과거의 필드 공개 시각으로
체결을 소급 기록하지 않는다. expires_at 경계는 기존 원장이 검사한다.
감사용 현재 시각이나 난수는 identity에 들어가지 않는다.
source_event_id는 고정된 원본 MarketEvent의 전체 hash이며 lineage다.
계산에 사용하는 가격은 공개 view 값이다. 원본 hash를 공개 가격의 대용으로 쓰지 않는다.

## 필수 체결 정책

세부 설정은 FrozenJSON으로 받는 PR-A 상위 정책의 하위 설정이다.
상위 manifest의 pending 승인 상태를 변경하지 않는다. 새로운 숫자 기본값은 없다.

| 키 | 명시해야 하는 지원 값 |
| --- | --- |
| version / scope | 비어 있지 않은 버전 / research |
| fill_mode | full |
| liquidity | synthetic_explicit_full_fill |
| execution_price_field | open, high, low, close 중 하나 |
| slippage_rate | 0 이상 1 미만 Decimal 문자열 |
| buy_fee_rate / sell_fee_rate / sell_tax_rate | 각각 0 이상 1 미만 Decimal 문자열 |
| buy_tax | not_applicable을 명시 (BUY 세금 미지원) |
| price_quantum / price_rounding | 양의 10진 자리 단위 / adverse |
| cost_quantum / cost_rounding | 양의 10진 자리 단위 / half_up, down, up 중 하나 |

수치는 fixture 또는 사용자 정책에서만 제공한다. 비용 미제공은 0이 아니다.
float, NaN, Infinity, 음수 비용/비율, 잘못된 quantum을 거부한다.
기존 Decimal 정밀도/크기 제한을 적용하고 계산 context precision은 80으로 고정한다.

benchmark B, 수량 Q, slippage 비율 s일 때:

- BUY 가격 = B × (1+s)를 price_quantum으로 올림(ROUND_CEILING).
- SELL 가격 = B × (1−s)를 price_quantum으로 내림(ROUND_FLOOR).
- 반올림 후 양의 가격이어야 한다. 반올림도 투자자에게 불리한 방향이다.
- notional = 반올림된 가격 × Q. 추가 명목금액 반올림은 하지 않는다.
- fee = notional × 방향별 fee rate를 cost_quantum/방식으로 반올림한다.
- SELL tax도 notional × sell_tax_rate를 같은 비용 규칙으로 반올림한다.
- slippage_krw = |체결가−benchmark| × Q. 가격에 이미 반영되므로 다시 차감하지 않는다.

quantum은 1, 0.1, 0.01 등 10의 거듭제곱 자리 단위이며 거래소 호가 단위 모델이
아니다. half_up은 ROUND_HALF_UP, down은 ROUND_DOWN, up은 ROUND_CEILING이다.
유리한 반올림을 피하기 위해 가격은 adverse만 지원한다.

## 원장과 멱등성

실제 BUY 총 필요 금액이 예약보다 크면 reservation_shortfall이며 가용 현금을
몰래 보충하지 않는다. SELL은 예약 주식으로만 체결한다. 전량 체결 후 잔여 예약은
기존 Ledger가 해제한다. 알려진 시각 이전의 경제적 반영은 없다.

order ID 기반 단일 full-fill 실행 키를 원장 event_id와 cost_charge_id로 사용한다.
VirtualFill 자체의 identifier는 내용 hash다. 동일 실행 키/동일 명령 재전달은
동일 상태, 동일 키/다른 내용은 fill_identifier_conflict다. 기존 Ledger의
event/fill 중복 및 비용 충돌 검사도 유지한다. 정책 변경은 예약에 고정된 hash와
맞지 않아 거부한다. checksum은 외부 입력의 진위를 보증하는 전자서명이 아니다.

## 주요 거부 코드와 한계

- same_or_past_event / same_or_past_event_time / future_market_event
- execution_sequence_mismatch / price_not_public / unsupported_price_field
- partial_fill_unsupported / liquidity_unproven / market_quality_blocked
- reservation_shortfall / account_revision_mismatch / admission_budget_exceeded
- execution_policy_missing / missing_or_unknown_fields / nonfinite_decimal
- real_trading_forbidden / fill_identifier_conflict / acceptance_policy_binding_mismatch

실데이터·PIT를 지원하는 척하지 않는다. synthetic historical research만 지원하며
synthetic_explicit_full_fill은 합성 시나리오의 전량 체결 가정이지 유동성 증거가 아니다.
liquidity_evidence_id는 해당 명시 가정의 hash다. 실제 유동성을 입증하지 않는다.
합성 사건도 open session, listed, trading, unadjusted, verified quality를 요구한다.
실제 거래 가능성/기업행사/유동성 자료의 계약은 별도 확장이 필요하다.

부분 체결·주문장·거래량 cap·암묵적 유동성·부분 취소·체결 지연 추정·실제 호가
단위·실제 결제·broker routing은 미지원이다. PR-D의 체결 후 재진입 projection
제약도 그대로다. 전체 runner, 수익률/MDD, Simulation DB, 최적화와 실거래는 없다.

## 검증

[execution 테스트](../tests/backtest/test_execution.py)는 합성 PR-D admission을
실제로 만들고 접수→예약→미래 사건→BUY/SELL 체결을 검증한다.
방향별 슬리피지/비용/세금/반올림, 공개/시간 경계, 정책 누락·충돌, 예약 부족,
계좌 변경, 실패 시 불변, 중복과 replay hash를 검사한다.
합성 결과를 실제 투자 성능의 증거로 취급하지 않는다.
