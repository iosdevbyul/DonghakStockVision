# Phase 3 연구 판단 계약 — research_decision_v1

승인된 [설계 v2](phase3-design.md)를 구현한다. 점수는 조건부 장벽 도달 모델 점수이며 확률·수익·안전성을 보장하지 않는다. 운영 요청은 **항상 WAIT/blocked**, `operational_eligible=false`, `executable=false`다. 연구 결과도 실제 주문이나 계좌 변경을 만들지 않는다.

## 경계와 API

- `strategy.contracts`: frozen `DecisionInput`, `DecisionPolicy`, `DecisionResult`. JSON 중첩 객체를 canonical 문자열로 보관하며 `to_dict()`는 복사본이다. 종류와 내용을 hash한 identifier를 제공한다.
- `strategy.adapter.assemble(request, analysis_path, account, orders, market)`: Phase 2 SQLite를 `mode=ro`/`query_only`로 연다. 정확한 analysis_id의 checksum, 모델·snapshot·계약·수정주가 기준을 확인한다. 최신 신호로 대체하지 않는다. 분석 불가 사유는 입력에 보관한다.
- `strategy.engine.decide(inputs, policy)`: I/O·시계·난수 없는 순수 함수. 연구 입력과 명시 정책만 사용한다. Decimal context도 호출자의 설정에 의존하지 않는다.
- `storage.decision.DecisionStore`: 별도 DB에 원자적 `save`, `get`, `query`, `replay`. 같은 입력은 같은 결정 ID, 실행 감사 시각은 별도 누적한다. replay는 저장된 bundle만 사용한다.

계약은 JSON 객체다. malformed JSON/요청 식별 오류는 CLI 오류(2), 필수 판단 값 누락·부적격은 저장 가능한 WAIT/blocked(1)이다. 유효 연구 판단·조회는 0이다. 정책은 비어 있어도 객체로 구성할 수 있으나 decide에서 차단된다. JSON NaN/Infinity는 거부한다.

## 요청·분석

`request`: `scope`(research/operational), 6자리 `ticker`, timezone-aware `decision_as_of`, `analysis_id`.

분석은 Phase 2 원본을 보존한다: mode, data_origin, usage_restriction, model_version, snapshot_id, feature_version, label_version, event_description/장벽 파라미터, context, up/down_score 및 status, input_status, quality_flags, anchor_at, data_as_of, snapshot_as_of, model_created_at, latest_collected_at, last_trading_date. 어댑터는 모델·quality manifest hash와 실제 artifact_available_at도 저장한다.

Historical Research는 과거 anchor보다 늦은 모델/수집/snapshot을 허용하는 사후 연구다. 계좌·주문·가격은 anchor 시나리오의 가상 관측값으로 명시한다. PIT 연구는 Phase 2 A1 전일 이하 일봉 정책·수신/모델 시각 제한을 유지한다. 어느 쪽도 운영 판단으로 전환할 수 없다. synthetic_test_only는 보존한다.

## 불변 가상 snapshot

공통 필수: `origin="virtual"`, `snapshot_version`(비어 있지 않은 문자열), `observed_at`, `received_at`. 관측≤수신≤판단 시각, 각 TTL 및 max_skew 검사. 금액은 KRW Decimal 문자열, 수량은 비음수 정수다. bool을 숫자로 받지 않는다. 숫자는 최대 24 유효 숫자, 지수 절댓값 12, 수량은 SQLite signed 64-bit 범위라는 직렬화/자원 한계를 갖는다. 이는 매매 한도 기본값이 아니다.

| snapshot | 필드 |
| --- | --- |
| account | account_ref, complete=true, ticker, currency=KRW, quantity, sellable_quantity, average_price_krw(없으면 null), cash_basis=net_of_reservations, available_cash_krw, reserved_cash_krw, equity_krw, gross_exposure_krw, position_exposure_krw, positions_count |
| orders | account_ref, complete=true, items(완전한 배열), history_complete, last_exit(확정 이력 또는 명시 null) |
| market | ticker, currency=KRW, tradable=true, session=open, listing_status=listed, quality_status=verified, market(KOSPI/KOSDAQ/KONEX), adjustment, reference_price_krw, price_observed_at |

