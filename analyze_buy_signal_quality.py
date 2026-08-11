import pandas as pd
import numpy as np
import xgboost as xgb
from pykrx import stock
from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

BUY_DATASET_PATH = "dataset.csv"

TRAIN_END = pd.Timestamp("2023-04-12")
TEST_START = pd.Timestamp("2023-04-13")

BUY_THRESHOLD_LIST = [
    0.70,
    0.75,
    0.80,
    0.85,
    0.90,
    0.95,
]

FORWARD_DAYS = [
    3,
    5,
    10,
    20,
]

MIN_DATA_DAYS = 30


# ============================================================
# FEATURES
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
        c for c in columns
        if c not in df.columns
    ]

    if missing:

        raise ValueError(
            f"{name}에 필요한 컬럼이 없습니다:\n{missing}"
        )


# ============================================================
# LOAD DATASET
# ============================================================

print("=" * 70)
print("BUY SIGNAL QUALITY ANALYSIS")
print("=" * 70)

print()
print("Dataset Loading...")


df = pd.read_csv(
    BUY_DATASET_PATH,
    low_memory=False
)


ticker_col = find_ticker_column(df)


df["날짜"] = pd.to_datetime(
    df["날짜"]
)


df = normalize_ticker(
    df,
    ticker_col
)


validate_columns(
    df,
    BUY_FEATURE_COLUMNS + ["Target"],
    "BUY dataset"
)


df = (
    df
    .sort_values(
        ["날짜", ticker_col]
    )
    .reset_index(drop=True)
)


print(
    f"전체 데이터 : {len(df):,}"
)


# ============================================================
# TRAIN / TEST
# ============================================================

train_df = df[
    df["날짜"] <= TRAIN_END
].copy()


test_df = df[
    df["날짜"] >= TEST_START
].copy()


print()
print(
    f"Train : {len(train_df):,}"
)

print(
    f"Test  : {len(test_df):,}"
)

print(
    f"Train 기간 : "
    f"{train_df['날짜'].min().date()} ~ "
    f"{train_df['날짜'].max().date()}"
)

print(
    f"Test 기간 : "
    f"{test_df['날짜'].min().date()} ~ "
    f"{test_df['날짜'].max().date()}"
)


# ============================================================
# XGBOOST
# ============================================================

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)


model = xgb.XGBClassifier(
    n_estimators=150,
    max_depth=8,
    learning_rate=0.15,
    objective="binary:logistic",
    eval_metric="logloss",
    tree_method="hist",
    random_state=42,
    n_jobs=-1,
)


model.fit(
    train_df[BUY_FEATURE_COLUMNS],
    train_df["Target"]
)


# ============================================================
# PROBABILITY
# ============================================================

print()
print("=" * 70)
print("BUY Probability 계산")
print("=" * 70)


test_df["Probability"] = (
    model
    .predict_proba(
        test_df[BUY_FEATURE_COLUMNS]
    )[:, 1]
)


print(
    test_df["Probability"]
    .describe()
)


# ============================================================
# SIGNAL SUMMARY
# ============================================================

print()
print("=" * 70)
print("BUY SIGNAL 분포")
print("=" * 70)


for threshold in BUY_THRESHOLD_LIST:

    count = (
        test_df["Probability"] >= threshold
    ).sum()

    print(
        f"{threshold:.2f} 이상 : "
        f"{count:,}"
    )


# ============================================================
# SIGNAL DATE / TICKER
# ============================================================

signal_df = test_df[
    test_df["Probability"] >= min(
        BUY_THRESHOLD_LIST
    )
].copy()


print()
print(
    f"분석 대상 Signal : "
    f"{len(signal_df):,}"
)


required_tickers = sorted(
    signal_df[ticker_col]
    .unique()
)


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


# ------------------------------------------------------------
# Signal 이후 20거래일까지 필요하므로
# 마지막 테스트 날짜보다 충분히 뒤까지 조회
# ------------------------------------------------------------

signal_last_date = (
    signal_df["날짜"].max()
)


ohlcv_end_date = (
    signal_last_date
    + pd.Timedelta(days=60)
)


start_date = (
    TEST_START
    .strftime("%Y%m%d")
)


end_date = (
    ohlcv_end_date
    .strftime("%Y%m%d")
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
    f"OHLCV 로딩 완료 : "
    f"{len(ohlcv_dict):,} rows"
)


# ============================================================
# TRADING DATES
# ============================================================

all_ohlcv_dates = sorted(
    {
        date
        for (
            ticker,
            date
        ) in ohlcv_dict.keys()
    }
)


date_index = {
    date: idx
    for idx, date in enumerate(
        all_ohlcv_dates
    )
}


# ============================================================
# SIGNAL RETURN CALCULATION
# ============================================================

print()
print("=" * 70)
print("BUY SIGNAL Forward Return 계산")
print("=" * 70)


records = []


