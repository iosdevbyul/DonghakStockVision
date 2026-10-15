# Historical research backtest: DB부터 보고서까지

이 경로는 `historical_research` 가상 실행이다. 실제 과거 체결 복원, PIT 검증,
OOS 검증 또는 투자 성능 보증이 아니다. 모델 학습·전략·비용 계산기를 추가하지 않는다.

## 실행

```sh
dsv backtest \
  --market-db /absolute/path/market.sqlite3 \
  --analysis-db /absolute/path/analysis.sqlite3 \
  --run-output-db /absolute/path/new-run.sqlite3 \
  --model-id "$MODEL_VERSION" \
  --config /absolute/path/historical-run.json \
  --start 2025-01-01 --end 2025-06-30 \
  --initial-cash 10000000 --ticker 005930
```

`--format json`은 요청, 전체 frozen input, Runner 결과, position history,
Performance, text report, 각 hash와 차단 사유를 함께 출력한다. 기본은 text다.
기존 `--input synthetic-request.json` 경로와 `--tickers` 옵션은 유지한다.
두 입력 경로는 상호 배타적이며 DB/model/config 경로나 정책을 추측하지 않는다.

DB 경로에서는 start/end를 **서울 거래일 YYYY-MM-DD, 양 끝 포함**으로 받는다.
초기 현금은 양수 Decimal 문자열이다. 0·음수·NaN·Infinity·float는 거부한다.
초기 보유 포지션 원가는 현금에서 다시 차감하지 않으며, 초기 평가 가격이 없으면
기존 Performance 규칙대로 initial equity/return을 미완성으로 표시한다.

중복 ticker는 정렬·중복 제거한다. builder는 여러 ticker를 지원하지만 이번 실행 서비스는
동시 예약 revision 제약 때문에 단일 ticker만 허용한다. 자동 종목 선택은 없다.

## 필수 config

`historical-run.json`의 최상위 필드는 아래와 정확히 일치해야 한다. 모든 거래 수치는
사용자가 정한다. 이 문서는 비용·임계값·예산의 운영 기본값을 제공하지 않는다.

| 필드 | 계약 |
|---|---|
| `manifest` | [PR-A RunManifest](backtesting-contracts.md). real / research_only / historical_research / backtest, 운영·실행 플래그 false. 명시적인 초기 계좌 ID/revision/보유 수량/원가 포함. 현금과 가상 초기 관측시각만 요청으로 대체한다. |
| `policy` | 기존 PR-A ExecutionPolicy 참조/가용성 계약. 아래 실제 execution policy와 별개인 기존 타입이다. |
| `captured_at`, `quality`, `availability_assumption` | [historical input](historical-backtesting.md) 계약. 실제 수집 cutoff 및 검증된 quality manifest. assumption은 `after_trading_date_seoul_midnight`. |
| `decision_policy` | [Phase 3 DecisionPolicy](decision-contract.md). 모델 허용 목록, 방향별 임계값, sizing, TTL, 비용, 재진입, 명시적 disabled risk exits 등 기존 필수 필드 모두. |
| `execution_policy` | [historical execution](historical-execution.md). `liquidity=historical_daily_next_open_full_fill`, `execution_price_field=open`. fee/tax/slippage/rounding 등 PR-E의 명시적 필드 모두. |
| `ledger_policy` | [LedgerPolicy](backtesting-ledger.md). KRW, 명시적 average 원가법·immediate_virtual 결제·비용 처리·rounding·shortfall reject. |
| `valuation_policy` | [Performance 정책](backtesting-performance.md)의 기존 모든 필드에 `historical_marks=verified_daily_research`를 추가한다. mark field, TTL, money/ratio quantum·rounding, schedule, external cash flow 모두 명시한다. |
| `admission_policy` | [DecisionBridge 정책](backtesting-decision-bridge.md)의 기존 모든 필드. ordered_tickers는 canonical universe와 일치. 예산·최대 수량·종목 한도·지연·만료를 명시한다. `marks`의 sequence/field는 각 공개 decision event와 아래 필드로 바인딩한다. |
| `reservation_by_side` | `{"BUY":{"max_notional_krw":"사용자 금액","max_cost_krw":"사용자 금액"},"SELL":{"max_notional_krw":"0","max_cost_krw":"0"}}`. BUY 한도를 명시하며 SELL 현금 예약은 0. |
| `decision_dates` | `"all_available"` 또는 기간 내 정렬·중복 제거된 거래일 문자열 배열. 날짜 지정은 decision 시점만 선택하며 BUY/SELL을 지정하지 않는다. |
| `decision_mark_field` | `open`, `high`, `low`, `close` 중 명시적 선택. 해당 complete bar가 공개된 이후 Phase 3 입력에 사용할 가격이다. execution reference는 항상 **다음 세션 OPEN**이다. |

