# Phase 4 PR-A: 불변 계약 및 구조 검증

[Phase 4 설계](phase4-design.md)의 PR-A만 구현한다. 설계 12절의 P1/P10 승인 선행 조건과 달리 이번 사용자 요청은 **두 항목을 미승인으로 유지한 계약 구현만** 허용했다. 이 문서는 그 제한과 실제 wire 계약을 기록한다. Phase 1~3의 구현·저장소·판단 정책은 변경하지 않는다.

## 범위 및 승인 경계

- `ExecutionPolicy`, `RunManifest`, `MarketEvent`, `SimulationAccount`, `VirtualOrder`, `VirtualFill`, `LedgerEvent`, `Checkpoint`는 불변 JSON 값 객체다. 생성자는 구조를 검증하며 외부 I/O, 현재 시각 읽기, 상태 전이, 체결·예약·손익 계산을 하지 않는다.
- 모든 계약은 `schema_version=1`, `scope=research`, `executable=false`, `operational_eligible=false`가 필수다. 주문/체결의 `virtual=true`, `broker_route=null`, 체결의 `simulation_only=true`도 필수다. 운영 scope 또는 활성화 플래그는 오류다.
- P1/P10 상태는 지금은 명시적 `pending`만 받는다. `approved`를 넣어 우회할 수 없다. 추후 사용자 선택은 별도 구현/계약 버전 변경으로 도입한다.
- 실행 정책은 버전과 **모든 하위 정책의 명시적 artifact ID/version/content hash**를 받는다. decision, availability, fill, cost, liquidity, reservation, settlement, valuation, ordering, end, storage 중 하나도 생략할 수 없다. 이 참조가 실제로 존재하거나 승인되었다고 주장하지 않는다. 구체 비용·세율·슬리피지·체결·저장 방식은 선택하거나 계산하지 않는다.
- `validate_run`은 구조·참조 ID·출처·기간·사건 순서를 검사한다. 통과해도 `ready_for_execution=false`이며 `p1_pending`, `p10_pending`, `policy_resolution_pending`, `execution_not_implemented`를 반환한다. 실제 artifact 해석·증거 검증은 PR-B 이후 책임이다. 빈 records 검사도 실행 가능성을 뜻하지 않는다.

## 공통 직렬화와 식별자

[contracts.py](../src/donghak_stock_vision/backtest/contracts.py)의 `from_dict` / `from_json`, `to_dict` / `to_json`을 사용한다. JSON 문자열만 내부 보관하고 반환 dict는 매번 새로 만든다. 중첩 자료를 수정해도 원본은 변하지 않는다. 필수 키 누락·알 수 없는 키·중복 JSON 키는 거부한다. nullable 필드도 키 자체는 생략하지 않는다.

가격·금액은 `Decimal` 또는 Decimal 문자열만 입력한다. float/int/bool을 금액으로 묵시 변환하지 않는다. 유한값만 허용하며 지수 없는 문자열로 출력하고 소수 끝의 0 및 음수 0을 정규화한다. 반올림하지 않고 외부 Decimal context에 의존하지 않는다. wire v1의 자원/표현 범위는 유효 숫자 최대 24자리, 입력 exponent 절댓값 최대 12다. 이는 거래 틱/수수료 반올림 정책이 아니다. 그러한 정책은 별도 명시 참조로 남는다. 수량·sequence·revision은 bool을 제외한 비음수 signed-64-bit 정수이며 주문/체결 원수량은 양수다. 시장 revision은 원천 문자열이다. 현금·가격·원가는 비음수, 체결 가격/거래금액은 양수다. 원장 증감 및 유리할 수도 있는 slippage는 부호 있는 Decimal을 허용한다. 원장 수량 증감은 각각 비음수 `quantity_increase` / `quantity_decrease`로 기록한다.

모든 순간은 timezone-aware여야 하고 UTC 고정 microsecond ISO 문자열로 정규화한다. `+09:00`, `Z`, 음수 offset의 같은 순간은 같은 hash다. 거래일은 별도 ISO date이며 `Asia/Seoul` 캘린더 의미를 갖는다. 날짜로 순간을 만들어내지 않는다.

JSON object key는 정렬하고 공백 없이 직렬화한다. 집합 의미의 참조 목록·보유 목록·flags·dependency IDs는 고유 키로 정렬하며 중복은 거부한다. 사건 배열은 순서를 유지하며 자동 정렬하지 않는다. `identifier`는 `{kind, hash_version: 1, payload}`의 SHA-256이다. `run_id`, `event_id`, `order_id`, `fill_id`는 해당 identifier로 계산하며 JSON에 자기 참조 ID를 넣지 않는다. record는 manifest의 ID를 `run_id`로 참조한다.

실제 감사 시각 `recorded_at`은 호출자가 반드시 제공하되 semantic identifier에서는 제외한다. `content_hash`는 이를 포함한 전체 payload를 보호한다. 다른 감사 시각의 경제적 identity는 같을 수 있지만 원본 JSON/content hash는 다르다. 시스템 현재 시각을 자동으로 넣지 않는다. 그 외 시각·정책·데이터·순서 변경은 identity를 바꾼다. 기존 Phase 3의 ID 계산은 바꾸지 않는다.

## 계약별 필드와 검증

정확한 필수 키는 [validation.py](../src/donghak_stock_vision/backtest/validation.py)의 `BODY_FIELDS`, `ACCOUNT_FIELDS`, `POLICY_REFS`에 정의한다.