for row in signal_df.itertuples():

    ticker = getattr(
        row,
        ticker_col
    )

    signal_date = row.날짜

    probability = row.Probability


    # --------------------------------------------------------
    # Signal 당일 종가
    # --------------------------------------------------------

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


    ticker_dates = [
        date
        for (
            t,
            date
        ) in ohlcv_dict.keys()
        if t == ticker
    ]


    ticker_dates = sorted(
        ticker_dates
    )


    # --------------------------------------------------------
    # signal date 위치
    # --------------------------------------------------------

    try:

        signal_idx = (
            ticker_dates.index(
                signal_date
            )
        )

    except ValueError:

        continue


    result = {
        "ticker": ticker,
        "signal_date": signal_date,
        "Probability": probability,
        "signal_close": signal_close,
    }


    valid_signal = True


    for forward_day in FORWARD_DAYS:

        future_idx = (
            signal_idx +
            forward_day
        )


        if future_idx >= len(
            ticker_dates
        ):

            result[
                f"Return{forward_day}"
            ] = np.nan

            continue


        future_date = (
            ticker_dates[
                future_idx
            ]
        )


        future_data = (
            ohlcv_dict.get(
                (
                    ticker,
                    future_date
                )
            )
        )


        if future_data is None:

            result[
                f"Return{forward_day}"
            ] = np.nan

            continue


        future_close = (
            future_data["close"]
        )


        forward_return = (
            future_close /
            signal_close
            - 1
        ) * 100


        result[
            f"Return{forward_day}"
        ] = forward_return


    records.append(result)


analysis_df = pd.DataFrame(
    records
)


print(
    f"분석 완료 Signal : "
    f"{len(analysis_df):,}"
)


# ============================================================
# SIGNAL QUALITY BY THRESHOLD
# ============================================================

print()
print("=" * 70)
print("BUY SIGNAL QUALITY")
print("=" * 70)


quality_results = []


for threshold in BUY_THRESHOLD_LIST:

    subset = analysis_df[
        analysis_df["Probability"]
        >= threshold
    ].copy()


    if subset.empty:
        continue


    result = {
        "Threshold": threshold,
        "Signals": len(subset),
    }


    for forward_day in FORWARD_DAYS:

        column = (
            f"Return{forward_day}"
        )


        returns = (
            subset[column]
            .dropna()
        )


        if returns.empty:

            result[
                f"AvgReturn{forward_day}"
            ] = np.nan

            result[
                f"MedianReturn{forward_day}"
            ] = np.nan

            result[
                f"WinRate{forward_day}"
            ] = np.nan

            result[
                f"PositiveCount{forward_day}"
            ] = 0

            continue


        result[
            f"AvgReturn{forward_day}"
        ] = returns.mean()


        result[
            f"MedianReturn{forward_day}"
        ] = returns.median()


        result[
            f"WinRate{forward_day}"
        ] = (
            returns > 0
        ).mean() * 100


        result[
            f"PositiveCount{forward_day}"
        ] = (
            returns > 0
        ).sum()


    quality_results.append(
        result
    )


quality_df = pd.DataFrame(
    quality_results
)


print()


if quality_df.empty:

    print(
        "Signal quality 결과가 없습니다."
    )

else:

    print(
        quality_df.to_string(
            index=False
        )
    )


# ============================================================
# PROBABILITY BUCKET
# ============================================================

print()
print("=" * 70)
print("Probability Bucket 분석")
print("=" * 70)


analysis_df["ProbabilityBucket"] = pd.cut(
    analysis_df["Probability"],
    bins=[
        0.00,
        0.70,
        0.75,
        0.80,
        0.85,
        0.90,
        0.95,
        1.00,
    ],
    include_lowest=True
)


bucket_results = []


for bucket, group in (
    analysis_df
    .groupby(
        "ProbabilityBucket",
        observed=False
    )
):

    result = {
        "ProbabilityBucket": str(bucket),
        "Signals": len(group),
    }


    for forward_day in FORWARD_DAYS:

        column = (
            f"Return{forward_day}"
        )


        returns = (
            group[column]
            .dropna()
        )


        if returns.empty:

            result[
                f"AvgReturn{forward_day}"
            ] = np.nan

            result[
                f"WinRate{forward_day}"
            ] = np.nan

        else:

            result[
                f"AvgReturn{forward_day}"
            ] = returns.mean()


            result[
                f"WinRate{forward_day}"
            ] = (
                returns > 0
            ).mean() * 100


    bucket_results.append(
        result
    )


bucket_df = pd.DataFrame(
    bucket_results
)


print(
    bucket_df.to_string(
        index=False
    )
)


# ============================================================
# BEST THRESHOLD
# ============================================================

print()
print("=" * 70)
print("BEST BUY THRESHOLD")
print("=" * 70)


if not quality_df.empty:

    # --------------------------------------------------------
    # 20일 평균수익률 기준
    # --------------------------------------------------------

    best_20d = (
        quality_df
        .sort_values(
            "AvgReturn20",
            ascending=False
        )
        .iloc[0]
    )


    print(
        f"20일 평균수익률 기준 BEST"
    )

    print(
        f"Threshold : "
        f"{best_20d['Threshold']:.2f}"
    )

    print(
        f"Signals : "
        f"{int(best_20d['Signals'])}"
    )

    print(
        f"20일 평균수익률 : "
        f"{best_20d['AvgReturn20']:.4f}%"
    )

    print(
        f"20일 중앙값 : "
        f"{best_20d['MedianReturn20']:.4f}%"
    )

    print(
        f"20일 승률 : "
        f"{best_20d['WinRate20']:.2f}%"
    )


# ============================================================
# SAVE
# ============================================================

print()
print("=" * 70)
print("결과 저장")
print("=" * 70)


analysis_df.to_csv(
    "buy_signal_quality_detail.csv",
    index=False
)


quality_df.to_csv(
    "buy_signal_quality_threshold.csv",
    index=False
)


bucket_df.to_csv(
    "buy_signal_quality_probability_bucket.csv",
    index=False
)


print(
    "buy_signal_quality_detail.csv"
)

print(
    "buy_signal_quality_threshold.csv"
)

print(
    "buy_signal_quality_probability_bucket.csv"
)


print()
print("=" * 70)
print("완료")
print("=" * 70)