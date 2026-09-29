# Historical market input 연결

## 입력 생성 범위와 기본 실행 차단

`backtest.historical.build_historical_input`은 Phase 1의 실제 `MarketDataStore.read`
경계를 사용해 `HistoricalBacktestInput`을 만든다. 객체는 FrozenJSON, FrozenTape,
FrozenAnalysis로 구성되며 Runner가 실행 중 DB를 읽는 경로를 만들지 않는다.

historical execution 정책을 지정하지 않은 기본 Runner는 `real_runner_unsupported`, DecisionBridge는
`market_state_evidence_unavailable`로 실데이터를 차단한다. 기존 synthetic 정책은
`synthetic_explicit_full_fill`이다. Performance는 synthetic 전용이다.
입력 생성 API/CLI는 실행 정책을 선택하지 않으며 생성 직후에는 execution_status=blocked다.
추가된 [historical execution 정책](historical-execution.md)을 명시하면 Python Runner의
연구용 체결 경로를 사용할 수 있다. 입력 생성 CLI 자체는 정책을 승인하거나 실행하지 않는다.
이를 숨기거나 real을 synthetic으로 재표기하지 않는다. 기존 합성 Runner/Performance/
Report 경로는 유지한다. 새 정책은 가상 full-fill 가정과 bundle 증거를 함께 요구한다.
Performance 및 성과 CLI의 real provenance 확장은 별도 후속 작업이다.

## 발견한 원본 계약

- `storage.sqlite.SQLiteStore`: bars의 (ticker, trading_date)별 최신 payload만 저장.
  read(ticker, start, end)는 서울 거래일의 inclusive 범위에서 정렬된 DailyBar를 반환한다.
  원본 raw_pages와 수집 기록이 있지만 과거 시점별 정제 revision 조회 API는 없다.
- `data.schema.DailyBar`: OHLC, volume, **실제 trading_value 모두 signed-64-bit 정수**다.
  Float→Decimal 반올림이 필요 없다. tape에는 금액을 정확한 십진 문자열로 옮긴다.
- ticker는 숫자·대문자로 이루어진 6자리 KRX 코드. 선행 0을 유지하고 자동 종목 선택이나
  임의의 대소문자 변환은 하지 않는다. 중복은 제거하고 정렬한다.
- trading_date는 Asia/Seoul 달력 날짜이며 collected_at은 timezone-aware 실제 수집 시각.
  현재 계약은 수집일보다 이전 서울 거래일만 허용한다.

## 요청과 freeze

서비스에는 inclusive **date** start/end, 명시적 tickers, aware captured_at, quality,
RunManifest template, ExecutionPolicy, availability_assumption이 필요하다.
start>end, 빈 universe, 잘못된 ticker, naive captured_at을 거부한다.
초기 현금·기존 포지션 및 모든 정책/버전은 기존 manifest/policy로 명시하며 기본값이 없다.

각 ticker의 요청 구간을 한 번씩 읽고 메모리 복사 후 기존 `capture_market`으로 검증한다.
(거래일, ticker) 순서로 sequence를 할당한다. 빈 ticker와 분석 불가 지점은 별도 기록한다.
거래일 목록에 없는 날을 생성하거나 가격을 보간하지 않는다. quality manifest는 호출자가
제공해야 하며, 조정 방법이 확인되지 않은 adjusted 데이터나 출처 혼합은 기존 검증이 거부한다.

시장 공개 read API에 다종목 단일 transaction snapshot 기능은 없다. 따라서 동시 DB 갱신
상황의 cross-ticker 원자성을 주장하지 않는다. 일관된 전체 DB snapshot이 필요하면 호출자가
안정된 읽기 전용 복사본을 제공해야 한다. 동결 완료 후에는 DB 수정·삭제가 bundle에 영향 없다.

기존 RunManifest는 market/models/analyses 참조를 비어 있게 만들 수 없다.
분석을 만들 수 없는 경우 caller template의 선언 참조는 유지하되 **실제 captured analyses는
비어 있고 decision point는 blocked**다. 선언 참조만으로 artifact 존재·가용성을 증명하지 않는다.
자동 placeholder 모델/분석을 생성하지 않는다.

## Daily availability 연구 가정

호출자가 `after_trading_date_seoul_midnight`를 명시해야 한다.
거래일 D의 전체 OHLCV와 거래대금은 **D+1일 00:00 Asia/Seoul**부터 공개한다.
이는 실제 거래소 발표 시각이나 PIT 증거가 아니라, 같은 bar 이전 판단에 high/low/close가
노출되지 않게 하는 보수적인 연구용 공개 가정이다. 휴일/주말에 가짜 다음 거래일을 만들지 않는다.

manifest 시간 구간은 start일 00:00 Seoul부터 end+1일 00:00 Seoul까지다.
MarketEvent의 event_at/available_at은 해당 bar의 연구 공개 경계이며 장 마감 체결 시각이 아니다.
원래 trading_date 및 실제 collected_at/source_received_at을 별도로 그대로 보존한다.
quality_available_at도 이 가정에 따른 연구 공개 경계다. 실제 quality verified_at은
bundle의 quality 원본에 남는다. 품질 검토가 과거에 이뤄졌다고 주장하지 않는다.
listing/halt 상태는 unknown, session은 closed이며 이를 거래 가능 증거로 쓰지 않는다.
무거래 일봉의 0 값도 생성·수정하지 않는다.

