# Historical daily execution policy

## 지원 경로와 명시적 선택

`execution.settings`의 기존 `liquidity="synthetic_explicit_full_fill"`은 그대로 유지한다.
새 선택지는 `liquidity="historical_daily_next_open_full_fill"`이다.
기존 실행 정책의 나머지 필드는 전부 필수이며 가격 field는 **open만** 허용한다.
`fill_mode=full`, `scope=research`이고 실제 주문, broker routing, PIT, paper/live 모드를
허용하지 않는다. 별도 가격·수수료 계산기를 만들지 않는다.

기존 실데이터 차단은 일봉만으로 당시 거래소 개장·상장·거래정지 상태와 실제 유동성을
증명할 수 없기 때문이었다. 이를 검증한 것으로 변경하지 않는다. 새 경로는 검토된 품질
manifest와 unadjusted latest-only 일봉에 대한 **명시적 가상 full-fill 가정**이다.
거래량은 무거래 bar 배제에만 사용하며 주문 수량 대비 참여율·전량 체결 능력은 추정하지 않는다.

## 시간: D 판단 → 다음 존재하는 session OPEN

D bar의 전체 OHLCV가 D+1일 00:00 Asia/Seoul에 공개된 뒤에만 판단과 주문 후보를 만든다.
기존 historical input의 공개 가정을 그대로 유지한다. 사건 sequence와 UTC 시각이 모두
판단·후보·접수보다 미래인 동일 ticker의 첫 후속 session만 체결 대상이다.
달력을 +1일 해서 가격을 만들지 않는다. 금요일 bar 판단 뒤 월요일 bar가 다음이면
월요일 OPEN을 기준 가격으로 사용한다. 같은 D bar 또는 수정 revision은 체결 대상이 아니다.

**OPEN 가격의 session 날짜와 원장 반영 시각은 다르다.** 월요일 bar OPEN은 입력 계약상
화요일 00:00 Seoul에 확인된다. fill_at/fill_known_at과 원장 경제 상태 변경은 그 공개
시각에 일어난다. 월요일 09:00에 이미 관측·체결한 것처럼 backdate하지 않는다.
결과의 execution_session이 가격의 월요일 거래일을 보존한다.
이는 next-session OPEN 가격을 쓰는 연구 시뮬레이션이지 실제 장 시작 체결 복원이 아니다.

공개 prefix에 도달하기 전에는 미래 OPEN을 decision/mark로 전달하지 않는다.
확인된 후속 session을 건너뛰어 유리한 나중 OPEN을 선택할 수 없다.
첫 후속 bar가 무효이거나 예약금 부족으로 거부되면 주문은 open으로 남는다.
이 버전은 이후 다른 session으로 자동 이월 체결하거나 예약/수량을 조정하지 않는다.
다음 session이 기간 안에 없으면 미체결이며 마지막 close나 기간 밖 가격을 사용하지 않는다.

## Frozen 증거 검증

`BacktestRunner(..., historical_input=bundle.document)`로 원본 HistoricalBacktestInput을
명시적으로 전달해야 한다. 정책 문자열만 바꾸거나 allow 플래그를 넣어서는 실행되지 않는다.

검증 항목:

- real / historical_research / research_only / backtest provenance
- bundle의 FrozenTape 및 실제 Runner tape 일치
- source hash 집합, latest_daily 원본과 수집 시각, quality manifest 및 검토 시각
- 거래일 범위, ticker universe, 중복 session/revision 금지
- 전체 일봉 field 공개 시각이 D+1 Seoul 자정인지
- FrozenAnalysis checksum·동일 학습 snapshot·원본 시장 행 일치
- Runner 분석 tuple이 bundle에 동결된 분석들과 일치하는지
- Bridge의 분석 session/최신 mark/계좌 revision·포지션 이력·정책 binding
- acceptance 정책·입력 hash 및 reservation checkpoint, fill 재생 검증

기존 same-event, 미래 정보, no pyramiding, confirmed exit, 중복 fill 및 예약 검사는 유지한다.

## Phase 3용 가상 주문접수 문맥

Phase 3는 `tradable/open/listed` 문맥을 요구하지만 원본 daily event의 시장 상태는
closed/unknown/unknown이다. 원본을 덮어쓰지 않는다.
`historical_execution.market_context(tape, document, ticker, clock, field=...)`는 검증된
공개 mark로 **virtual order-entry 문맥**을 만든다. snapshot_version은
`historical_daily_order_intent_v1`이다. 여기서 open/listed는 연구용 주문접수 가정이며
실제 거래소가 해당 자정에 개장했거나 상장 상태를 검증했다는 뜻이 아니다.
Bridge는 이 정확한 projection과 명시적 historical 정책·source evidence가 함께 있을 때만
허용한다. 이 문맥을 운영 판단이나 실거래에 사용할 수 없다.

