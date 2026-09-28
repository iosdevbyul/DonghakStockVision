# Phase 4 PR-C: 결정적 연구 원장

[전체 설계](phase4-design.md)의 원장 계산만 구현한다. [PR-A 계약](backtesting-contracts.md)과 [PR-B 시간 경계](backtesting-time-boundaries.md)는 변경하지 않는다. 순수 함수는 새 불변 상태를 반환하며 DB, 파일, 네트워크, 시스템 현재 시각, 난수를 사용하지 않는다. P10 영구 저장소 승인은 필요하지 않은 범위이며 저장소를 승인된 것으로 취급하지 않는다.

## 공개 계산 경계

[ledger.py](../src/donghak_stock_vision/backtest/ledger.py):

- `LedgerPolicy(FrozenJSON)`: 반드시 명시하는 원장 계산 정책.
- `LedgerCommand(FrozenJSON)`: caller가 제공하는 사건 ID·종류·주문 ID·sequence·발생/알려진 시각·원본 source ID·data. UTC 시각 정규화 및 canonical content hash.
- `initialize(manifest, tape_hash, policy, event, cutoff=...)`: manifest의 명시 초기 계좌로 초기화. 초기화 사건이 필수다.
- `apply(state, event, cutoff=...)`: 검증 뒤 새 `LedgerState`를 반환한다. 기존 상태는 바꾸지 않으며 실패하면 상태가 전진하지 않는다.
- `checkpoint(state)` / `restore(document)`: 보존용 불변 JSON 및 전체 사건 재실행 검증. 파일/DB 쓰기는 caller 책임이며 이 PR에 저장 API 구현은 없다.

PR-A `LedgerEvent`는 이미 계산된 증감과 원인 ID를 나타내며 예약 명령·주문/체결 payload와 caller 지정 충돌 키를 모두 담지 않는다. 이를 억지로 변경하지 않고 **입력 사건 LedgerCommand와 계산 결과 LedgerState**를 별도로 둔다. PR-A Checkpoint도 요약 계약이므로 전체 입력·사건을 담은 본 replay envelope와 혼동하지 않는다. PR-A `RunManifest`, `VirtualOrder`, `VirtualFill` 검증기는 그대로 사용한다.

## 명시 정책 및 초기 계좌

모든 키가 필수다. 지원 목록은 구현 가능한 계산 방식이며 사용자에게 추천 숫자를 자동 적용하는 기본값이 아니다.

| 필드 | 현재 지원하는 명시 선택 |
| --- | --- |
| version / currency | 비어 있지 않은 사용자 버전 / KRW |
| cost_method | average만 지원. FIFO·미지정 거부 |
| settlement | immediate_virtual만 지원. 실제 결제 제도라는 주장 없음. 지연 결제·미수·차입 거부 |
| costs | supplied_fill_buy_capitalize_sell_expense: 공급받은 fee/tax만 사용. 매수는 원가 포함, 매도는 순유입에서 차감 |
| cost_quantum | 사용자 지정 1 또는 1 이하의 양의 10진 자리 단위(1, 0.1 등). 숫자 기본값 없음 |
| cost_rounding | half_up 또는 down을 명시. 평균원가 일부 배분에만 적용 |
| reservation_shortfall | reject: 예약보다 큰 체결 비용을 자동으로 가용 현금에서 보충하지 않음 |
| expiry_boundary | exclusive: expires_at 이상인 신규 예약/체결 거부. 만료 해제는 해당 시각부터 가능 |

정책 hash는 주문/체결의 policy_id와 일치해야 한다. manifest의 기존 execution_policy_id를 다시 해석하거나 P1/P10 pending 상태를 해제하지 않는다. 이 원장 하위 정책은 checkpoint에 내용과 함께 고정한다. 향후 실행 정책 resolver가 승인된 상위 정책과 결합해야 한다.

초기 계좌의 available_cash_krw는 **초기 주식 취득 후 별도로 존재하는 현금**이다. 초기 position cost_basis_krw는 해당 주식 전체 취득원가이며 현금에 더하거나 다시 차감하지 않는다. 예를 들어 현금 1,000과 보유 원가 450은 현금 1,000 + 주식 원가 450이라는 별도 항목이다. 이것을 평가 순자산/투자 수익률로 해석하지 않는다. 사용자가 총 투자 예산만 주는 경우 주식 원가를 추정해 현금으로 변환하지 않는다.

초기 계좌는 PR-A의 KRW/정수/Decimal/완전성 검증을 받는다. 초기 예약은 0, sellable=quantity인 즉시 가상 결제 상태만 지원한다. 초기 잠금·미결제·차입·음수/소수 주식은 지원하지 않는다. 보유 0에 양수 원가는 거부한다. 가격/비용은 PR-A 숫자 범위와 유한 Decimal 규칙을 유지하고, 연산은 독립 80자리 Decimal context에서 수행한다.

