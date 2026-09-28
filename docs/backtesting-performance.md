# Phase 4 PR-G: 연구 성과 계산·보고

## 범위와 공개 경계

`backtest.performance.calculate_performance(run_result, tape, valuation_policy)`는
[Runner](backtesting-runner.md)의 불변 결과와 고정 tape를 소비하고 `FrozenJSON`을 반환한다.
`identifier`가 canonical performance hash이며 결과에 원본 `run_hash`를 보존한다.
Runner, Phase 2/3 정책, Ledger 및 Execution의 계약은 변경하지 않는다.

현재 Runner와 동일하게 **synthetic / historical_research / backtest**만 지원한다.
`operational_eligible=false`, `executable=false`이며 실제 PIT/OOS 성과가 아니다.
실제 시장 데이터 로더, 학습, 전략 최적화, 유동성 모델, 영구 Simulation DB를 추가하지 않는다.
종료 보유 포지션을 강제 청산하지 않는다. 합성 예시는 투자 성능의 증거가 아니다.

## 검증과 관측 지점

최종 checkpoint를 복원하고 manifest/tape hash, 최종 계좌, 사건별 checkpoint를 검증한다.
초기 Ledger부터 성공한 reservation 및 confirmed fill 명령을 순서대로 재생한다.
실패한 사건은 경제적 상태를 변경하지 않는다. 기간 내 tape 사건 수·시각·sequence와
Runner 보고를 대조하며 계좌 숫자만 신뢰하지 않는다.
체결이 있으면 [verified position history](backtesting-position-history.md)가 필수이며,
기존 검증기로 decision → admission → reservation → execution → fill 연결을 다시 검증한다.

Equity curve는 초기 상태, 각 Runner 사건 처리 후, 종료 상태 순서로 생성한다.
각 point는 UTC timestamp, sequence, stage, cash, known_position_value,
nullable total_equity, complete, positions 및 coverage를 보존한다.
시작 시각에 여러 사건이 있으면 초기 평가는 첫 사건 sequence까지만 공개한다.
그 외 초기 평가는 시작 이하 마지막 sequence를 사용한다. 종료 평가는 종료 이하 마지막
sequence를 사용한다. 동일 시각의 initial/event/final point는 stage로 구분한다.
모든 mark는 PR-B `tape.view(clock)`의 공개 필드만 사용한다.
미공개 종가나 종료 이후 사건을 평가에 사용하지 않는다.

## 명시적 valuation policy

다음 모든 필드가 필요하다. 아래 선택지는 지원 계약이며 거래 수치 기본값이 아니다.

| 필드 | 계약 |
|---|---|
| version | 사용자 정책 버전 |
| field | open / high / low / close 중 하나 |
| selection | latest_public_sequence를 명시적으로 선택 |
| max_age_seconds | 0 이상 정수 TTL |
| money_quantum | 양의 10의 거듭제곱 단위 문자열 |
| money_rounding | half_up / down / up |
| ratio_quantum | 양의 10의 거듭제곱 단위 문자열 |
| ratio_rounding | half_up / down / up |
| schedule | start_events_end |
| external_cash_flow_krw | 현재는 명시적 문자열 "0"만 지원 |

가격·금액·비율은 Decimal 문자열이며 로컬 precision 80을 사용한다.
포지션별 가격×수량을 money 단위로 반올림하고 합산한다. 수익률·승률은 ratio 단위로
반올림한다. NaN/Infinity, 잘못된 단위, 누락된 정책을 거부한다.

공개된 후보 중 sequence가 가장 큰 mark를 선택한다. 해당 mark의 사건 시각과 필드
공개 시각 **모두** cutoff 이하이며 TTL 이내여야 한다. unadjusted, 빈 quality_flags,
cutoff 이하 quality_available_at, listed/trading 상태만 평가한다.
선택된 mark가 부적격이면 오래된 다른 가격으로 임의 fallback하지 않는다.
거래정지·수정주가의 대체 평가 정책은 지원하지 않는다.

## Initial/final equity와 결측

초기 equity = 초기 총현금 + 초기 보유 주식의 공개 mark 평가금액.
원가는 초기 market valuation의 대용물이 아니다.
최종 equity = 최종 총현금 + 종료 open position 평가금액.
총현금에는 예약 현금이 포함된다. 예약 자체는 자산 소멸이나 체결이 아니다.

하나라도 mark가 없거나 오래되었거나 품질 증거가 부족하면 해당 position은 unvalued다.
known cash, known position value, known unrealized P&L 및 종목별 이유를 남기고
완전한 equity와 aggregate unrealized P&L은 null로 둔다.
Coverage는 평가된/전체/미평가 포지션 수 및 complete/전체 point 수를 제공한다.
이는 종목 수 기준이며 가치 비중으로 오인하면 안 된다.

두 끝점이 complete일 때만 다음 비율을 계산한다.

`total_return = (final_equity - initial_equity - net_external_cash_flow) / initial_equity`

완전한 initial equity가 0 이하이면 오류다. 초기 equity가 알려지지 않으면 return은 null이다.
현재 Ledger/Runner에 외부 입출금이 없으므로 nonzero external cash flow를 거부한다.
향후 입출금은 별도 검증된 원장 계약이 필요하며 여기서 추정하지 않는다.

## MDD

각 complete point의 이전 최고 equity 대비 감소율 중 최대값이다.
양의 peak가 필요하며 peak/trough의 timestamp·sequence·stage를 함께 반환한다.
동률이면 최초 최대 감소를 보존한다. 감소가 없으면 MDD 0, peak/trough null이다.