Phase 3 판단 정책·threshold·TTL·비용 추정은 변경하지 않는다. 사용자는 D 16:00 분석
anchor와 다음 자정 공개의 차이를 감당하는 TTL/skew를 명시해야 한다. 부족하면 원래대로
WAIT/blocked다. 모델 학습 cutoff가 과거였다는 증거도 만들지 않는다.

## 가격·gap·비용·예약

다음 session OPEN을 benchmark_price_krw/source_open에 그대로 기록한다.
D close와 차이가 있어도 clamp하거나 대체하지 않는다. Phase 1 정수 KRW를 정확히 Decimal로
처리한다. HIGH/LOW/CLOSE 선택, 누락 OPEN fallback, 소수 주식, partial fill은 거부한다.

PR-E 계산을 재사용한다:

- BUY 가격 = OPEN × (1 + 명시적 slippage_rate), price_quantum 단위 adverse 올림
- SELL 가격 = OPEN × (1 - 명시적 slippage_rate), 같은 단위 adverse 내림
- BUY/SELL fee와 SELL tax는 실제 실행 notional에 명시적 rate·cost rounding 적용
- BUY tax는 기존 not_applicable 계약; slippage=0도 명시해야 한다

금액 계산은 기존 Decimal precision 80이다. 수수료·세금·slippage 기본값은 없다.
원장만 현금·주식·예약을 변경한다. 예약을 초과한 BUY는 거부하며 추가 현금을 가져오거나
수량을 줄이지 않는다. SELL도 예약 수량을 초과할 수 없다.

Phase 3 예상 비용과 PR-D admission budget이 PR-E 예약의 상한이다. next-open gap에 대비한
여유 예약을 원하면 Phase 3 예상 비용·예산도 그 범위를 명시해야 한다. execution이
admission budget을 임의로 확대하지 않는다.

## 결과·재현성

accepted/filled/rejected 결과에 research_only와 다음 제한을 보존한다:
full-fill assumption, 실제 호가/유동성 검증 부재, 실제 주문 부재, revision 부재,
PIT/OOS 아님, model training cutoff 미검증, complete bar 공개 이후에만 fill 확인.

기존 decision/order/reservation/source event/fill/policy 참조를 유지하고 execution_session,
source_open, slippage_rate를 추가한다. 실제 가격·fee·tax·slippage 금액은 기존 VirtualFill에
남는다. acceptance에 frozen 입력을 보존하므로 DB 없이 이력 검증과 replay가 가능하다.
동일 bundle·초기 계좌·정책·config는 동일 orders/fills/Ledger/run hash를 만든다.
현재 시각과 난수를 execution/runner에서 사용하지 않는다.

## Python 연결과 범위

[historical input](historical-backtesting.md)을 먼저 만들고 다음을 명시적으로 구성한다:

1. 기존 initialize로 bundle tape/manifest에 연결한 Ledger 초기 상태
2. Phase 3 DecisionPolicy와 marks/admission/reservation을 포함한 Runner config
3. 위 historical execution 정책(기존 PR-E 필수 비용 필드 포함)
4. `market_context(...)`로 각 공개 decision step의 가상 문맥
5. `BacktestRunner(tape, bundle.analyses, initial, decision_policy, execution_policy,
   config, historical_input=bundle.document).run()`

P10 영구 저장소, 자동 정책 생성, 새 CLI는 추가하지 않는다.
PR-G Performance/Report와 `dsv backtest` 서비스는 이번 확장에서 제외되어 실데이터 성과
실행을 계속 차단한다. 기존 synthetic 입력 경로와 결과는 그대로 동작한다.

## 검증

fixture SQLite → HistoricalBacktestInput → FrozenAnalysis → 실제 Phase 3 판단 → Bridge →
예약 → 다음 session OPEN → Ledger → confirmed_full_exit를 검증한다.
분석 방향은 공개 SignalService boundary의 명시적 test double로 고정하며 production
안전장치·원장·체결 코드를 monkeypatch하지 않는다. 학습·전략 성과를 증명하는 테스트가 아니다.
금요일→월요일, BUY/SELL gap, 명시적 0/양수 slippage, fee/tax, 예약 부족, 다음 session/OPEN
부재, same-event/미래 가격 차단, 멱등성·ID 충돌, evidence 부재 및 policy hash 변경을 검사한다.
