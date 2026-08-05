import pandas as pd
import numpy as np

from xgboost import XGBClassifier


# ============================================================
# 설정
# ============================================================

BUY_THRESHOLD = 0.70

TOP_N = 5

MAX_HOLDING_DAYS = 20

INITIAL_CAPITAL = 10_000_000

TRADING_COST = 0.003

TEST_START = "2024-07-29"


# ============================================================
# Feature
# ============================================================

BUY_FEATURES = [
    "거래량비율",
    "MA20비율",
    "MA60비율",
    "MA120비율",
    "RSI",
    "HIGH20비율",
    "Volatility20",
    "Momentum20",
    "HIGH252비율",
    "MA20_MA60_Gap",
    "MA60_MA120_Gap",
    "BollingerPosition",
    "MACD",
    "MACDSignal",
    "MACDHistogram",
    "ATR",
    "OBV",
    "ADX",
    "MFI",
    "CMF",
    "CCI",
    "DonchianPosition",
    "VWAPRatio",
    "ATRRatio",
    "VolumeSpike",
    "LOW20비율",
    "Position60",
    "Position120",
]


# ============================================================
# 1. Dataset 불러오기
# ============================================================

print("=" * 70)
print("BUY Dataset 불러오는 중...")
print("=" * 70)

dataset = pd.read_csv(
    "dataset.csv",
    low_memory=False
)

dataset["날짜"] = pd.to_datetime(
    dataset["날짜"]
)

dataset["ticker"] = dataset["ticker"].astype(str)


print(
    f"전체 Dataset: {len(dataset):,}"
)


# ============================================================
# 2. 날짜 정렬
# ============================================================

dataset = dataset.sort_values(
    ["날짜", "ticker"]
).reset_index(drop=True)


# ============================================================
# 3. Train / Test 분리
# ============================================================

train_df = dataset[
    dataset["날짜"] < TEST_START
].copy()

test_df = dataset[
    dataset["날짜"] >= TEST_START
].copy()


print()
print("=" * 70)
print("Train / Test")
print("=" * 70)

print(
    f"Train: {len(train_df):,}"
)

print(
    f"Test : {len(test_df):,}"
)

print(
    f"Train 기간: "
    f"{train_df['날짜'].min().date()} ~ "
    f"{train_df['날짜'].max().date()}"
)

print(
    f"Test 기간 : "
    f"{test_df['날짜'].min().date()} ~ "
    f"{test_df['날짜'].max().date()}"
)


# ============================================================
# 4. BUY 모델
# ============================================================

X_train = train_df[BUY_FEATURES]

y_train = train_df["Target"]

X_test = test_df[BUY_FEATURES]


model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8,
    n_estimators=150,
    learning_rate=0.15,
    max_depth=8,
    colsample_bytree=1.0
)


print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)

model.fit(
    X_train,
    y_train
)


# ============================================================
# 5. BUY Probability
# ============================================================

print()
print("BUY Probability 계산 중...")

test_df["BUY_PROBABILITY"] = (
    model.predict_proba(X_test)[:, 1]
)


# ============================================================
# 6. BUY Signal
# ============================================================

test_df["BUY_SIGNAL"] = (
    test_df["BUY_PROBABILITY"]
    >= BUY_THRESHOLD
)


buy_signals = test_df[
    test_df["BUY_SIGNAL"]
].copy()


print()
print("=" * 70)
print("BUY Signal")
print("=" * 70)

print(
    f"BUY Threshold: {BUY_THRESHOLD:.2f}"
)

print(
    f"BUY Signal 수: {len(buy_signals):,}"
)


# ============================================================
# 7. Raw OHLC 데이터 준비
# ============================================================

print()
print("=" * 70)
print("Raw OHLC 데이터 불러오는 중...")
print("=" * 70)


raw_cache = {}


tickers = (
    buy_signals["ticker"]
    .drop_duplicates()
    .tolist()
)


