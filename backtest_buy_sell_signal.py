import pandas as pd
import numpy as np
from xgboost import XGBClassifier

BUY_THRESHOLD = 0.70
SELL_THRESHOLD = 0.40

TOP_N = 5
MAX_HOLD_DAYS = 20

INITIAL_CAPITAL = 10_000_000
TRADING_COST = 0.003

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

SELL_FEATURES = [
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
    "Return3",
    "Return5",
    "Return10",
    "Drawdown5",
    "Drawdown10",
    "Drawdown20",
    "MA20Slope5",
    "MA60Slope5",
    "MA120Slope5",
    "RSIChange3",
    "RSIChange5",
    "VolumeRatio5",
    "VolumeRatio10",
    "PriceVolumeDown",
]

print("Dataset Loading...")

buy_df = pd.read_csv(
    "dataset.csv",
    low_memory=False,
)

sell_df = pd.read_csv(
    "sell_dataset_v2.csv",
    low_memory=False,
)

buy_df["날짜"] = pd.to_datetime(buy_df["날짜"])
sell_df["날짜"] = pd.to_datetime(sell_df["날짜"])

buy_df["ticker"] = buy_df["ticker"].astype(str).str.zfill(6)
sell_df["ticker"] = sell_df["ticker"].astype(str).str.zfill(6)

print(f"BUY Dataset : {len(buy_df):,}")
print(f"SELL Dataset: {len(sell_df):,}")

print()
print("=" * 70)
print("BUY Train / Test")
print("=" * 70)

buy_df = buy_df.sort_values(
    ["날짜", "ticker"]
).reset_index(drop=True)

unique_dates = sorted(
    buy_df["날짜"].drop_duplicates()
)

split_date_index = int(
    len(unique_dates) * 0.80
)

split_date = unique_dates[split_date_index]

buy_train = buy_df[
    buy_df["날짜"] < split_date
].copy()

buy_test = buy_df[
    buy_df["날짜"] >= split_date
].copy()

print(f"Train: {len(buy_train):,}")
print(f"Test : {len(buy_test):,}")

print(
    f"Train 기간: "
    f"{buy_train['날짜'].min().date()} ~ "
    f"{buy_train['날짜'].max().date()}"
)

print(
    f"Test 기간 : "
    f"{buy_test['날짜'].min().date()} ~ "
    f"{buy_test['날짜'].max().date()}"
)

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)

X_buy_train = buy_train[BUY_FEATURES]
y_buy_train = buy_train["Target"]

X_buy_test = buy_test[BUY_FEATURES]

buy_model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8,
    n_estimators=150,
    learning_rate=0.15,
    max_depth=8,
    colsample_bytree=1.0,
)

buy_model.fit(
    X_buy_train,
    y_buy_train,
)

print()
print("BUY Probability 계산 중...")

buy_test["BUY_PROBABILITY"] = (
    buy_model
    .predict_proba(X_buy_test)[:, 1]
)

print()
print("=" * 70)
print("SELL XGBoost v2 모델 Loading")
print("=" * 70)

sell_model = XGBClassifier()

sell_model.load_model(
    "sell_xgboost_v2.json"
)

model_features = list(
    sell_model.get_booster().feature_names
)

if model_features != SELL_FEATURES:
    raise ValueError(
        "SELL_FEATURES 순서가 모델과 일치하지 않습니다."
    )

print(
    f"SELL Dataset: {len(sell_df):,}"
)

print(
    f"SELL 모델 Feature 수: "
    f"{len(model_features)}"
)

test_start = buy_test["날짜"].min()
test_end = buy_test["날짜"].max()

sell_test = sell_df[
    (sell_df["날짜"] >= test_start)
    & (sell_df["날짜"] <= test_end)
].copy()

sell_test = sell_test.sort_values(
    ["날짜", "ticker"]
).reset_index(drop=True)

print(
    f"SELL Test 데이터: "
    f"{len(sell_test):,}"
)