계약 객체 검증은 `HistoricalBacktestRequest(...)` 생성 시 실행하며 서비스 실행 전에
기존 Phase 3 정책, PR-E execution 설정, Ledger/valuation/admission 설정을 검증한다.
DB와 모델 snapshot의 내용 검증은 builder가 수행한다. 별도 dry-run 엔진은 추가하지 않는다.

## 공개 API와 책임

`backtest.historical_service.HistoricalBacktestRequest`와
`run_historical_backtest(request, market_store, analysis_store, run_output=output_store)`를 사용한다.
반환 `HistoricalBacktestResult`는 불변 `FrozenJSON` document와 identifier/report를 제공한다.

1. Phase 1 `MarketDataStore.read()`로 정제된 일봉을 읽는다. CLI의 시장 SQLite 연결은
   read-only이며 스키마 생성·데이터 수정을 하지 않는다.
2. 기존 `build_historical_input()`이 source를 복사하고 공개
   `SignalService.research(model_id, model.snapshot_id, tickers, anchor_date)`를 호출한다.
   private inference, 재학습, 가짜 분석 또는 고정 매매 방향을 production에 넣지 않는다.
3. 모델의 학습 snapshot과 요청 기간 DB rows가 정확히 일치해야 한다. mismatch,
   missing model, lookback 부족, 분석 불가를 `unavailable`에 보존한다.
4. 이후 Runner는 frozen tape/analysis만 사용한다. 기존 `market_context()`와 공개 gate로
   decision 입력을 만들고 Phase 3가 판단한다. Runner는 확정된 action에 맞춰 명시적인
   BUY/SELL 예약 설정을 선택할 뿐 수량·금액을 계산하거나 판단을 바꾸지 않는다.
   기존 단일 reservation 형태도 그대로 지원한다.
5. Bridge → admission → Ledger reservation → 이후 eligible event → PR-E execution →
   Ledger → verified position history를 재사용한다.
6. Performance는 동일 frozen historical bundle과 run 참조를 재검증한다. 계산 공식은
   그대로 두고 명시적으로 검증된 historical source를 허용하는 경로만 추가한다.

원본 시장/분석 DB는 CLI에서 모두 읽기 전용(`mode=ro`, `query_only=ON`)으로 연다.
`--run-output-db`는 필수이며 **새 파일**이어야 한다. 기존 파일·별칭·원본 경로를
출력으로 지정하면 거부한다. 자동 기본 경로나 원본 DB 복사는 없다.
`ResearchRunStore(source, output)`은 모델·dataset·snapshot과 원래 created_at을
source에서 검증해 읽고, 새 signal과 analysis_executions 감사 행만 output에 기록한다.
source에 과거 signal이 있어도 이번 실행 신호를 대체하지 않는다. 모델/정책 등록은 없다.
Python API 호출자도 별도의 output store를 명시해야 하며 양쪽 store의 수명은 호출자가 관리한다.
결과 JSON에는 모든 frozen evidence와 기존 order/reservation/fill/history/performance 참조가 남는다.
run-output DB는 신호/추론 감사 저장소이며 결과 JSON을 대신하는 거래 원장은 아니다.
보고서는 `--format json` 출력도 별도로 보관해야 한다.

## 분석 날짜 제한

`build_historical_input(..., decision_dates="all_available", run_output=output_store)`가
서비스의 `decision_dates`를 **추론 전에** 적용한다. 기본 all_available은 기존 모든
관측일 분석을 유지한다. 명시 배열은 canonical `YYYY-MM-DD` 문자열을 오름차순으로
한 번씩만 포함해야 한다. 잘못된 타입/날짜, 중복, 역순, 기간 밖 날짜와 알려진 불완전
연구 세션은 거부한다. 빈 배열은 분석 없이 관찰만 한다.
요청했지만 bar가 없는 날짜는 `missing_decision_bar` unavailable 항목으로 남기며
주말·휴일 또는 이웃 거래일로 추정하지 않는다. 종목 전체가 비어 있으면 기존
`missing_historical_data` 결과를 유지한다.

이 제한은 signal 생성/분석 공개 지점만 줄인다. tape의 모든 시장 사건, 이후 체결과
평가에 필요한 일봉, 학습 snapshot의 이전 warmup, 실제 source cutoff와 사건 순서는
유지한다. 거래일별 BUY/SELL을 지정하거나 미래 가격으로 날짜를 고르는 기능이 아니다.

## 실제 계산과 증거 수준

`tests/backtest/test_public_inference.py`는 명시적 fake/synthetic 데이터에서 기존
TrainingService로 실제 선형 모델을 학습하고 공개 SignalService로 trailing feature와
점수를 계산한다. 가짜 점수·ControlledResearch·운영 등록 없이 기존 synthetic Runner,
Ledger, verified history 및 Performance로 왕복/재진입을 검증한다. 고정된 mechanics
정책과 날짜는 수익성 평가 정책이 아니며 결과에는 synthetic_test_only가 유지된다.
실데이터 전용 historical builder/service의 real gate는 이 테스트 때문에 완화하지 않는다.
기존 ControlledResearch 테스트는 별도의 storage/체결 계약 검증으로 유지한다.