for index, ticker in enumerate(tickers, start=1):

    print(
        f"[{index}/{len(tickers)}] "
        f"{ticker}"
    )

    try:

        raw = pd.read_csv(
            f"raw/{ticker}.csv",
            low_memory=False
        )

        raw["날짜"] = pd.to_datetime(
            raw["날짜"]
        )

        raw = raw.sort_values(
            "날짜"
        ).reset_index(drop=True)

        raw_cache[ticker] = raw

    except Exception as e:

        print(
            f"[{ticker}] Raw 데이터 실패: {e}"
        )


# ============================================================
# 8. 거래일 목록
# ============================================================

trading_dates = (
    dataset["날짜"]
    .drop_duplicates()
    .sort_values()
    .tolist()
)


test_trading_dates = [
    date
    for date in trading_dates
    if date >= pd.Timestamp(TEST_START)
]


print()
print(
    f"Test 거래일 수: "
    f"{len(test_trading_dates):,}"
)


# ============================================================
# 9. Raw 가격 조회 함수
# ============================================================

def get_price(
    ticker,
    date,
    column
):

    raw = raw_cache.get(ticker)

    if raw is None:
        return None

    rows = raw[
        raw["날짜"] == date
    ]

    if len(rows) == 0:
        return None

    value = rows.iloc[0][column]

    if pd.isna(value):
        return None

    return float(value)


# ============================================================
# 10. 다음 거래일 찾기
# ============================================================

def get_next_trading_date(
    current_date,
    days
):

    current_index = (
        test_trading_dates.index(
            current_date
        )
    )

    target_index = (
        current_index + days
    )

    if target_index >= len(
        test_trading_dates
    ):
        return None

    return test_trading_dates[
        target_index
    ]


# ============================================================
# 11. BUY Signal을 날짜별로 그룹화
# ============================================================

signals_by_date = {
    date: group
    for date, group in buy_signals.groupby(
        "날짜"
    )
}


# ============================================================
# 12. 백테스트
# ============================================================

print()
print("=" * 70)
print("BUY → 20일 보유 백테스트")
print("=" * 70)

capital = INITIAL_CAPITAL

trades = []


# ============================================================
# 13. 거래 실행
# ============================================================

for signal_date in test_trading_dates:

    candidates = signals_by_date.get(
        signal_date
    )

    if candidates is None:
        continue


    # --------------------------------------------------------
    # Probability 높은 순
    # --------------------------------------------------------

    candidates = candidates.sort_values(
        "BUY_PROBABILITY",
        ascending=False
    )


    candidates = candidates.head(
        TOP_N
    )


    # --------------------------------------------------------
    # 다음 거래일에 매수
    # --------------------------------------------------------

    buy_date = get_next_trading_date(
        signal_date,
        1
    )

    if buy_date is None:
        continue


    # --------------------------------------------------------
    # 최대 보유기간 후 매도
    # --------------------------------------------------------

    sell_date = get_next_trading_date(
        buy_date,
        MAX_HOLDING_DAYS
    )

    if sell_date is None:
        continue


    for _, signal in candidates.iterrows():

        ticker = signal["ticker"]

        name = signal["name"]


        # ----------------------------------------------------
        # 실제 매수가
        # ----------------------------------------------------

        buy_price = get_price(
            ticker,
            buy_date,
            "시가"
        )

        if buy_price is None:
            continue


        # ----------------------------------------------------
        # 실제 매도가
        # ----------------------------------------------------

        sell_price = get_price(
            ticker,
            sell_date,
            "시가"
        )

        if sell_price is None or sell_price <= 0:
            print(
                f"[SKIP] 비정상 매도가: "
                f"{sell_price}"
            )
            continue


        # ----------------------------------------------------
        # 수익률
        # ----------------------------------------------------

        if buy_price <= 0:
            print(
                f"[SKIP] 비정상 매수가: "
                f"{buy_price}"
            )
            continue

        gross_return = (
            (
                sell_price
                - buy_price
            )
            / buy_price
            * 100
        )


        # ----------------------------------------------------
        # 거래비용
        #
        # 매수 + 매도 전체 비용을 단순화하여
        # TRADING_COST로 반영
        # ----------------------------------------------------

        net_return = (
            gross_return
            - TRADING_COST * 100
        )


        # ----------------------------------------------------
        # 자본 업데이트
        # ----------------------------------------------------

        capital *= (
            1
            + net_return / 100
        )


        # ----------------------------------------------------
        # 거래 기록
        # ----------------------------------------------------

        trades.append({

            "SignalDate": signal_date,

            "BuyDate": buy_date,

            "SellDate": sell_date,

            "ticker": ticker,

            "name": name,

            "BUY_PROBABILITY":
                signal["BUY_PROBABILITY"],

            "BuyPrice":
                buy_price,

            "SellPrice":
                sell_price,

            "GrossReturn":
                gross_return,

            "NetReturn":
                net_return,

            "Capital":
                capital

        })


