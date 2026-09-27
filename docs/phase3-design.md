# Phase 3 수정 설계 v2 — 연구용 가상 판단 엔진

상태: **사용자 최종 승인 완료, 구현 진행**. 원본 작성 2026-09-24.
2026-09-27 임시 worktree 소실 후 사용자 복구 승인에 따라 대화의 v2 계약을 재구성했다. 원본 파일의 바이트 단위 복구는 아니며 승인한 범위·정책을 유지한다. 구현용 JSON 필드와 실행 예시는 별도 [decision-contract.md](decision-contract.md)에 명시한다.

## 기준과 변경 경계

Phase 2 PR #2의 병합 커밋 `651ff859739d9db4c5e0e91c0093c25a64b4d701` 기준이다. `feat/strategy-decision-engine`, `/private/tmp/dsv-phase3`를 사용한다. 사용자 원본 CI 파일과 다른 worktree는 변경하지 않는다. main 직접 커밋·push·병합은 하지 않는다.

근거: [Phase 2 설계](phase2-design.md), [데이터 계약](data-contract.md), [signals/service.py](../src/donghak_stock_vision/signals/service.py), [data/schema.py](../src/donghak_stock_vision/data/schema.py), [storage/analysis.py](../src/donghak_stock_vision/storage/analysis.py).

## 1. Phase 2 소비 계약

- 점수는 `reversal_barrier_v1`: 직전 반대 추세 문맥에서 5개 미래 세션 내 단기 역방향 장벽 최초 도달이다. H=5, 최소 장벽 2%, 변동성 배수 2는 라벨 정의이며 보유 기간·수익·지속 반전·검증된 상승 확률이 아니다.
- 상승 모델은 r5<0인 prior_decline, 하락 모델은 r5>0인 prior_rise에서만 적용한다. 적용 방향은 scored/점수, 상대 방향은 null/context_mismatch. flat은 양쪽 null이다. null을 0으로 바꾸거나 두 점수를 합산·차감하지 않는다.
- 분석용 signal_state는 0.5로 필터링돼 있으므로 운영 신호 목록만으로 전략을 판단하지 않는다. 정확한 analysis_id와 모델/snapshot/version을 고정해 분석 원본을 읽는다. 0.5를 매매 기준으로 재사용하지 않는다.
- Historical Research는 가상 서울 16:00 anchor와 실제 snapshot cutoff를 구분한다. 사후 수정·기업행사·생존편향이 남는 retrospective 분석이다. PIT는 실제 수신 시각과 모델 가용 시각을 요구한다. synthetic_test_only는 영구 합성 제한이다.
- validation_qualified/AP 우위, test evaluated는 운영 승인이나 수익 입증이 아니다. 연구의 unregistered 자체는 연구 판단을 막지 않지만 운영 승격도 허용하지 않는다.
- Phase 2 PIT 신선도는 마지막 일봉 7달력일 초과 및 검증 세션 누락 검사다. 계좌·주문·가격의 TTL을 대신하지 않는다. universe_incomplete/revision_history_unavailable 등의 품질 flags를 지우지 않는다.
- 최신 부적격 분석을 과거 적격 분석으로 대체하지 않는다. 서로 다른 시각·모델·snapshot의 방향 점수를 합치지 않는다. 정확한 연구 anchor를 사용하며 immutable replay는 최신 시장 DB를 읽지 않는다.

## 2. D1/D2 후속 분리

**D1 A1 확정:** 전일 이하 일봉 정책을 유지한다. Phase 1 DailyBar는 trading_date>=수신 서울 날짜를 거부하고 capture 역시 당일 범위를 거부한다. PIT infer도 end=as_of 서울 날짜-1일이다. 이는 명시적 보수 정책이며 Phase 3에서 우회하지 않는다.

당일 최종 일봉 A2는 source finality/published_at/received_at·정정 이력·세션 종료·schema/라벨/회귀 검증을 갖춘 별도 Phase 1/2 계약 변경이다. 단순히 장 마감 시각을 지났다고 허용하지 않는다.