로컬 실모델은 fingerprint가 맞아도 reviewed research quality와 전체 명시 정책/config가
필요하다. 학습 snapshot의 quality가 null인 모델만 있다는 이유로 quality를 발명하지 않는다.
전제조건이 없으면 real-artifact 검사는 blocked이며 실제 재생·수익률 주장을 하지 않는다.
재생이 허용되는 경우에도 source 파일 hash를 전후 대조하고 별도 run output만 사용한다.

## 시간과 평가

D 일봉 전체는 기존 가정대로 **D+1 00:00 Asia/Seoul**에 공개된다.
D 의사결정 뒤 tape에 존재하는 다음 eligible 세션의 OPEN이 reference이며,
그 세션의 complete bar가 공개되기 전에는 fill을 알려진 것으로 처리하지 않는다.
09:00 체결 사실로 소급하지 않는다. 주말·공휴일 bar를 생성하지 않는다.
요청 end 거래일의 다음 자정까지 그 거래일의 공개를 처리하지만, end 이후 거래일은
읽거나 체결하지 않는다. 마지막 미체결 주문과 보유 포지션은 그대로 남긴다.

Slippage·fee·SELL tax·반올림·예약 부족 거부는 PR-E가 담당한다.
추가 현금 사용, 자동 수량 축소, 동일 event 체결 및 가격 fallback은 없다.

Historical valuation은 검증된 동결 일봉의 공개 mark만 사용한다. unadjusted,
quality, 공개 cutoff, 명시적 TTL을 검사한다. historical source의 closed/unknown
시장 상태는 실제 거래 가능성으로 바꾸지 않으며, 명시적 research mark 가정 아래에서만
평가에 사용한다. 무거래·품질 불량은 평가 증거로 승인하지 않는다.
missing/stale mark이면 해당 포지션은 unvalued이며 완전한 return/MDD를 만들지 않는다.
종료 강제 청산도 없다. 비용은 Ledger의 경제 상태에 반영된 금액을 중복 차감하지 않는다.

## 결과 해석과 재현

Text/JSON은 initial/final equity, return, realized/unrealized P&L, MDD/coverage,
completed episodes, wins/losses/breakeven, win rate, fees/taxes/slippage, final cash,
open positions 및 각 hash를 제공한다. `unavailable`과 `blocked`에는 원래 이유를 보존한다.
입력 일부를 사용할 수 없으면 `completed_with_unavailable_inputs`로 표시한다.
이때 cash-only 0%는 사용 가능한 사건의 산술 결과일 뿐, 누락된 분석의 성능이 아니다.

반드시 함께 보존하는 한계:

- latest-only historical snapshot, revision history 없음
- PIT/OOS 검증 false, model training cutoff 검증 false
- full-fill 연구 가정, 실제 호가·유동성·주문 증거 없음
- complete daily bar publication 후에만 체결 결과가 알려짐
- 단일 ticker 실행, 실제 모델과 동일 snapshot 제약
- 누락 세션 보간 없음, 당일 장중 MDD가 아닌 선언된 관측점 MDD

동일 저장 artifact, DB 내용, 요청과 정책을 재실행하면 input/run/performance/report hash가
일치한다. Phase 2 artifact의 실제 최초 생성 시각은 증거로 보존한다. 최초 생성 전의 DB를
복제해서 서로 다른 시각에 새 artifact를 생성하면 감사 시각도 다른 입력이므로, **완전한
재현에는 결과의 frozen input과 최초 artifact를 함께 보관해야 한다**. 생성 시각을 과거로
위조하거나 PIT 증거로 바꾸지 않는다. source 감사 시각을 지운 경제적 별도 run identity는
기존 계약에서 제공하지 않으므로 이번 연결 계층에서 임의로 만들지 않는다.

테스트의 연구용 SQLite와 분석 test double은 production SignalService 연결을 바꾸지 않는다.
fixture의 수익률은 산술·연결 검증이며 실제 종목 추천이나 시장 예측 성능 증거가 아니다.

## 검증

```sh
pytest tests/backtest/test_historical_service.py
pytest
ruff check .
mypy
python -m build
```

독립 wheel 검증은 checkout의 src를 경로에 넣지 않고, fixture SQLite에서 동일 서비스 및
CLI를 실행하며 BUY→SELL→BUY, Ledger/Performance/Report와 반복 hash 일치를 확인한다.
실제 DB smoke는 존재하는 데이터와 모델을 읽기 전용으로 점검할 수 있을 때만 수행한다.
네트워크 수집, KRX 호출, model mismatch 우회는 하지 않는다.