가상 평가 정합성: position_exposure=reference_price×quantity, gross≥position_exposure, equity=available_cash+reserved_cash+gross, 보유 종목 수와 노출 일치. 레버리지·부채·외화·다른 자산은 지원하지 않는다. 평균 매입가는 보존하지만 이번 disabled 손익 청산에는 쓰지 않는다. 가격은 가상 시장 snapshot의 명시 관측값으로서 실제 체결 가능성을 입증하지 않는다.

주문 items 각 행: ticker, state, remaining_quantity, reserved_cash_krw, received_at. filled/cancelled/rejected는 잔량·예약 0. open/partial/cancel_pending/submitted는 동일 종목이면 차단, unknown은 종목과 무관하게 차단. 다른 종목의 확정 활성 주문 예약금은 가용 현금에서 이미 차감되어 있어야 하고 합계가 account reserved_cash와 같아야 한다. 예약을 이중 차감하지 않는다. 빈 목록이라도 complete=false면 차단한다.

재진입 후보의 last_exit: status=completed, completed_at≤received_at≤decision_as_of, 이전 analysis_id. 동일 분석으로 재진입하지 않는다. SELL 판단은 가상 체결/청산 사건을 생성하지 않는다. verified_sessions cooldown은 market에 calendar_verified=true, 정렬·중복 없는 sessions(YYYY-MM-DD), 시작/끝을 덮는 calendar_coverage.start/end를 추가 요구한다. 검증된 세션 중 청산일 다음부터 판단일까지의 날짜를 센다. 미래 세션은 경과 수에 포함하지 않는다.

## 명시적 정책 (거래 수치 기본값 없음)

| 필드 | 계약 |
| --- | --- |
| schema_version / scope / version / currency | 1 / research / 사용자 불변 버전 / KRW |
| allowed_models | 정확한 모델 버전 배열 |
| allowed_quality_flags | 연구 한계로 명시 수용한 flags; input_status 불량을 해제하지 않음 |
| session_basis | Phase 2와 동일 verified_sessions 또는 observed_bars_unverified |
| effective_from / effective_until | 정책 유효 시각, 양 경계 포함 |
| thresholds.buy / thresholds.sell | 각각 명시 Decimal 문자열 [0,1], >= 비교 |
| ttl_seconds | account, orders, market, analysis, price, max_skew 각각 비음수 정수 |
| ttl_boundary | inclusive(경과=TTL 허용, 초과 차단) |
| sizing | buy_budget_krw>0, quantity_rounding=floor, sell_mode=all/partial, sell_basis=held/available |
| sizing (partial) | partial_kind=quantity/fraction, partial_value=양의 정수 또는 (0,1] 문자열 |
| limits | max_order_notional_krw>0, max_position_weight/max_gross_weight∈[0,1], max_positions≥0, exposure_rule=post_action_strict |
| costs | source/version, market, effective_from/until, quantum_krw>0, rounding=ceiling/half_up, buy/sell 각각 fee_rate/tax_rate/slippage_rate/fixed_fee_krw |
| reentry | unit=seconds/verified_sessions, duration≥0, boundary=inclusive, require_new_analysis=true, allow_first_entry 명시 bool |
| risk_exit | stop_loss/take_profit/max_holding_period 각각 mode=disabled, parameters=null |

부분 매도 비중은 명시 기준 수량에 곱하고 floor로 정수화한다. 이번 구현이 지원하는 정수화·경계·평가 규칙만 허용하며 다른 규칙 요청은 차단한다. 수치·매도 전량/일부를 자동 선택하지 않는다. 실제 세율/수수료는 제공하지 않는다. 0 비용도 사용자가 명시해야 한다.

Q2 매수는 비용 포함 예산 이하 최대 정수 수량을 구한다. 매수 예상가격=reference×(1+slippage), 매도=reference×(1-slippage). fee=notional×fee_rate+fixed_fee, tax=notional×tax_rate를 각각 quantum에 지정 반올림한다. 매수 필요현금=notional+fee+tax, 매도 유입=notional-fee-tax. 예산 후보가 현금·한도를 위반하면 차단하며 암묵적으로 축소하지 않는다. 매도는 sellable_quantity를 초과하면 차단하고 자동 부분 매도하지 않는다.

노출은 기준가격으로, 판단 후 equity는 비용·세금·slippage 차감으로 계산한다. post_action_strict 한도는 SELL/HOLD/WAIT에도 적용하므로 일부 축소만으로 한도 위반을 해소하지 못하면 WAIT/blocked다. 별도 리스크 청산을 몰래 활성화하지 않는다. 여러 종목의 독립 BUY 결과는 동일 현금을 공유할 수 있으므로 일괄 주문 계획이 아니다.

