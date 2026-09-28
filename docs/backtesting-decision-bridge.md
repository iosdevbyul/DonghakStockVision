# Phase 4 PR-D: 판단과 가상 주문 후보 연결

## 범위와 기존 계약

`prepare_decision`은 Phase 3 공개 `assemble → decide → DecisionStore.save`를
호출한다. `DecisionBundle`은 입력·정책·결과와 판단 당시 원장 revision/hash를
묶는다. 별도 영구 저장소는 만들지 않는다. `admit`은 외부 I/O 없는 순수
함수이며 결과와 갱신된 불변 후보 이력을 반환한다.

Phase 3의 수량을 그대로 검증한다. 예산에 맞춰 수량을 몰래 축소하거나 판단을
다시 해석하지 않는다. operational_eligible/executable은 항상 false이다.
BUY/SELL이고 research/virtual인 판단만 검토하며 HOLD/WAIT/운영 요청은
후보를 만들지 않는다. 판단 결과는 기존 decide로 재계산해 일치를 확인한다.

기존 [판단 계약](decision-contract.md),
[시간 경계](backtesting-time-boundaries.md),
[원장](backtesting-ledger.md), [Phase 4 설계](phase4-design.md)를 변경하지 않는다.

## 명시적인 admission 정책

모든 키를 제공해야 한다. 거래 수치 기본값은 없다.

| 키 | 의미 |
| --- | --- |
| version | 사용자 정책 버전 |
| order_budget_krw | 주문별 기준 명목금액 및 매수 현금 상한 |
| max_order_quantity | 주문 정수 수량 상한 |
| max_position_quantity | 종목별 주문 후 보유 수량 상한 |
| max_position_notional_krw | 공개 기준 가격으로 평가한 종목별 주문 후 한도 |
| ordered_tickers | 동일 revision 후보 처리의 명시적인 종목 우선순위 |
| marks | 종목별 공개 사건 sequence와 가격 field |
| max_delay_seconds | 판단과 admission 사이 허용 시간 |
| expires_at | 명시적인 만료 시각 또는 null |

marks는 체결 가격이 아니다. Phase 3 입력의 기준 가격·공개 시각·수정 상태를
PR-B 공개 view와 대조한다. 모든 보유 종목의 공개 mark로 입력 계좌 평가금액을
대조한다. 매수 현금은 Phase 3의 명시적 비용 정책으로 계산된 estimated_cost를
사용한다. 새로운 수수료·세율·슬리피지·체결 가정을 추가하지 않는다.

## 시간·계좌·출처 검증

시장 및 분석 view는 admission 시각이 아니라 판단 clock에서 고정한다.
그 이후 공개된 필드는 사용할 수 없다. 원장 known_at, revision, 상태 hash,
checkpoint replay, 현금·주식·주문 snapshot을 검증한다. 기존 예약은 가용 현금과
매도 가능 수량에 반영된다. 미확정/활성 주문, 동일 종목 중복 후보와 추가 매수를
차단한다. 후보 이력은 동일 run/revision의 앞선 매수 후보 예산도 차감해 검사한다.
후보 생성 자체는 현금·주식·예약을 변경하지 않는다.

현재 공개 계약으로 입증할 수 없는 부분은 다음과 같이 차단한다.

- PIT: PR-B의 모델 선택·과거 revision 증거 부족을 그대로 차단한다.
- 실데이터: 시장 view의 not_execution_evidence는 거래 가능 증거가 아니다.
  실제 거래 가능 상태의 공개 증거 계약이 없으므로 실데이터 후보도 차단한다.
- 체결 이력이 있는 원장: 완전한 청산→분석 연결 이력의 공개 projection이 없다.
  이를 첫 진입으로 추정하지 않고 exit_history_projection_unavailable로 차단한다.

따라서 현재 admitted 경로는 합성 historical 연구 fixture에 한정된다.
synthetic_test_only는 후보와 결과에 유지되며 실전 성능 증거가 아니다.

## 결과, identity와 replay

반환값은 canonical JSON/hash를 갖는 FrozenJSON이다.
admitted는 가상 후보 생성, rejected는 검증된 조건의 거부,
blocked는 정책/증거/입력 부족을 의미한다.
주요 reason은 policy_missing, policy_incomplete_or_invalid,
account_revision_conflict, insufficient_cash, insufficient_quantity,
pending_order, data_not_public, pit_evidence_missing,
market_state_evidence_unavailable, decision_id_conflict이다.
Phase 3 diagnostics와 차단 사유도 보존한다.

후보는 기존 VirtualOrder를 사용하며 submitted, accepted_at=null, 예약=0이다.
예산·정책 hash·원장 revision·판단 및 admission 시각·공개 view hash는 결과
envelope에 보존한다. 동일 판단·원장·정책·clock·이력은 동일 ID와 hash를 만든다.
동일 decision ID의 다른 bundle은 충돌한다. 동일 성공 요청은 기존 결과를 반환한다.
이미 후보가 된 판단을 다른 정책으로 다시 admission하지 않는다.

호출자는 반환된 불변 이력을 다음 호출에 전달해야 한다. 이력은 단일 순서의
신뢰된 후보 계획이며 공유 저장소의 동시성 제어나 영구 중복 방지 장치가 아니다.
이력을 버리거나 변조한 외부 호출자에 대한 원자성은 보장하지 않는다.

## 다음 단계의 승인 및 공개 계약

PR-C 예약은 open 상태와 원장 정책 ID를 요구한다. 이 후보는 submitted 상태와
admission 정책 ID를 가지므로 그대로 예약할 수 없다. 다음 단계에 명시적인
후보 접수→정책 연결→예약 전이 계약이 필요하다. 이번에는 이를 자동 변환하지 않는다.

P2/P3/P5/P6/P9의 거래·가격·유동성·시간·모델 증거 정책은 확정하지 않는다.
실데이터 거래 가능 증거와 완전한 청산/재진입 projection이 선행되어야 한다.
P10 승인 전 SimulationStore, 원자적 접수와 예약, orphan 처리 및 영구
idempotency 구현은 제외한다. 체결, runner, 수익률, CLI도 구현하지 않는다.

## 검증

신규 테스트는 BUY/SELL, HOLD/WAIT/운영 차단, 정책 누락과 한도,
예약/계좌 binding, revision/decision 충돌, 시간 공개 및 PIT 차단,
합성 출처 전파, 계좌 불변성과 실제 Phase 3 저장/replay 연결을 검증한다.
기존 Phase 1~3 및 PR-A~PR-C 회귀 테스트는 그대로 유지한다.