# ============================================================
# 14. 결과 DataFrame
# ============================================================

trades_df = pd.DataFrame(
    trades
)


if len(trades_df) == 0:

    print()
    print(
        "거래가 생성되지 않았습니다."
    )

    exit()


# ============================================================
# 15. 기본 통계
# ============================================================

average_return = (
    trades_df["NetReturn"]
    .mean()
)

win_rate = (
    trades_df["NetReturn"] > 0
).mean() * 100

total_trades = len(
    trades_df
)


# ============================================================
# 16. MDD
# ============================================================

capital_series = (
    trades_df["Capital"]
)

running_max = (
    capital_series
    .cummax()
)

drawdown = (
    capital_series
    / running_max
    - 1
)

max_drawdown = (
    drawdown.min()
    * 100
)


# ============================================================
# 17. 최종 수익률
# ============================================================

final_capital = (
    trades_df["Capital"]
    .iloc[-1]
)

total_return = (
    (
        final_capital
        / INITIAL_CAPITAL
    )
    - 1
) * 100


# ============================================================
# 18. 결과 출력
# ============================================================

print()
print("=" * 70)
print("BUY Dynamic Backtest 결과")
print("=" * 70)

print(
    f"BUY Threshold       : "
    f"{BUY_THRESHOLD:.2f}"
)

print(
    f"TOP N               : "
    f"{TOP_N}"
)

print(
    f"최대 보유기간       : "
    f"{MAX_HOLDING_DAYS} 거래일"
)

print(
    f"초기 자본           : "
    f"{INITIAL_CAPITAL:,.0f}원"
)

print(
    f"총 거래 수          : "
    f"{total_trades:,}"
)

print(
    f"평균 거래 수익률    : "
    f"{average_return:.2f}%"
)

print(
    f"승률                : "
    f"{win_rate:.2f}%"
)

print(
    f"MDD                 : "
    f"{max_drawdown:.2f}%"
)

print(
    f"최종 자본           : "
    f"{final_capital:,.0f}원"
)

print(
    f"총 수익률           : "
    f"{total_return:.2f}%"
)


# ============================================================
# 19. 거래 결과 저장
# ============================================================

trades_df.to_csv(
    "dynamic_sell_backtest_trades.csv",
    index=False
)


print()
print(
    "거래 기록 저장:"
)

print(
    "dynamic_sell_backtest_trades.csv"
)


# ============================================================
# 20. 최근 거래 확인
# ============================================================

print()
print("=" * 70)
print("최근 거래 10개")
print("=" * 70)

print(
    trades_df[
        [
            "SignalDate",
            "BuyDate",
            "SellDate",
            "ticker",
            "name",
            "BUY_PROBABILITY",
            "BuyPrice",
            "SellPrice",
            "NetReturn"
        ]
    ]
    .tail(10)
    .to_string(index=False)
)