print()
print("SELL Probability 계산 중...")

sell_test["SELL_PROBABILITY"] = (
    sell_model
    .predict_proba(
        sell_test[SELL_FEATURES]
    )[:, 1]
)

print()
print("=" * 70)
print("Signal Index 생성")
print("=" * 70)

buy_signal_map = {}

buy_candidates = buy_test[
    buy_test["BUY_PROBABILITY"] >= BUY_THRESHOLD
].copy()

for row in buy_candidates.itertuples(index=False):

    key = (
        row.ticker,
        row.날짜,
    )

    existing = buy_signal_map.get(key)

    if existing is None:
        buy_signal_map[key] = row
    elif row.BUY_PROBABILITY > existing.BUY_PROBABILITY:
        buy_signal_map[key] = row

sell_signal_map = {}

for row in sell_test.itertuples(index=False):

    key = (
        row.ticker,
        row.날짜,
    )

    sell_signal_map[key] = row.SELL_PROBABILITY

print(
    f"BUY Signal Index: "
    f"{len(buy_signal_map):,}"
)

print(
    f"SELL Signal Index: "
    f"{len(sell_signal_map):,}"
)

print()
print("=" * 70)
print("Trading Date")
print("=" * 70)

trading_dates = sorted(
    buy_test["날짜"].drop_duplicates()
)

trading_date_index = {
    date: i
    for i, date in enumerate(trading_dates)
}

print(
    f"Test 거래일 수: "
    f"{len(trading_dates)}"
)

def get_next_trading_date(date):

    index = trading_date_index.get(date)

    if index is None:
        return None

    next_index = index + 1

    if next_index >= len(trading_dates):
        return None

    return trading_dates[next_index]

def get_future_trading_date(
    date,
    days,
):

    index = trading_date_index.get(date)

    if index is None:
        return None

    target_index = index + days

    if target_index >= len(trading_dates):
        return None

    return trading_dates[target_index]

print()
print("=" * 70)
print("Raw OHLCV Loading")
print("=" * 70)

raw_cache = {}

def load_raw(ticker):

    ticker = str(ticker).zfill(6)

    if ticker in raw_cache:
        return raw_cache[ticker]

    path = f"raw/{ticker}.csv"

    try:

        df = pd.read_csv(
            path,
            low_memory=False,
        )

    except FileNotFoundError:

        raw_cache[ticker] = None

        return None

    df["날짜"] = pd.to_datetime(
        df["날짜"]
    )

    df = (
        df
        .sort_values("날짜")
        .drop_duplicates("날짜")
        .set_index("날짜")
    )

    raw_cache[ticker] = df

    return df

def get_price(
    ticker,
    date,
    column,
):

    df = load_raw(ticker)

    if df is None:
        return None

    if date not in df.index:
        return None

    value = df.loc[date, column]

    if isinstance(value, pd.Series):
        value = value.iloc[0]

    if pd.isna(value):
        return None

    value = float(value)

    if value <= 0:
        return None

    return value

print()
print("=" * 70)
print("BUY Signal")
print("=" * 70)

print(
    f"BUY Threshold : "
    f"{BUY_THRESHOLD:.2f}"
)

print(
    f"BUY Signal 수 : "
    f"{len(buy_candidates):,}"
)

print()
print("=" * 70)
print("BUY + SELL Portfolio Backtest")
print("=" * 70)

print(
    f"BUY Threshold       : {BUY_THRESHOLD:.2f}"
)

print(
    f"SELL Threshold      : {SELL_THRESHOLD:.2f}"
)

print(
    f"TOP N               : {TOP_N}"
)

print(
    f"MAX HOLD DAYS       : {MAX_HOLD_DAYS}"
)

print(
    "매수 체결           : BUY Signal 다음 거래일 시가"
)

print(
    "매도 체결           : SELL Signal 다음 거래일 시가"
)

print(
    "TEST 마지막 날      : 마지막 테스트 거래일 종가"
)