## 출력·차단 순서

scope → 정책 유효성/리스크 disabled → 분석 → snapshot/주문/거래 가능성 → Q2/비용/한도 순서로 검사하며 첫 차단 사유와 진행한 checks를 반환한다. 누락이 있는 단계 이후 값을 추정하지 않는다. 정상 null/context_mismatch만 비적용으로 인정하고, 적용 점수 누락이나 양방향 점수 충돌은 차단한다.

유효 flat+상승 조건이면 BUY, 유효 long+하락 조건이면 SELL; 그 외 flat은 WAIT/virtual, long은 HOLD/virtual. 모든 오류는 보유 여부와 관계없이 WAIT/blocked. 추가 매수·공매도 없음. HOLD는 안전성 주장이 아니다.

출력: schema_version/engine_version, action/status, decision_scope/ticker/decision_as_of, input_bundle_id/policy_id/policy_version/analysis_id/model_version/snapshot_id, mode/origin/restriction/quality_flags, decision_eligible, operational_eligible=false, executable=false, execution_blockers, position_state, proposed_quantity, estimated_cost, selected_rule, checks, evidence, reason_codes, blocking_reasons, diagnostics. 저장/CLI는 decision_id와 실제 UTC computed_at을 추가한다.

보유 prior_decline의 정상 HOLD: diagnostics.exit_signal_status=no_applicable_exit_signal, exit_model_status=context_mismatch, 세 risk_exit_rules=disabled/policy_disabled, diagnostic_codes=[exit_signal_absent_in_decline_context,risk_exit_rules_disabled], safety_assurance=false. 이 정보는 저장·조회·replay에서 유지한다.

## 저장과 조회

decision_bundles에 input_json/policy_json/result_json을 함께 INSERT하고 decision_executions에 감사 시각을 같은 트랜잭션으로 INSERT한다. update/delete trigger와 내용 hash는 우발적 변경을 검출한다. 로컬 파일 관리자에 대한 암호학적 인증·변조 방지 장치는 아니다. 다른 schema의 SQLite, 동일 경로·symlink·hardlink는 거부한다. Phase 2 DB에는 쓰지 않는다.

query(scope="operational", ticker=None, as_of=None, limit=100)는 cutoff 이하 판단 이력을 최신순 조회하며 연구로 fallback하지 않는다. replay(decision_id)는 저장된 입력·정책으로 재계산하고 hash/결과가 같아야 한다. 정책·엔진 변경 시 예전 엔진을 자동 선택하지 않으며 불일치는 명시 오류다. computed_at은 최초 보관 시각, 반복 실행 시각은 별도 감사 테이블이다.

완전한 합성 정책과 가상 snapshot 예시는 [tests/strategy/helpers.py](../tests/strategy/helpers.py)에 있다. 해당 수치는 테스트 fixture이며 실거래 기본값이나 추천 정책이 아니다.


### 저장 멱등성과 시간 조회 호환성

동일 decision_id 재저장은 기존 input_json/policy_json/result_json/scope/ticker/as_of 여섯 필드와 새 값이 **문자열까지 완전히 일치**해야 한다. 일치하면 최초 bundle/created_at을 보존하고 실행 감사만 추가한다. 하나라도 다르면 decision_bundle_conflict 오류로 트랜잭션 전체를 롤백하며 감사 이력을 추가하지 않는다. 같은 시각의 다른 offset 표기도 중복 저장에서 자동 동치화하지 않는다. save 응답의 computed_at은 해당 실행 감사 시각이고, get/replay의 computed_at은 최초 생성 시각이라는 기존 계약을 유지한다.

query는 scope/ticker로 선택한 행의 as_of와 cutoff를 timezone-aware UTC datetime으로 해석한 후 경계 포함 필터와 실제 시각 내림차순 정렬을 적용한다. 같은 시각은 decision_id 오름차순이며 limit은 정렬 후 적용한다. +09:00, Z, 음수 offset과 마이크로초를 문자열 순서나 부동소수점 변환 없이 비교한다. 기존 JSON·indexed text·identifier를 재작성하지 않고 스키마 마이그레이션도 하지 않으므로 checksum/get/replay 계약은 바뀌지 않는다. 로컬 연구 DB 범위의 단순 구현으로 해당 scope/ticker의 시간 메타데이터를 메모리에서 정렬한다.
