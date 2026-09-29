# KRX 날짜별 전체시장 수집

## 사용자 명령과 호환성

```sh
.venv/bin/dsv --db .data/market.sqlite3 collect --all \
  --start 2022-01-01 --end 2025-06-30 --provider krx --market KOSPI
```

환경의 기존 KRX 인증키·시장 API 승인을 사용한다. 키를 명령행 인수로 받거나 출력하지 않는다.
이 변경 작업에서는 실제 API를 호출하지 않으며 시장/분석 DB도 변경하지 않는다.
위 명령은 사용자가 실제 전체시장 수집을 시작할 때 실행한다.

- `collect`는 `--all` 또는 `--tickers ...` 중 정확히 하나를 요구한다.
- `--all --provider fake`는 DB 생성 전에 설정 오류로 거부한다.
- 기존 `collect --tickers`, `update --tickers`, `query --tickers`는 그대로다.
- `update --all`을 임의로 정의하지 않는다. 날짜별 재실행은 명시적 collect 범위로 한다.
- 시장은 KOSPI/KOSDAQ/KONEX 중 하나이며, 요청별로 해당 KRX endpoint를 사용한다.

## 요청 수와 provider 경계

`KRXProvider.fetch_market(day) -> RawPage`와 `parse_market(page) -> list[DailyBar]`를 추가했다.
기존 `fetch(ticker,start,end)`와 `parse(page,ticker)`의 서명·선택 의미는 유지한다.
날짜별 HTTP 코드와 필드 파싱은 두 경로가 공유한다. `MarketDataProvider` protocol에
새 메서드를 강제로 추가하지 않아 기존 provider 구현도 유지한다.

`KRXMarketPipeline`의 반복 단위는 ticker가 아닌 날짜다:

1. 평일 날짜 하나를 선택한다.
2. `fetch_market(day)`를 한 번 호출한다.
3. 응답을 한 번 archive한다.
4. `OutBlock_1`의 모든 행을 파싱·검증한다.
5. 종목별 중복을 정규화하고 날짜 전체 봉을 한 번 save한다.

종목 루프 내부에는 HTTP 호출이 없다. 신규 provider의 정상 응답 수집에서는
HTTP 횟수가 요청 평일 수와 같다. 주말은 기존과 같이 건너뛰고, 평일 휴장은 빈 응답으로
기록한다. 임의 거래일 캘린더나 가격을 생성하지 않는다.

동일 provider 인스턴스의 최근 128일 캐시를 재사용할 수 있다. 네트워크/429/일시적 5xx는
기존 RetryingHTTP의 최대 3회 **시도**를 유지하므로 장애 시 실제 HTTP 횟수는 날짜 수보다
많을 수 있다. 재시도도 RequestGate의 간격·일일 한도에 포함된다. 종목 수로는 증가하지 않는다.
캐시 크기보다 긴 191일 범위로 날짜당 1회 요청 회귀 검증을 한다.

## 검증과 실패 경계

- `OutBlock_1`은 배열이어야 한다. 각 행은 객체이며 ISU_CD는 기존 ticker 계약을 만족한다.
- BAS_DD가 요청 RawPage.source_date와 다르거나 MKT_NM이 요청 시장과 다르면 거부한다.
- OHLC·거래량·실제 거래대금은 기존 정수 파싱과 DailyBar 검증을 재사용한다.
- 결측·소수·NaN·음수·불가능한 OHLC를 임의로 0 또는 다른 가격으로 대체하지 않는다.
- 같은 종목/날짜의 동일 중복은 하나로 정규화하고, 서로 다른 중복은 날짜 전체를 거부한다.
- 한 행이라도 잘못되면 해당 날짜 정제 데이터를 하나도 저장하지 않는다. 원본은 감사용으로
  남기며, 날짜 실패를 기록하고 다음 날짜를 진행한다.
- DB save 중 오류도 해당 날짜 전체를 rollback한다. 앞서 성공한 날짜와 기존 데이터는 보존한다.
- 수집 대상 종목 이동 등으로 기존 provider/market/adjustment와 충돌하는 경우 기존 저장소의
  거부 정책을 그대로 적용한다. 기존 메타데이터를 강제 교체하지 않는다.

## 원본 저장 및 멱등성

SQLite 스키마와 MarketDataStore protocol은 변경하지 않는다. 기존 `archive`, `save`,
`record_run`을 사용한다. 시장 전체 원본·감사의 기존 ticker 칼럼에는 예약된 범위 이름
`@krx_market:KOSPI` 등을 저장한다. 이는 실제 ticker나 범용 wildcard가 아니다.

`save`의 원본 연결은 기존 특정 ticker 일치 또는 아래 **모든** 조건을 요구한다:

- raw와 bar의 provider가 동일한 `krx`
- raw 범위가 해당 bar.market과 정확히 일치
- raw source_date가 해당 bar.trading_date와 정확히 일치(None 허용 안 함)

한 raw page를 그 날짜의 모든 변경 봉이 참조하므로 큰 응답을 종목마다 중복 저장하지 않는다.
기존 ticker 원본과 함께 사용할 수 있으며 별도 migration이나 DB 초기화가 필요 없다.

`(ticker,trading_date)` primary key, `old.content()==new.content()`일 때 변경 0 및 최초
collected_at 보존, 새로운 수집 시각의 정정만 upsert, stale/같은 시각의 다른 값 거부 정책은
그대로다. 재수집 감사 raw는 추가될 수 있지만 정제 봉이 중복되지는 않는다.

## 요약과 보안

전체시장 JSON에는 `rows`가 추가된다. 이 모드에서 `succeeded`, `failed`, `empty`는 시장
**날짜** 수이며 `empty`는 succeeded에 포함된다. rows는 정상 처리한 중복 제거 봉 수로
기존 동일 값도 포함한다. changed는 실제 삽입·정정 수다. 기존 ticker 결과는 변경하지 않는다.

오류/로그에는 날짜·시장·안전한 오류 종류만 기록한다. 파서 예외 체인에서도 서버 필드값이
노출되지 않게 한다. 키는 AUTH_KEY 헤더에만 전달하며 원본 응답은 로컬 raw 저장소에만
보관한다. 테스트는 임시 DB와 MockTransport를 사용하고 실제 네트워크 연결은 차단한다.

전체시장 자료 확보가 Phase 2의 최소량·기업행사·거래일 검증을 자동 통과시키지는 않는다.
이 모드는 학습·전략·백테스트 계약을 변경하지 않는다.