## Phase 2 공개 분석 API

`SignalService.research(model_id, snapshot_id, tickers, anchor_date)`를 사용한다.
private inference, 새 학습, 인위적 점수/BUY/HOLD 생성은 없다.
이 API는 모델의 **학습 snapshot과 같은 snapshot**에서만 추론한다.
따라서 지정 기간의 market rows와 그 snapshot rows가 정확히 일치해야 한다.
다르면 `model_snapshot_market_mismatch`로 전 지점을 차단한다.
lookback은 해당 immutable model snapshot의 과거 행만 사용하며 future bar로 채우지 않는다.
기간 밖 lookback은 tape에 시장 사건으로 추가하지 않고 분석 snapshot 증거에 보존한다.

분석 anchor는 기존 Phase 2의 D일 16:00 Seoul이다. 실제 생성 시각과 별개다.
공개는 bar와 동일한 D+1일 경계로 제한한다. 실제 생성 시각, 모델/분석/snapshot/dataset ID와
원본은 `FrozenAnalysis.capture`로 보존한다. 모델 생성·학습이 요청 기간 이후일 수 있으므로
`model_training_cutoff_unverified`, `not_oos_evidence`를 항상 남긴다.
insufficient_history, missing_model, snapshot 불일치 등은 명시적 blocked 사유다.
분석 artifact를 생성해도 scored/유효한 매매 신호임을 보장하지 않는다.

AnalysisStore의 공개 put은 최초 artifact와 실행 감사 이력을 저장한다.
반복 builder 실행은 경제 입력을 바꾸지 않지만 분석 DB의 실행 감사 행은 추가될 수 있다.
시장 DB 값은 변경하지 않는다.

## 결정성과 identity

정렬된 ticker/사건, 내용 기반 source revision/hash, 고정 요청·품질·정책을 사용한다.
시스템 현재 시각을 builder가 생성하지 않는다. SignalService 반환의 analyzed_at은
경제 입력으로 사용하지 않는다. 원본 artifact의 실제 created_at은 보존한다.
`HistoricalBacktestInput.identifier`는 새 signal의 실제 생성 감사 시각만 제외한 semantic hash다.
`document.identifier`는 감사 시각까지 포함한 전체 무결성 hash다. 모델·snapshot·dataset의
기존 생성 시각은 provenance로 고정된다. 모델 ID가 다른 snapshot을 임의 대체하지 않는다.
PIT 모드는 허용하지 않으며 감사 시각 제외가 PIT 가용성 승인을 뜻하지 않는다.

## CLI

기존 `dsv backtest`의 synthetic frozen-input 실행은 그대로 유지한다.
새 **입력 생성 전용** 명령:

```bash
dsv backtest-input --market-db market.sqlite3 \
  --analysis-db analysis.sqlite3 --model-id MODEL_CONTENT_ID \
  --start 2025-01-01 --end 2025-06-30 --tickers 005930 000660 \
  --config historical-config.json --output historical-input.json
```

config 필수 키는 `manifest`, `policy`, `quality`, `captured_at`, `availability_assumption`이다.
manifest/policy는 기존 PR-A JSON 계약이며 initial_account에 초기 현금을 명시한다.
실제 DB 경로와 모델을 자동으로 찾거나 만들지 않는다. analysis-db/model-id 둘 다 생략하면
시장 데이터만 동결하고 모든 분석 지점을 unavailable로 표시한다.
출력 파일은 새 파일만 허용해 사용자 파일을 덮어쓰지 않는다.
완료 시 mode, semantic input_hash, limitations, execution_status=blocked를 출력한다.
이 파일을 `dsv backtest --input ...`으로 실행하면 `real_runner_unsupported`로 거부한다.

## 해석·테스트·후속 사항

latest-only, revision 부재, PIT 미검증, 모델 cutoff 미검증, 시장 상태/유동성 미검증,
비원자적 다종목 capture, 동시 reservation 미지원 제한을 bundle에 남긴다.
**historical_research는 과거 당시에 알 수 있었던 PIT/OOS 성과라는 뜻이 아니다.**

오프라인 fixture SQLite에서 다종목 범위·중복·동결·공개 경계·결측·모델 부재 및 실제
SignalService의 짧은 lookback 차단을 검증한다. fixture provider 이름은 저장 구조를 시험하기
위한 것이며 실제 시장 데이터나 성과의 증거가 아니다. 합성 점수를 실데이터 경로에 넣어
실제 시장 성과로 표시하지 않는다. historical execution 테스트는 명시적 analysis test double을
사용해 체결·원장까지 연결한다. 성과 E2E는 아직 지원하지 않으며 기존 합성 회귀는 유지한다.

기존 capture_market 계약에 따라 한 tape의 provider/market/adjustment 조합은 하나여야 한다.
여러 ticker 입력은 지원하지만 서로 다른 가격 기준·시장 프로필을 섞는 확장은 포함하지 않는다.