## 사건과 예약

사건 공통 필드: `event_id`, `kind`, `order_id`(initialize만 null), `sequence`, `effective_at`, `known_at`, `source_id`, `data`.

| kind | data 및 효과 |
| --- | --- |
| initialize | 빈 data를 명시. 초기 계좌·0 실현손익·빈 주문/체결 이력 생성 |
| reserve_buy | 원본 open VirtualOrder + max_notional_krw + max_cost_krw 필수. 두 금액 합을 예약하고 가용 현금을 감소. 보유량/원가/손익 불변 |
| reserve_sell | 원본 open VirtualOrder 필수. 원래 주문 수량만큼 sellable에서 reserved로 옮김 |
| amend_cash | 매수 잔량의 새로운 max_notional_krw/max_cost_krw. 증액은 추가 가용 현금 검사, 감액은 현금 반환. 수량 변경 없음 |
| release | 명시 status=cancelled/expired/rejected. 미체결 잔량 전부와 잔여 예약만 해제. 일부 체결 후 rejected는 거부 |
| fill | 원본 PR-A VirtualFill을 그대로 제공. 발생/알려진 시각·sequence·source_event_id가 사건과 같아야 함 |

예약은 원래 주문 ID를 안정된 키로 사용한다. PR-A ID는 내용 hash이므로 current 주문 snapshot의 identifier는 상태 변화 때 달라진다. 원장은 `original`/`current`를 구분하며 VirtualFill.order_id는 **최초 original.identifier**만 참조한다. current에도 PR-A 수량 보존·상태 검증을 적용하되 그 ID를 새 주문으로 등록하지 않는다.

명시 order idempotency_key 중복과 계좌 revision 불일치를 거부한다. 새 예약 입력은 open·미체결·예약 0이어야 한다. 이미 외부에서 예약된 현금을 다시 예약하지 않는다. 매수는 보유 중 추가 매수 및 같은 ticker의 활성 주문이 있으면 거부한다. 복수 매도 예약은 남은 sellable 범위에서만 가능하다. 이는 Phase 3 admission을 대신하지 않으며 향후 bridge가 pending 주문 등 기존 판단 정책을 별도로 검사해야 한다.

unknown/cancel_pending 등 불명확한 상태를 성공·취소로 변환하지 않는다. 현재 이 원장에 새로 접수 가능한 상태는 명시 open뿐이고 fill/amend/release는 open/partial만 허용한다. 취소 요청 및 상태 미확정 사건 모델은 후속 승인 범위다. 이미 종료된 주문에 새 경제적 사건은 거부한다.

## 체결 반영과 불변식

체결 가격·수량을 생성하거나 시장 데이터와 비교해 체결을 결정하지 않는다. VirtualFill의 양수 정수 수량, 가격, notional, fee, tax가 명시돼야 하며 `notional=price×quantity`만 검사한다. slippage는 체결 가격에 이미 포함된 진단이므로 추가 현금 비용으로 다시 빼지 않는다. 비용율·세율·체결 가능성 검증은 여기서 수행하지 않는다.

- 매수: `notional+fee+tax`를 주문 예약에서 소비하고 보유 수량/매도 가능 수량/총원가를 증가한다. 부분 체결이면 남은 예약을 유지한다. 잔량이 있는데 예약이 0이면 거부한다. 마지막 체결이면 남은 초과 예약을 가용 현금으로 반환한다.
- 매도: 해당 주문에 예약된 주식을 차감하고 `notional-fee-tax`를 가용 현금에 반영한다. 음수 순유입은 거부한다. 평균원가 비례 배분액을 명시 단위/반올림으로 계산하고 마지막 전량 청산에는 남은 원가 전부를 배분한다. 실현손익은 순유입−배분 원가다.
- 가용/예약 현금≥0, total_cash=available+reserved, 전체 주문 예약 합=계좌 예약, sellable+reserved=held, 종목별 주문 주식 예약 합=종목 reserved를 검사한다.
- filled+remaining+cancelled=original quantity, 누적 체결 수량≤원래 주문 수량, terminal 잔량/예약 0을 유지한다.

effective_at≤known_at≤cutoff여야 한다. 새로운 경제적 사건은 마지막 sequence보다 커야 하며 known_at와 effective_at 모두 역행할 수 없다. 늦게 도착한 체결의 effective_at가 이미 적용한 경제적 사건보다 과거이면 과거 상태에 삽입하지 않고 오류를 반환한다. 지연 확인을 자동 재정렬하는 엔진은 없다.