**D2 확정:** 운영 등록·철회 이벤트와 공개 시점별 증거 조회는 별도 후속이다. 현재 registry는 (model,direction) 한 행과 등록시각/evidence, 현재 revoked 값만 검사하며 쓰기·승인·불변 이벤트 이력이 없다. evidence_hashes 존재만으로 외부 증거를 입증하지 못한다. 현재 revoked를 바꾸면 과거 조회에도 영향을 주므로 시점별 이력이 완성됐다고 볼 수 없다.

Phase 2 `_latest`는 anchor cutoff로 조회하며 실제 artifact 생성시각을 모두 격리하지 않는다. 향후 운영은 artifact_available_at 및 approval/revocation의 effective_at/recorded_at과 범위를 공개 조회해야 한다. 소급 승인·후일 생성 결과를 과거 운영 정보로 사용하지 않는다. 이번에는 이 후속을 구현하지 않는다.

**모든 operational 요청은 항상 WAIT/blocked, operational_eligible=false, executable=false다.** 임의 승인 플래그·직접 SQL registry 삽입·fixture로 활성화할 수 없다. 등록 evidence reader는 이번 범위에 없다.

## 3. 구조와 불변 계약

```text
Phase 2 읽기 전용 artifact + 불변 가상 계좌/주문/시장 snapshot + 명시적 연구 정책
 → assemble (I/O 및 원본/버전 검증)
 → decide(DecisionInput, DecisionPolicy) → DecisionResult (순수 함수)
 → 별도 Decision SQLite (입력·정책·결과/diagnostics 원자적 저장)
 → 조회 / 불변 replay
```

DecisionInput/DecisionPolicy/DecisionResult는 중첩 상태까지 불변이다. 순수 decide는 DB·네트워크·시계·환경 변수·무작위·모델 학습/추론에 접근하지 않으며 계좌·주문을 변경하지 않는다. 동일 의미 입력/정책/cutoff는 동일 ID·결과이고 실제 computed_at 감사 시각은 별도다.

입력 bundle에는 요청 scope/ticker/decision_as_of, 정확한 분석 ID와 원본 provenance·모델/snapshot/version, 가상 계좌·주문·시장 문맥, 정책 hash가 포함된다. 재현 시 bundle만 읽으며 최신 계좌·정책·시장 DB를 섞지 않는다.

시간은 timezone-aware UTC 저장, 거래일은 Asia/Seoul이다. 연구의 Phase 2 모델/원천 snapshot 생성은 anchor보다 늦을 수 있으나 그 사실을 숨기지 않는다. 가상 계좌·주문·시장 observed_at/received_at은 시나리오 가용 시각이며 실제 snapshot 보관시각과 구분한다. 원천 collected_at을 가상 시각으로 바꾸지 않는다. 미래 시나리오·향후 체결·라벨·고저·수익률을 판단 입력으로 사용하지 않는다.

연구 입력의 실제 분석 provenance와 가상 문맥 provenance는 구분한다. synthetic_test_only 분석을 research_only나 운영으로 승격하지 않는다. 실제 시장 입력에 가상 계좌를 붙인 결과도 연구 판단일 뿐이다.

## 4. 입력과 출력

| 입력 | 필수 내용 |
| --- | --- |
| 요청 | scope, ticker, decision_as_of, analysis_id, 정책 버전 |
| 분석 | mode/origin/restriction, 모델/특징/라벨/snapshot 버전, anchor/cutoff/가용 시각, 문맥과 양방향 점수/상태, input_status/quality_flags |
| 가상 계좌 | 식별자·완전성·관측/가용 시각, KRW 현금 및 예약 기준, 보유·매도 가능 정수 수량, 필요한 평균가, 포트폴리오 평가액/노출/종목 수 |
| 가상 주문 | 완전성·동일 계좌·시각, 활성/부분체결/취소 대기/결과 불명 상태와 예약, 확정 청산 사건/이력 완전성 |
| 가상 시장 | 거래·상장·세션 상태, 품질·가격 기준·가격 시각, 필요한 검증 캘린더 |
| 연구 정책 | 독립 임계값, 허용 모델/품질, Q2 예산·매도 방식·한도, TTL·비용·재진입, 명시적 비활성 RiskExitPolicy |

