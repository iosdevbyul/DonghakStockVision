import pandas as pd
import numpy as np
import xgboost as xgb

from pykrx import stock


# ============================================================
# CONFIG
# ============================================================

BUY_DATASET = "dataset.csv"
SELL_DATASET = "sell_dataset_v2.csv"

BUY_TARGET = "Target"
SELL_TARGET = "SELL_TARGET"

TRAIN_END = pd.Timestamp("2023-04-12")
TEST_START = pd.Timestamp("2023-04-13")

SELL_THRESHOLD = 0.50

BUY_THRESHOLDS = [
    0.70,
    0.75,
    0.80,
    0.85,
    0.90,
    0.95,
]

TOP_N_LIST = [
    1,
    3,
    5,
    10,
    20,
]

INITIAL_CAPITAL = 10_000_000

HOLD_DAYS = 20


# ============================================================
# BUY FEATURES
# ============================================================

BUY_FEATURE_COLUMNS = [
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
# SELL FEATURES
# ============================================================

SELL_FEATURE_COLUMNS = [
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


# ============================================================
# UTILS
# ============================================================

def normalize_ticker(df, ticker_col):

    df[ticker_col] = (
        df[ticker_col]
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.zfill(6)
    )

    return df


def find_ticker_column(df):

    if "ticker" in df.columns:
        return "ticker"

    if "종목코드" in df.columns:
        return "종목코드"

    raise ValueError(
        "종목 코드 컬럼을 찾을 수 없습니다."
    )


def validate_columns(df, columns, name):

    missing = [
        column
        for column in columns
        if column not in df.columns
    ]

    if missing:

        raise ValueError(
            f"{name}에 필요한 컬럼이 없습니다:\n"
            f"{missing}"
        )


# ============================================================
# LOAD BUY DATASET
# ============================================================

print("=" * 70)
print("BUY DATASET Loading")
print("=" * 70)


buy_df = pd.read_csv(
    BUY_DATASET,
    low_memory=False
)


buy_ticker_col = find_ticker_column(
    buy_df
)


buy_df["날짜"] = pd.to_datetime(
    buy_df["날짜"]
)


buy_df = normalize_ticker(
    buy_df,
    buy_ticker_col
)


validate_columns(
    buy_df,
    BUY_FEATURE_COLUMNS + [BUY_TARGET],
    "BUY dataset"
)


buy_df = (
    buy_df
    .sort_values(
        ["날짜", buy_ticker_col]
    )
    .reset_index(drop=True)
)


print(
    f"BUY Dataset : {len(buy_df):,}"
)


buy_train = buy_df[
    buy_df["날짜"] <= TRAIN_END
].copy()


buy_test = buy_df[
    buy_df["날짜"] >= TEST_START
].copy()


print(
    f"BUY Train   : {len(buy_train):,}"
)

print(
    f"BUY Test    : {len(buy_test):,}"
)

print(
    f"BUY Train 기간 : "
    f"{buy_train['날짜'].min().date()} ~ "
    f"{buy_train['날짜'].max().date()}"
)

print(
    f"BUY Test 기간  : "
    f"{buy_test['날짜'].min().date()} ~ "
    f"{buy_test['날짜'].max().date()}"
)


# ============================================================
# LOAD SELL DATASET
# ============================================================

print()
print("=" * 70)
print("SELL DATASET Loading")
print("=" * 70)


sell_df = pd.read_csv(
    SELL_DATASET,
    low_memory=False
)


sell_ticker_col = find_ticker_column(
    sell_df
)


sell_df["날짜"] = pd.to_datetime(
    sell_df["날짜"]
)


sell_df = normalize_ticker(
    sell_df,
    sell_ticker_col
)


validate_columns(
    sell_df,
    SELL_FEATURE_COLUMNS + [SELL_TARGET],
    "SELL dataset"
)


sell_df = (
    sell_df
    .sort_values(
        ["날짜", sell_ticker_col]
    )
    .reset_index(drop=True)
)


print(
    f"SELL Dataset : {len(sell_df):,}"
)


sell_train = sell_df[
    sell_df["날짜"] <= TRAIN_END
].copy()


sell_test = sell_df[
    sell_df["날짜"] >= TEST_START
].copy()


print(
    f"SELL Train   : {len(sell_train):,}"
)

print(
    f"SELL Test    : {len(sell_test):,}"
)

print(
    f"SELL Train 기간 : "
    f"{sell_train['날짜'].min().date()} ~ "
    f"{sell_train['날짜'].max().date()}"
)

print(
    f"SELL Test 기간  : "
    f"{sell_test['날짜'].min().date()} ~ "
    f"{sell_test['날짜'].max().date()}"
)


# ============================================================
# TRAIN BUY XGBOOST
# ============================================================

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)


buy_model = xgb.XGBClassifier(
    n_estimators=150,
    max_depth=8,
    learning_rate=0.15,
    objective="binary:logistic",
    eval_metric="logloss",
    tree_method="hist",
    random_state=42,
    n_jobs=-1,
)


buy_model.fit(
    buy_train[BUY_FEATURE_COLUMNS],
    buy_train[BUY_TARGET]
)


# ============================================================
# TRAIN SELL XGBOOST
# ============================================================

print()
print("=" * 70)
print("SELL XGBoost 학습")
print("=" * 70)


sell_model = xgb.XGBClassifier(
    n_estimators=150,
    max_depth=8,
    learning_rate=0.15,
    objective="binary:logistic",
    eval_metric="logloss",
    tree_method="hist",
    random_state=42,
    n_jobs=-1,
)


sell_model.fit(
    sell_train[SELL_FEATURE_COLUMNS],
    sell_train[SELL_TARGET]
)


# ============================================================
# BUY PROBABILITY
# ============================================================

print()
print("=" * 70)
print("BUY Probability 계산")
print("=" * 70)


buy_test["BUY_Probability"] = (
    buy_model
    .predict_proba(
        buy_test[BUY_FEATURE_COLUMNS]
    )[:, 1]
)


# ============================================================
# SELL PROBABILITY
# ============================================================

print()
print("=" * 70)
print("SELL Probability 계산")
print("=" * 70)


sell_test["SELL_Probability"] = (
    sell_model
    .predict_proba(
        sell_test[SELL_FEATURE_COLUMNS]
    )[:, 1]
)


sell_signal_df = sell_test[
    sell_test["SELL_Probability"] >= SELL_THRESHOLD
].copy()


print(
    f"SELL Threshold : "
    f"{SELL_THRESHOLD}"
)

print(
    f"SELL Signal 수 : "
    f"{len(sell_signal_df):,}"
)


# ============================================================
# BUY SIGNAL
# ============================================================

buy_signal_df = buy_test.copy()


# SELL signal lookup
sell_lookup = set(
    zip(
        sell_signal_df[sell_ticker_col],
        sell_signal_df["날짜"]
    )
)


# ============================================================
# REQUIRED TICKERS
# ============================================================

required_tickers = sorted(
    set(
        buy_signal_df[
            buy_signal_df["BUY_Probability"]
            >= min(BUY_THRESHOLDS)
        ][buy_ticker_col]
    )
)


print()
print("=" * 70)
print("필요 종목 계산")
print("=" * 70)


print(
    f"필요 종목 수 : "
    f"{len(required_tickers):,}"
)


# ============================================================
# PYKRX OHLCV PRELOAD
# ============================================================

print()
print("=" * 70)
print("pykrx OHLCV 사전 로딩")
print("=" * 70)


signal_dates = buy_signal_df[
    buy_signal_df["BUY_Probability"]
    >= min(BUY_THRESHOLDS)
]["날짜"]


if signal_dates.empty:

    raise ValueError(
        "BUY signal이 없습니다."
    )


signal_last_date = (
    signal_dates.max()
)


ohlcv_end_date = (
    signal_last_date
    + pd.Timedelta(days=60)
)


start_date = (
    TEST_START.strftime("%Y%m%d")
)


end_date = (
    ohlcv_end_date.strftime("%Y%m%d")
)


ohlcv_dict = {}


for count, ticker in enumerate(
    required_tickers,
    start=1
):

    try:

        ohlcv = stock.get_market_ohlcv(
            start_date,
            end_date,
            ticker
        )


        if ohlcv.empty:
            continue


        ohlcv.index = pd.to_datetime(
            ohlcv.index
        )


        for date, row in ohlcv.iterrows():

            open_price = row["시가"]
            high_price = row["고가"]
            low_price = row["저가"]
            close_price = row["종가"]


            if (
                pd.isna(open_price)
                or pd.isna(high_price)
                or pd.isna(low_price)
                or pd.isna(close_price)
            ):
                continue


            ohlcv_dict[
                (ticker, date)
            ] = {
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
            }


    except Exception as e:

        print(
            f"OHLCV 실패: "
            f"{ticker} / {e}"
        )


    if count % 100 == 0:

        print(
            f"OHLCV Loading: "
            f"{count} / "
            f"{len(required_tickers)}"
        )


print()
print(
    f"OHLCV 로딩 완료: "
    f"{len(ohlcv_dict):,} rows"
)


# ============================================================
# TICKER DATE CACHE
# ============================================================

ticker_dates_dict = {}


for ticker, date in ohlcv_dict.keys():

    if ticker not in ticker_dates_dict:

        ticker_dates_dict[ticker] = []

    ticker_dates_dict[ticker].append(
        date
    )


for ticker in ticker_dates_dict:

    ticker_dates_dict[ticker] = sorted(
        ticker_dates_dict[ticker]
    )


# ============================================================
# SIGNAL DATA
# ============================================================

signal_candidates = buy_signal_df[
    buy_signal_df["BUY_Probability"]
    >= min(BUY_THRESHOLDS)
].copy()


# ============================================================
# FORWARD RETURN
# ============================================================

print()
print("=" * 70)
print("BUY Signal Forward Return 계산")
print("=" * 70)


trade_records = []


for row in signal_candidates.itertuples():

    ticker = getattr(
        row,
        buy_ticker_col
    )

    signal_date = row.날짜

    probability = row.BUY_Probability


    current_data = ohlcv_dict.get(
        (ticker, signal_date)
    )


    if current_data is None:
        continue


    signal_close = (
        current_data["close"]
    )


    if signal_close <= 0:
        continue


    ticker_dates = ticker_dates_dict.get(
        ticker,
        []
    )


    if signal_date not in ticker_dates:
        continue


    signal_idx = ticker_dates.index(
        signal_date
    )


    future_idx = (
        signal_idx + HOLD_DAYS
    )


    if future_idx >= len(ticker_dates):
        continue


    future_date = ticker_dates[
        future_idx
    ]


    future_data = ohlcv_dict.get(
        (ticker, future_date)
    )


    if future_data is None:
        continue


    future_close = (
        future_data["close"]
    )


    forward_return = (
        future_close /
        signal_close
        - 1
    ) * 100


    trade_records.append({

        "ticker": ticker,

        "signal_date": signal_date,

        "BUY_Probability": probability,

        "signal_close": signal_close,

        "future_date": future_date,

        "future_close": future_close,

        "return": forward_return,

    })


trades_df = pd.DataFrame(
    trade_records
)


print(
    f"분석 가능 Signal : "
    f"{len(trades_df):,}"
)


# ============================================================
# PARAMETER SWEEP
# ============================================================

print()
print("=" * 70)
print("BUY THRESHOLD + TOP_N PARAMETER SWEEP")
print("=" * 70)


results = []


for threshold in BUY_THRESHOLDS:

    threshold_df = trades_df[
        trades_df["BUY_Probability"]
        >= threshold
    ].copy()


    if threshold_df.empty:
        continue


    threshold_df = (
        threshold_df
        .sort_values(
            [
                "signal_date",
                "BUY_Probability"
            ],
            ascending=[
                True,
                False
            ]
        )
    )


    for top_n in TOP_N_LIST:

        print(
            f"THRESHOLD={threshold:.2f} "
            f"TOP_N={top_n}"
        )


        selected_records = []


        for date, group in (
            threshold_df
            .groupby("signal_date")
        ):

            selected = group.head(
                top_n
            )

            selected_records.append(
                selected
            )


        if not selected_records:
            continue


        selected_df = pd.concat(
            selected_records,
            ignore_index=True
        )


        returns = selected_df[
            "return"
        ].values


        if len(returns) == 0:
            continue


        capital = (
            INITIAL_CAPITAL
        )


        equity_curve = [
            capital
        ]


        for return_pct in returns:

            capital *= (
                1 + return_pct / 100
            )

            equity_curve.append(
                capital
            )


        equity = np.array(
            equity_curve
        )


        running_max = np.maximum.accumulate(
            equity
        )


        drawdown = (
            equity /
            running_max
            - 1
        ) * 100


        mdd = drawdown.min()


        average_return = (
            selected_df["return"]
            .mean()
        )


        median_return = (
            selected_df["return"]
            .median()
        )


        win_rate = (
            selected_df["return"] > 0
        ).mean() * 100


        final_capital = capital


        total_return = (
            final_capital /
            INITIAL_CAPITAL
            - 1
        ) * 100


        results.append({

            "BUY_THRESHOLD": threshold,

            "TOP_N": top_n,

            "Trades": len(
                selected_df
            ),

            "AverageReturn":
                average_return,

            "MedianReturn":
                median_return,

            "WinRate":
                win_rate,

            "MDD":
                mdd,

            "FinalCapital":
                final_capital,

            "TotalReturn":
                total_return,

        })


# ============================================================
# RESULTS
# ============================================================

result_df = pd.DataFrame(
    results
)


print()
print("=" * 70)
print("BUY THRESHOLD + TOP_N 결과")
print("=" * 70)


if result_df.empty:

    print(
        "결과가 없습니다."
    )

    raise SystemExit


result_df = (
    result_df
    .sort_values(
        "TotalReturn",
        ascending=False
    )
)


print(
    result_df.to_string(
        index=False
    )
)


# ============================================================
# MDD FILTER
# ============================================================

for mdd_limit in [
    -30,
    -40,
    -50,
]:

    print()
    print("=" * 70)

    print(
        f"MDD {mdd_limit}% 이내 전략"
    )

    print("=" * 70)


    filtered = result_df[
        result_df["MDD"]
        >= mdd_limit
    ]


    if filtered.empty:

        print(
            "조건을 만족하는 전략이 없습니다."
        )

    else:

        print(
            filtered.to_string(
                index=False
            )
        )


# ============================================================
# BEST STRATEGY
# ============================================================

print()
print("=" * 70)
print("BEST STRATEGY")
print("=" * 70)


best_strategy = (
    result_df
    .iloc[0]
)


print(
    f"BUY Threshold : "
    f"{best_strategy['BUY_THRESHOLD']:.2f}"
)


print(
    f"TOP_N         : "
    f"{int(best_strategy['TOP_N'])}"
)


print(
    f"Trades        : "
    f"{int(best_strategy['Trades'])}"
)


print(
    f"Win Rate      : "
    f"{best_strategy['WinRate']:.2f}%"
)


print(
    f"Average Return: "
    f"{best_strategy['AverageReturn']:.4f}%"
)


print(
    f"Median Return : "
    f"{best_strategy['MedianReturn']:.4f}%"
)


print(
    f"MDD           : "
    f"{best_strategy['MDD']:.2f}%"
)


print(
    f"Final Capital : "
    f"{best_strategy['FinalCapital']:,.0f}"
)


print(
    f"Total Return  : "
    f"{best_strategy['TotalReturn']:.2f}%"
)


# ============================================================
# SAVE
# ============================================================

result_df.to_csv(
    "buy_threshold_topn_sweep.csv",
    index=False
)


print()
print("=" * 70)
print("저장 완료")
print("=" * 70)


print(
    "buy_threshold_topn_sweep.csv"
)