print(
    f"초기 자본           : {INITIAL_CAPITAL:,.0f}원"
)

trades = []

open_positions = []

cash = float(INITIAL_CAPITAL)

equity_history = []

for signal_date in trading_dates:

    current_index = trading_date_index[
        signal_date
    ]

    current_date = signal_date

    remaining_positions = []

    for position in open_positions:

        ticker = position["ticker"]

        sell_signal = sell_signal_map.get(
            (
                ticker,
                current_date,
            )
        )

        should_sell = (
            sell_signal is not None
            and sell_signal >= SELL_THRESHOLD
        )

        hold_days = (
            current_index
            - position["buy_index"]
        )

        should_force_sell = (
            hold_days >= MAX_HOLD_DAYS
        )

        is_last_test_day = (
            current_date == test_end
        )

        if (
            should_sell
            or should_force_sell
            or is_last_test_day
        ):

            if should_sell:

                sell_date = (
                    get_next_trading_date(
                        current_date
                    )
                )

                sell_reason = "SELL_SIGNAL"

            elif should_force_sell:

                sell_date = (
                    get_next_trading_date(
                        current_date
                    )
                )

                sell_reason = "MAX_HOLD"

            else:

                sell_date = current_date

                sell_reason = "TEST_END"

            if sell_date is None:
                sell_date = test_end
                sell_reason = "TEST_END"

            if sell_date > test_end:
                sell_date = test_end
                sell_reason = "TEST_END"

            if sell_reason == "TEST_END":

                sell_price = get_price(
                    ticker,
                    test_end,
                    "종가",
                )

            else:

                sell_price = get_price(
                    ticker,
                    sell_date,
                    "시가",
                )

            if sell_price is None:
                sell_price = get_price(
                    ticker,
                    test_end,
                    "종가",
                )

                sell_date = test_end
                sell_reason = "TEST_END"

            if sell_price is None:
                remaining_positions.append(
                    position
                )
                continue

            sell_value = (
                position["shares"]
                * sell_price
            )

            sell_value *= (
                1 - TRADING_COST
            )

            cash += sell_value

            net_return = (
                sell_value
                / position["invested"]
                - 1
            ) * 100

            trades.append(
                {
                    "SignalDate": position[
                        "SignalDate"
                    ],
                    "BuyDate": position[
                        "BuyDate"
                    ],
                    "SellSignalDate": (
                        current_date
                        if should_sell
                        else pd.NaT
                    ),
                    "SellDate": sell_date,
                    "ticker": ticker,
                    "name": position["name"],
                    "BUY_PROBABILITY": position[
                        "BUY_PROBABILITY"
                    ],
                    "SELL_PROBABILITY": (
                        sell_signal
                        if should_sell
                        else np.nan
                    ),
                    "BuyPrice": position[
                        "BuyPrice"
                    ],
                    "SellPrice": sell_price,
                    "NetReturn": net_return,
                    "SellReason": sell_reason,
                    "HoldDays": hold_days,
                }
            )

        else:

            remaining_positions.append(
                position
            )

    open_positions = remaining_positions

    if current_date == test_end:
        break

    next_buy_date = get_next_trading_date(
        current_date
    )

    if next_buy_date is None:
        continue

    available_slots = (
        TOP_N
        - len(open_positions)
    )

    if available_slots <= 0:
        continue

    daily_candidates = buy_candidates[
        buy_candidates["날짜"] == current_date
    ]

    if daily_candidates.empty:
        continue

    existing_tickers = {
        position["ticker"]
        for position in open_positions
    }

    daily_candidates = (
        daily_candidates[
            ~daily_candidates["ticker"].isin(
                existing_tickers
            )
        ]
        .sort_values(
            "BUY_PROBABILITY",
            ascending=False,
        )
        .head(available_slots)
    )

    if daily_candidates.empty:
        continue

    position_count = len(
        daily_candidates
    )

    allocation = (
        cash
        / position_count
    )

    for row in daily_candidates.itertuples():

        ticker = row.ticker

        if ticker in existing_tickers:
            continue

        buy_price = get_price(
            ticker,
            next_buy_date,
            "시가",
        )

        if buy_price is None:
            continue

        if cash <= 0:
            break

        investment = min(
            allocation,
            cash,
        )

        investment_after_cost = (
            investment
            * (1 - TRADING_COST)
        )

        shares = (
            investment_after_cost
            / buy_price
        )

        if shares <= 0:
            continue

        cash -= investment

        buy_index = trading_date_index.get(
            next_buy_date
        )

        if buy_index is None:
            continue

        open_positions.append(
            {
                "SignalDate": current_date,
                "BuyDate": next_buy_date,
                "ticker": ticker,
                "name": getattr(
                    row,
                    "name",
                    ticker,
                ),
                "BUY_PROBABILITY": row.BUY_PROBABILITY,
                "BuyPrice": buy_price,
                "shares": shares,
                "invested": investment,
                "buy_index": buy_index,
            }
        )

        existing_tickers.add(ticker)

    position_value = 0.0

    for position in open_positions:

        price = get_price(
            position["ticker"],
            current_date,
            "종가",
        )

        if price is None:
            price = position["BuyPrice"]

        position_value += (
            position["shares"]
            * price
        )

    equity = (
        cash
        + position_value
    )

    equity_history.append(
        {
            "Date": current_date,
            "Cash": cash,
            "PositionValue": position_value,
            "Equity": equity,
        }
    )