미제공 계좌를 flat, 현금을 무한/0, 불완전 빈 주문 조회를 주문 없음으로 추정하지 않는다. 금액은 KRW Decimal 문자열, 수량은 비음수 정수. NaN/Inf/bool/다른 통화·예약 모순·미래·stale은 차단한다. 평균가는 손익 기반 규칙에 필요할 때 필수이며 이번에는 그런 규칙이 disabled다. 포트폴리오 한도에 필요한 값은 생략하지 않는다.

결과에는 BUY/HOLD/SELL/WAIT, virtual/blocked 상태, reason_codes와 blocking_reasons, 독립 diagnostics, scope·origin·restriction·입력 근거·정책/version/hash·cutoff, position_state, 검사 결과, 계산 가능한 proposed_quantity/비용 추정, decision_eligible 및 고정 false인 operational_eligible/executable를 저장한다. HOLD/WAIT의 수량은 null이다. 실제 계산시각은 감사 이력에 기록한다.

**HOLD는 유효 평가 후 보유 유지**, **WAIT는 비진입 또는 판단/행동 차단**이다. 보유 중 오류·정지이면 WAIT이며 안전 보유를 뜻하지 않는다. 비보유 HOLD는 없다. 정상 연구 결과는 virtual, 누락/오류/범위 위반은 blocked다. 차단된 매매 후보를 BUY/SELL로 표시하지 않는다.

## 5. 정책과 상태 전이

D3: long-only 현금주식. 추가 매수·공매도·레버리지 제외.
D4: τ_buy/τ_sell은 명시적 독립 연구 정책 값이며 >= 비교. 기본값·Phase 2 0.5 재사용 없음. 순위 최적화·기대수익 계산은 제외한다.
D5: Q2 명시 예산, 정수 수량, 비용 포함 현금, 매도 가능 수량, 포트폴리오 한도. 매도 전량/일부 및 기준·수량·비중·정수화는 정책값이다. 전량 불가를 몰래 일부로 바꾸지 않는다.
D6~D8: 불변 가상 snapshot과 명시 TTL·비용/세금/슬리피지·재진입 제한. 누락 차단. 활성/미확정 동일 종목 주문은 WAIT, 취소나 반대 주문을 만들지 않는다. 새 분석·확정 가상 청산·명시 cooldown을 사용하고 SELL 판단만으로 청산 완료를 만들지 않는다.

| 입력/상태 (상단 차단 우선) | 결과 |
| --- | --- |
| 모든 운영 요청, 연구/합성의 운영 유입 | WAIT/blocked, operational_unavailable (scope 위반 근거 보존) |
| 신원 불명·미래/오래된 입력·누락/불완전 정책·품질 불량·양방향 충돌 | WAIT/blocked |
| 계좌/주문 불명·미확정/활성 주문·정지/거래 불가 | WAIT/blocked |
| 손절/익절/기간 청산 enabled 또는 disabled 명시 누락 | WAIT/blocked |
| flat, prior_decline, up_score>=τ_buy, Q2/한도/비용/재진입 충족 | BUY/virtual |
| long, prior_rise, down_score>=τ_sell, 수량/거래/비용/한도 충족 | SELL/virtual |
| 후보는 있으나 자금·한도·수량·비용·재진입 실패 | WAIT/blocked |
| 유효 flat, 진입 조건 없음 | WAIT/virtual |
| 유효 long, 청산 조건 없음 | HOLD/virtual |
| 유효 long+prior_decline, 정상 down_score=null/context_mismatch | HOLD/virtual + 별도 청산 신호 부재/리스크 비활성 진단 |

정상 비적용 null은 오류가 아니다. 적용 방향의 모델/점수 부족은 WAIT다. 낮은 up_score를 SELL 근거로 바꾸지 않는다. 두 방향을 합쳐 새로운 확률을 만들지 않는다. 보유·현금 상태는 외부의 새 가상 snapshot으로만 바뀐다.

비용은 명시적인 가정으로 예산·정수 수량·현금/노출 검증에만 쓴다. score×장벽-비용으로 기대수익을 만들지 않는다. 수수료·세율 수치를 정하지 않으며 누락을 0으로 채우지 않는다. 다종목 독립 판단은 같은 현금을 소비할 수 있으므로 자금 예약·일괄 실행 가능성을 주장하지 않는다.