불완전한 point 또는 비양수 peak가 있으면 정확한 `mdd.ratio`는 null이다.
확인 가능한 complete point에서의 `observed_lower_bound`와 unavailable_points를 별도로 남긴다.
하한은 반올림으로 부풀리지 않도록 ratio 단위에서 **항상 down**한다.
모든 point가 complete이면 ratio_rounding을 적용한 MDD를 반환한다.
이는 선언된 관측 지점의 MDD이며 장중 경로 전체의 MDD가 아니다.
끝점이 complete여서 return을 제공하더라도 중간 결측으로 MDD는 불완전할 수 있다.

## 손익·거래 episode·비용

완료 거래는 acquisition → `confirmed_full_exit` position episode 하나다.
fill 개수를 거래 횟수로 세지 않는다. partial_exit/still_open은 승률 분모에서 제외한다.
초기 보유 포지션의 취득 시각은 알 수 없으므로 entry_at=null로 보존한다.

- 취득 경제 원가: 검증된 BUY notional + fee + tax. 초기 보유분은 명시적 초기 cost basis.
- 순청산대금: 해당 episode의 모든 SELL notional - fee - tax 합계.
- realized_pnl: 완료 episode 순청산대금 - 취득 경제 원가 합계.
- ledger_realized_pnl: 부분 매도까지 포함한 Ledger 확정 손익.
- open_episode_realized_pnl: 위 두 실현손익의 차이. 미완료 episode의 확정 손익을 숨기지 않는다.
- unrealized_pnl: 종료 mark 평가금액 - Ledger의 잔여 cost basis.
- episode return: realized P&L / 취득 경제 원가. 원가가 0 이하이면 null과 이유를 남긴다.

초기 원가와 초기 시장 가치가 다르면 realized+unrealized가 기간 중 equity 증가와
같을 필요는 없다. 초기부터 존재한 미실현 손익을 구분해 해석해야 한다.

양수/음수/0 손익을 win/loss/breakeven으로 분리한다.
`win_rate = wins / completed_trades`이며 breakeven도 완료 분모에 포함한다.
완료 거래가 없으면 win_rate=null이다.

Fee, SELL tax, slippage monetary impact는 검증된 PR-E fill의 값을 그대로 합산한다.
Slippage는 이미 실행 가격에 반영되어 있다. 수수료와 세금도 Ledger 현금·원가에 반영되어
있으므로 손익에서 다시 차감하지 않는다. `total_explicit_trading_costs = fees + sell_taxes`이며
slippage는 별도 진단이다. Gross 가상 성과를 재추정하지 않는다.
fill/BUY/SELL/rejected execution 수와 최종 open/unvalued 수를 제공하지만 turnover와
liquidity 지표는 만들지 않는다.

## 서비스·최소 CLI

`backtest.service.freeze_request(existing_runner, explicit_valuation_policy)`가 이미 구성한
불변 Runner 입력을 JSON으로 저장할 수 있게 한다. `run_backtest(...)`는 그 입력으로
Runner → Performance → Report를 실행한다. 네트워크나 모델 private API를 호출하지 않는다.

```python
from pathlib import Path
from donghak_stock_vision.backtest.service import freeze_request

# existing_runner와 explicit_valuation_policy는 호출자가 실제 계약대로 구성한다.
request = freeze_request(existing_runner, explicit_valuation_policy)
Path("synthetic-request.json").write_text(request.payload_json, encoding="utf-8")
```

```bash
dsv backtest --input synthetic-request.json \
  --start 2026-01-05T00:00:00Z --end 2026-01-12T02:00:00Z \
  --initial-cash 10000 --tickers 005930 --format text
```

시각에는 UTC offset이 필수다. 날짜만 받아 임의 거래 시각으로 바꾸지 않는다.
기간은 고정 manifest 안에서 선택한다. 초기 현금과 순서가 있는 ticker universe는
frozen input과 일치해야 한다. 다른 자본·종목으로 실행하려면 manifest/tape/초기 계좌를
그 입력에 맞춰 먼저 구성해야 한다. CLI에서 hash를 재작성하거나 가짜 데이터를 만들지 않는다.
모든 decision/admission/execution/valuation 정책은 입력 파일에 명시해야 한다.
`history_mode=verified_execution`인 입력을 사용해야 체결 성과를 검증할 수 있다.

`--format json`은 run/performance 객체와 각각의 hash를 출력한다. text는 equity, return,
손익, MDD, 승패, 비용, 최종 포지션, episode 및 limitations를 출력한다.
표시 형식 외 정책 기본값은 없다. 오류는 기존 CLI와 동일하게 종료 코드 2다.
입력의 실제 시장/분석 DB 로딩은 제공하지 않는다. 따라서 일반 종목의 실전 백테스트나
PIT/OOS 실행 명령으로 사용하면 안 된다.

## 재현성과 검증

시스템 시각·난수·네트워크에 의존하지 않는다. 동일 frozen 입력과 정책은 동일 Runner hash,
equity curve, performance hash 및 보고서를 생성한다. 입력/정책 변경은 hash에 반영된다.
사건별 checkpoint 재생은 메모리 검증이며 새 영구 저장소 transaction을 만들지 않는다.

테스트는 cash-only, open position, BUY→SELL→BUY, 두 round trip, 손익/본전,
초기 원가와 mark 차이, 결측/미래 mark, 부분 청산, 비용 중복 차감 방지,
MDD·반올림·불완전 coverage, 이력 변조, CLI 및 결정적 replay를 검증한다.
독립 wheel에서도 준비된 합성 frozen 입력으로 전체 CLI 흐름을 실행한다.