trades_df = pd.DataFrame(trades)

print()
print("=" * 70)
print("BUY + SELL Signal Backtest 결과")
print("=" * 70)

if trades_df.empty:

    print("거래가 없습니다.")

else:

    average_return = (
        trades_df["NetReturn"].mean()
    )

    median_return = (
        trades_df["NetReturn"].median()
    )

    win_rate = (
        trades_df["NetReturn"] > 0
    ).mean() * 100

    equity_df = pd.DataFrame(
        equity_history
    )

    if not equity_df.empty:

        running_max = (
            equity_df["Equity"]
            .cummax()
        )

        drawdown = (
            equity_df["Equity"]
            / running_max
            - 1
        )

        mdd = (
            drawdown.min()
            * 100
        )

        final_equity = float(
            equity_df["Equity"].iloc[-1]
        )

    else:

        mdd = 0.0

        final_equity = (
            INITIAL_CAPITAL
        )

    total_return = (
        (
            final_equity
            / INITIAL_CAPITAL
        )
        - 1
    ) * 100

    print(
        f"총 거래 수          : "
        f"{len(trades_df):,}"
    )

    print(
        f"평균 거래 수익률    : "
        f"{average_return:.2f}%"
    )

    print(
        f"중앙값 거래 수익률  : "
        f"{median_return:.2f}%"
    )

    print(
        f"승률                : "
        f"{win_rate:.2f}%"
    )

    print(
        f"MDD                 : "
        f"{mdd:.2f}%"
    )

    print(
        f"최종 자본           : "
        f"{final_equity:,.0f}원"
    )

    print(
        f"총 수익률           : "
        f"{total_return:.2f}%"
    )

    time_errors = (
        trades_df["SellDate"]
        < trades_df["BuyDate"]
    ).sum()

    print(
        f"시간 순서 오류 거래 : "
        f"{time_errors:,}"
    )

    trades_df.to_csv(
        "buy_sell_signal_backtest_trades.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        "거래 기록 저장:"
    )

    print(
        "buy_sell_signal_backtest_trades.csv"
    )

    print()
    print("=" * 70)
    print("매도 사유")
    print("=" * 70)

    print(
        trades_df["SellReason"]
        .value_counts()
        .to_string()
    )

    print()
    print("=" * 70)
    print("최근 거래 10개")
    print("=" * 70)

    print(
        trades_df
        .tail(10)
        .to_string(index=False)
    )