| 계약 | 내용과 불변식 |
| --- | --- |
| RunManifest | 실행 방식 backtest/paper, 정보 모드 historical_research/point_in_time, 출처 real/synthetic, 제한 상태를 독립 입력. 기간, 완전한 초기 가상 계좌, 실행 정책 ID, 시장 snapshots·models·analyses·품질·캘린더·종목군의 정확한 참조, 코드/환경 hash와 feature/label/engine 버전, 명시적 UTC/Asia/Seoul·경계·순서 정책, 가용성 가정 ID, null 또는 명시 seed. 초기 계좌 수신 시각≤시작, 시작≤종료 |
| MarketEvent | run ID, sequence, ticker, 거래일, 사건/실제 수신/공개/품질 공개 시각, 원천 revision와 참조, 공개 필드, 품질 상태/증거 IDs/flags, 시장·세션·상장·정지·가격 수정 상태. 사건≤수신 및 공개, 품질 공개≤공개; PIT는 수신≤공개도 필수. 검증 완료 표시에는 증거 ID가 필요하지만 내용 진실성까지 입증하지는 않음 |
| SimulationAccount | account ID/revision, observed/received 시각, completeness, virtual/KRW, 가용/예약 현금, ticker별 수량·매도 가능·예약 수량·원가, valuation hash. observed≤received, 매도 가능+예약≤보유, ticker 중복 금지. 초기 계좌 외 일반 account는 불완전 상태 자체를 기록 가능 |
| VirtualOrder | decision/policy ID, idempotency key, 계좌 revision, ticker/side, 원수량·활성 잔량·누적 체결·취소/종료 수량, 상태, 제출/접수/만료/취소 시각, 현금/주식 예약. 원수량=잔량+체결+취소/종료. terminal은 활성 잔량·예약 0. partial은 일부 체결과 양수 잔량. 상태에 맞는 접수/취소/만료 시각 요구 |
| VirtualFill | order ID, sequence, ticker/양수 수량·가격·notional, fee/tax/benchmark/slippage, 발생/확인 시각, 원천 사건·유동성 증거·정책·비용 charge ID, virtual/simulation_only. 발생≤확인. notional 곱셈·비용 계산이나 주문 상태 갱신은 수행하지 않음 |
| LedgerEvent | sequence, 이전 state hash, 원인 종류/ID, 현금·예약·보유 수량·원가 증감, effective/available/recorded 시각, nullable 비용 charge ID. effective≤available, ticker 중복 금지. 실제 계좌 적용·중복 비용 계상 검사는 후속 원장 단계 |
| Checkpoint | 마지막 sequence/state hash, manifest hash(=run ID), 상태, 기준/감사 시각, 의존 IDs·누락 사유. completed에 누락 사유 허용하지 않음. 저장/commit/resume는 구현하지 않음 |

실행 정책 외 모든 record와 manifest의 데이터 참조는 같은 정보 모드·출처·제한 상태를 갖는다. synthetic은 언제나 `synthetic_test_only`, real historical은 `research_only`, real PIT는 `pit_review_required`다. 마지막 상태도 운영 적격을 뜻하지 않는다. synthetic/real 혼합, 다른 run의 record, 다른 정책 ID는 오류다. 실행 방식은 어느 정보 모드/출처와도 독립적으로 표현할 수 있으며 기본 선택은 없다.

historical의 실제 수신 시각은 가상 공개 시각보다 늦을 수 있지만 `availability_assumption_id`를 반드시 명시한다. PIT는 이 가정 ID가 null이고 실제 수신 뒤에만 공개할 수 있다. 품질 공개 시각도 run-visible 시각이며 historical에서 가정한 시각이면 동일 가정 artifact에 그 근거를 남겨야 한다. 실제 원천 시각을 변경하는 코드는 없다.

`validate_event_sequence`는 전달된 사건 순서 그대로 같은 run/provenance, 엄격 증가 sequence, 비감소 UTC available_at, cutoff 이하(포함)를 검사한다. sequence 간격은 허용하고 동일 시각은 sequence로 구별한다. `validate_run`은 명시된 inclusive 또는 exclusive 양 끝 기간과 정책 결합을 추가 검사하며 market 사건의 sequence 검사도 호출한다.

## 후속 단계와 남은 제약

이 계약만으로 PIT/시장 품질 또는 실제 모델 생성·선택·라벨 성숙 시각을 검증했다고 주장하지 않는다. 원천 content hash 조회/검증, 개별 공개 필드의 당시 가용성, 모델/분석 가용성, 기업행사·종목군의 진실성은 PR-B의 해석기와 고정 입력 검증이 필요하다. A1 전일 이하 일봉 및 Phase 3 판단 계약은 그대로 유지한다. 원본 시장/분석/모델/Decision DB는 읽거나 변경하지 않는다.

초기 계좌 valuation hash의 실제 mark/원가 방식 검증, 별도 Position/Valuation 및 DecisionLink/Report 상세 계약, 원장 replay/주문 인과 관계·비용/수량 적용·원자성은 후속 PR-C~G에서 승인 정책에 따라 구현한다. 해시 참조 자체는 데이터 보존/서명/진실성 보장이 아니다. P1~P10의 거래·저장 정책 및 수치에 대한 승인을 이 PR-A가 대신하지 않는다.

검증 테스트는 [계약 테스트](../tests/backtest/test_contracts.py), [사전 검증 테스트](../tests/backtest/test_validation.py)에서 합성 fixture만 사용한다. fixture 금액·시각·정책 참조는 기능 테스트 입력이며 투자 성능이나 권장 설정이 아니다.