## 6. D9 독립 리스크 청산과 HOLD 진단

RiskExitPolicy는 stop_loss, take_profit, max_holding_period 각각 mode=disabled와 parameters=null을 명시한다. 누락·enabled·모순 파라미터는 차단한다. inspect_risk_exit_policy는 진단만 반환하며 활성 청산 evaluator는 만들지 않는다. 수치를 넣는 것만으로 활성화할 수 없다.

보유 prior_decline에서 정상 null 때문에 HOLD가 나오면 행동 사유 no_exit_condition과 별개로 다음을 반드시 저장·조회·replay한다.

| diagnostics | 값 |
| --- | --- |
| exit_signal_status | no_applicable_exit_signal |
| exit_model_status | context_mismatch |
| risk_exit_rules | 세 규칙 각각 disabled/policy_disabled |
| diagnostic_codes | exit_signal_absent_in_decline_context, risk_exit_rules_disabled |
| safety_assurance | false |

단일 r5<0으로 지속 하락 전체를 증명하지 않는다. 계속 하락 문맥인 여러 입력에서도 진단을 유지한다. 이 경고만으로 정상 HOLD를 WAIT로 바꾸지 않지만 실제 필수 입력 오류는 WAIT다. HOLD는 손실 제한·안전성 보장이 아니다. 실제 손절·익절·최대 보유 기간의 활성화/수치는 별도 승인이다. H=5나 레거시 20일을 사용하지 않는다.

## 7. 저장·조회·테스트

D10: 별도 Decision SQLite에 입력·정책·결과를 한 트랜잭션으로 보관한다. Phase 2 시장/분석/모델 DB는 읽기 전용이며 스키마·artifact·감사 기록을 쓰지 않는다. 같은 경로·파일 alias는 거부한다. 결정 artifact는 불변·내용 hash 검증, 실제 실행 감사는 별도 누적이다. 정책/입력 변경은 새 결정이고 replay는 원래 입력만 사용한다.

연구 판단/조회 CLI는 dsv decide / dsv decisions다. 운영 scope는 차단 결과만 반환한다. 기본 조회가 운영이어도 연구로 fallback하지 않는다. 실제 주문·계좌 시뮬레이션·HTTP/iOS/증권사 API·전체 백테스트·자동매매·스케줄러는 제외한다.

테스트: 결정표·>= 경계·정상/비정상 null·NaN/누락·신선도/미래·Q2 현금/비용/정수/매도 전량/일부·포트폴리오·재진입/주문 완전성·HOLD 진단·운영 불변 차단·JSON 승인/registry 우회 차단·순수 함수 I/O 금지·동일 입력 멱등성·DB 변경/삭제 후 replay·원자적 실패·SQLite 별도 파일·Phase 1/2 회귀·Ruff/mypy/build·독립 wheel smoke. Python 3.12/3.13/3.14 CI 유지. 합성 테스트는 성능 증거가 아니다. 수익률·체결·거래비용 현실성 검증은 Phase 4다.

## 8. 미정 수치와 별도 승인 사항

| 정책 | 명시적으로 받아야 할 값 |
| --- | --- |
| 임계값 | 독립 τ_buy/τ_sell, 고정 모델 버전 |
| Q2 예산 | 금액·정수화 규칙·현금/예약 기준 |
| 매도 | 전량/일부, 보유/매도 가능 기준, 일부 수량/비중·반올림 |
| 한도 | 종목/총 노출·최대 종목 수·1회 금액·평가 규칙 |
| TTL | 계좌/주문/시장/가격/분석별 값·단위·경계·허용 시간 차이 |
| 비용 | 매수/매도 수수료·세금·슬리피지·반올림·출처/시점 |
| 재진입 | cooldown 길이/단위/경계·초기 이력 처리 |
| 손절/익절/보유 기간 | 현재 명시 disabled, 활성화/수치/우선순위 별도 승인 |

미정은 기본값을 고르라는 뜻이 아니다. 필요한 값이 없으면 WAIT/blocked다. 명시 fixture 수치는 구현 검증용이며 실제 매매 정책으로 발표하지 않는다. 사용자 연구 정책 변경은 새 불변 버전이다. D1~D10 구조 승인을 실거래 승인으로 해석하지 않는다.