## 멱등성·checkpoint

같은 event_id와 동일 정규화 내용은 기존 상태 그대로 반환한다. 같은 ID의 다른 내용은 충돌이다. 같은 VirtualFill ID 재전달은 다시 돈·주식을 변경하지 않는다. 새 event_id로 동일 체결을 전달한 경우도 경제적 상태/last sequence는 유지하되 충돌 검사용 receipt를 사건 이력에 보존한다. 이 경우 전체 이력 hash는 달라질 수 있고 동일 receipt의 재전달은 다시 추가하지 않는다. 이 중복 receipt는 과거 경제적 사건 삽입이 아니다. 같은 cost_charge_id를 다른 fill에 재사용하면 거부한다.

checkpoint는 manifest 본문/hash, tape hash, 원장 정책, 모든 사건/receipt, events hash, 마지막 경제적 사건/sequence를 포함한 상태 및 state hash를 보존한다. restore는 manifest 검증→초기화→전체 사건 재적용→저장 상태 및 hash 비교 순서다. 중간 정상 checkpoint부터 나머지 사건을 적용한 결과는 처음부터 같은 사건을 적용한 결과와 같다. 실제 실행·감사 now를 경제적 payload에 추가하지 않는다.

checksum은 우발적 변조 탐지이며 공격자가 모든 입력과 hash를 다시 쓰는 것을 방지하는 서명은 아니다. replay envelope만으로 원본 체결 사실, tape 내용 또는 정책 승인을 인증하지 않는다. tape hash의 실제 내용은 별도 보존·검증해야 한다. 작은 메모리 이력을 전체 보존하므로 대규모 스트리밍/압축 checkpoint는 미지원이다.

## P10 제안 — 아직 구현하지 않는 영구 저장소

권장안은 기존 시장/분석/Decision DB와 분리된 하나의 Simulation DB 안에 사건·receipt·주문 projection·원장 상태·checkpoint를 한 step transaction으로 기록하는 방식이다. 예상 공개 인터페이스는 다음과 같으며 코드로 구현하지 않는다.

```text
load_checkpoint(run_id) -> 보존 envelope 또는 missing
commit_step(run_id, expected_revision, expected_state_hash,
            step_id, exact_input_bundle, events, result_checkpoint) -> committed / exact_duplicate
load_events(run_id, after_sequence) -> 불변 사건/receipt
```

`commit_step`은 같은 step/event/fill 키의 정확 일치만 재시도 성공으로 인정하고 다른 내용은 전체 rollback한다. 기대 revision/state hash 비교와 unique 키 검사를 동일 transaction 안에서 수행해야 한다. commit 후 응답 전 중단도 같은 키 조회로 중복 경제 효과를 방지한다. 프로세스 간 경합/장애 내구성은 본 순수 함수의 기능이 아니다.

DecisionStore bundle은 먼저 준비하고 Simulation DB에 정확한 참조/사본을 기록하는 안을 권장한다. 실패 시 DecisionStore orphan artifact/실행 감사는 남을 수 있으나 경제적 사건으로 간주하지 않는다. 두 DB를 하나의 분산 ACID라고 표현하지 않으며 orphan 보존·재시도·정리 정책은 P10 승인 후 별도 작업이다. 본 PR은 DecisionStore를 호출하거나 SimulationStore·SQL을 작성하지 않는다.

## 다음 승인 사항

- **P4:** 초기 현금은 주식 취득 후 별도 현금으로 명시하고 초기 보유 총원가/매도 가능 수량을 별도로 제공하는 방식을 권장한다. 다종목 주문 순서·실제 예산·예약 상한 수치는 사용자 선택이다.
- **P7:** 첫 연구 시나리오는 명시 average + immediate_virtual + 무차입으로 좁히는 안을 권장한다. 현재 지원된 계산 선택지일 뿐 run에 자동 적용하지 않는다. FIFO/지연 결제/입출금/기업행사는 별도 계약·승인이 필요하다. 원가 배분 quantum/rounding 역시 필수 사용자 입력이다.
- **P10:** 위 단일 Simulation DB transaction + 준비 artifact 보존안을 검토·승인한 후 저장소 PR로 분리한다. 이번 구현은 영구 저장을 승인하지 않는다.
- 실제 주문 종류·부분 취소·불명 상태 확인·체결 가격/비용/지연/유동성은 후속 admission/fill 단계의 승인 사항이다. 기존 Phase 3 리스크 청산 정책과 운영 차단은 그대로 유지한다.

[원장 테스트](../tests/backtest/test_ledger.py)는 합성 회계 예시만 사용한다. 수익률/MDD·실전 성능·실제 주문에 관한 검증이 아니다.
