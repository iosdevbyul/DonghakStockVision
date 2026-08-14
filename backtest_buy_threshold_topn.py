import os
import warnings

import numpy as np
import pandas as pd
import xgboost as xgb
from pykrx import stock

warnings.filterwarnings("ignore")


# ============================================================
# CONFIG
# ============================================================

BUY_DATASET = "dataset.csv"
SELL_DATASET = "sell_dataset_v2.csv"

INITIAL_CAPITAL = 10_000_000

BUY_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
TOP_N_VALUES = [3, 5, 10, 20]

STOP_LOSSES = [-0.03, -0.05, -0.07, -0.10]
TAKE_PROFITS = [0.05, 0.10, 0.15, 0.20]
TRAILING_STOPS = [0.00, 0.03, 0.05]

SELL_THRESHOLD = 0.50

HOLD_DAYS = 20

# 실제 투자금 기준
POSITION_SIZE = 1.0


# ============================================================
# XGBOOST
# ============================================================

XGB_PARAMS = {
    "learning_rate": 0.15,
    "max_depth": 8,
    "n_estimators": 150,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "tree_method": "hist",
    "random_state": 42,
}


# ============================================================
# COLUMN CONFIG
# ============================================================

TARGET_COLUMNS = {
    "buy": "Target",
    "sell": "SELL_TARGET",
}

EXCLUDE_COLUMNS = {
    "BUY_TARGET",
    "SELL_TARGET",
    "date",
    "날짜",
    "종목코드",
    "티커",
    "ticker",
    "Probability",
}


# ============================================================
# UTIL
# ============================================================

def find_date_column(df):
    candidates = [
        "date",
        "날짜",
        "Date",
        "DATE",
    ]

    for column in candidates:
        if column in df.columns:
            return column

    raise ValueError("날짜 컬럼을 찾을 수 없습니다.")


def find_code_column(df):
    candidates = [
        "종목코드",
        "티커",
        "ticker",
        "code",
        "Code",
    ]

    for column in candidates:
        if column in df.columns:
            return column

    raise ValueError("종목코드 컬럼을 찾을 수 없습니다.")


def get_feature_columns(df, target):
    excluded = set(EXCLUDE_COLUMNS)
    excluded.add(target)

    features = [
        column
        for column in df.columns
        if column not in excluded
        and pd.api.types.is_numeric_dtype(df[column])
    ]

    if not features:
        raise ValueError("사용 가능한 Feature가 없습니다.")

    return features


# ============================================================
# DATA LOADING
# ============================================================

print("=" * 70)
print("BUY DATASET Loading")
print("=" * 70)

buy_df = pd.read_csv(BUY_DATASET)

buy_date_col = find_date_column(buy_df)
buy_code_col = find_code_column(buy_df)

buy_df[buy_date_col] = pd.to_datetime(buy_df[buy_date_col])

buy_df = buy_df.sort_values(
    [buy_date_col, buy_code_col]
).reset_index(drop=True)

print(f"BUY Dataset : {len(buy_df):,}")

buy_split_index = int(len(buy_df) * 0.671)

buy_train = buy_df.iloc[:buy_split_index].copy()
buy_test = buy_df.iloc[buy_split_index:].copy()

print(f"BUY Train   : {len(buy_train):,}")
print(f"BUY Test    : {len(buy_test):,}")
print(
    f"BUY Train 기간 : "
    f"{buy_train[buy_date_col].min().date()} ~ "
    f"{buy_train[buy_date_col].max().date()}"
)
print(
    f"BUY Test 기간  : "
    f"{buy_test[buy_date_col].min().date()} ~ "
    f"{buy_test[buy_date_col].max().date()}"
)


print()
print("=" * 70)
print("SELL DATASET Loading")
print("=" * 70)

sell_df = pd.read_csv(SELL_DATASET)

sell_date_col = find_date_column(sell_df)
sell_code_col = find_code_column(sell_df)

sell_df[sell_date_col] = pd.to_datetime(sell_df[sell_date_col])

sell_df = sell_df.sort_values(
    [sell_date_col, sell_code_col]
).reset_index(drop=True)

print(f"SELL Dataset : {len(sell_df):,}")

sell_split_index = int(len(sell_df) * 0.668)

sell_train = sell_df.iloc[:sell_split_index].copy()
sell_test = sell_df.iloc[sell_split_index:].copy()

print(f"SELL Train   : {len(sell_train):,}")
print(f"SELL Test    : {len(sell_test):,}")

print(
    f"SELL Train 기간 : "
    f"{sell_train[sell_date_col].min().date()} ~ "
    f"{sell_train[sell_date_col].max().date()}"
)

print(
    f"SELL Test 기간  : "
    f"{sell_test[sell_date_col].min().date()} ~ "
    f"{sell_test[sell_date_col].max().date()}"
)


# ============================================================
# FEATURES
# ============================================================

buy_features = get_feature_columns(
    buy_train,
    TARGET_COLUMNS["buy"]
)

sell_features = get_feature_columns(
    sell_train,
    TARGET_COLUMNS["sell"]
)

# 두 모델에서 공통으로 사용할 feature만 사용
features = [
    column
    for column in buy_features
    if column in sell_features
]

print()
print("=" * 70)
print("FEATURES")
print("=" * 70)
print(f"Feature 수 : {len(features)}")


# ============================================================
# BUY MODEL
# ============================================================

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)

X_buy_train = buy_train[features]
y_buy_train = buy_train[TARGET_COLUMNS["buy"]]

X_buy_test = buy_test[features]

buy_model = xgb.XGBClassifier(
    **XGB_PARAMS
)

buy_model.fit(
    X_buy_train,
    y_buy_train
)

buy_test["BUY_PROBABILITY"] = buy_model.predict_proba(
    X_buy_test
)[:, 1]


# ============================================================
# SELL MODEL
# ============================================================

print()
print("=" * 70)
print("SELL XGBoost 학습")
print("=" * 70)

X_sell_train = sell_train[features]
y_sell_train = sell_train[TARGET_COLUMNS["sell"]]

X_sell_test = sell_test[features]

sell_model = xgb.XGBClassifier(
    **XGB_PARAMS
)

sell_model.fit(
    X_sell_train,
    y_sell_train
)

sell_test["SELL_PROBABILITY"] = sell_model.predict_proba(
    X_sell_test
)[:, 1]


# ============================================================
# OHLCV CACHE
# ============================================================

all_codes = set(
    buy_test[buy_code_col].astype(str)
)

all_codes.update(
    sell_test[sell_code_col].astype(str)
)

all_codes = sorted(all_codes)

print()
print("=" * 70)
print("pykrx OHLCV 사전 로딩")
print("=" * 70)

ohlcv_cache = {}

for index, code in enumerate(all_codes, start=1):

    if index % 100 == 0:
        print(
            f"OHLCV Loading: "
            f"{index:,} / {len(all_codes):,}"
        )

    try:

        df = stock.get_market_ohlcv_by_date(
            "20110101",
            "20261231",
            code
        )

        if df is None or df.empty:
            continue

        df = df.reset_index()

        date_column = df.columns[0]

        df[date_column] = pd.to_datetime(
            df[date_column]
        )

        df = df.rename(
            columns={
                "시가": "OPEN",
                "고가": "HIGH",
                "저가": "LOW",
                "종가": "CLOSE",
            }
        )

        required = [
            "OPEN",
            "HIGH",
            "LOW",
            "CLOSE",
        ]

        if not all(
            column in df.columns
            for column in required
        ):
            continue

        df = df[
            [
                date_column,
                "OPEN",
                "HIGH",
                "LOW",
                "CLOSE",
            ]
        ].copy()

        df = df.rename(
            columns={
                date_column: "DATE"
            }
        )

        df = df.set_index("DATE")

        ohlcv_cache[code] = df

    except Exception as e:
        continue

print(
    f"OHLCV 로딩 완료: "
    f"{sum(len(df) for df in ohlcv_cache.values()):,} rows"
)


# ============================================================
# PREPARE SIGNALS
# ============================================================

buy_signals = buy_test[
    [
        buy_date_col,
        buy_code_col,
        "BUY_PROBABILITY",
    ]
].copy()

sell_signals = sell_test[
    [
        sell_date_col,
        sell_code_col,
        "SELL_PROBABILITY",
    ]
].copy()

buy_signals = buy_signals.rename(
    columns={
        buy_date_col: "DATE",
        buy_code_col: "CODE",
    }
)

sell_signals = sell_signals.rename(
    columns={
        sell_date_col: "DATE",
        sell_code_col: "CODE",
    }
)

buy_signals["CODE"] = buy_signals["CODE"].astype(str)
sell_signals["CODE"] = sell_signals["CODE"].astype(str)


# ============================================================
# SELL SIGNAL LOOKUP
# ============================================================

sell_lookup = {}

for row in sell_signals.itertuples(index=False):

    key = (
        row.CODE,
        pd.Timestamp(row.DATE)
    )

    sell_lookup[key] = row.SELL_PROBABILITY


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    threshold,
    top_n,
    stop_loss,
    take_profit,
    trailing_stop,
):

    signals = buy_signals[
        buy_signals["BUY_PROBABILITY"] >= threshold
    ].copy()

    if signals.empty:
        return [], pd.DataFrame()

    # 날짜별 Probability 순위
    signals["RANK"] = (
        signals
        .groupby("DATE")["BUY_PROBABILITY"]
        .rank(
            method="first",
            ascending=False
        )
    )

    signals = signals[
        signals["RANK"] <= top_n
    ].copy()

    signals = signals.sort_values(
        ["DATE", "BUY_PROBABILITY"],
        ascending=[True, False]
    )

    trades = []

    for row in signals.itertuples(index=False):

        code = row.CODE
        entry_date = pd.Timestamp(row.DATE)

        if code not in ohlcv_cache:
            continue

        price_df = ohlcv_cache[code]

        future = price_df[
            price_df.index > entry_date
        ].head(HOLD_DAYS)

        if future.empty:
            continue

        entry_price = float(
            future.iloc[0]["OPEN"]
        )

        if entry_price <= 0:
            continue

        highest_price = entry_price

        exit_price = None
        exit_date = None
        sell_reason = None

        holding_days = 0

        for current_date, candle in future.iterrows():

            holding_days += 1

            open_price = float(candle["OPEN"])
            high_price = float(candle["HIGH"])
            low_price = float(candle["LOW"])
            close_price = float(candle["CLOSE"])

            highest_price = max(
                highest_price,
                high_price
            )

            # ------------------------------------------------
            # SELL SIGNAL
            # ------------------------------------------------

            sell_probability = sell_lookup.get(
                (code, pd.Timestamp(current_date)),
                0.0
            )

            if sell_probability >= SELL_THRESHOLD:

                exit_price = close_price
                exit_date = current_date
                sell_reason = "SELL_SIGNAL"

                break

            # ------------------------------------------------
            # STOP LOSS
            # ------------------------------------------------

            stop_price = entry_price * (
                1 + stop_loss
            )

            if low_price <= stop_price:

                exit_price = stop_price
                exit_date = current_date
                sell_reason = "STOP_LOSS"

                break

            # ------------------------------------------------
            # TAKE PROFIT
            # ------------------------------------------------

            take_price = entry_price * (
                1 + take_profit
            )

            if high_price >= take_price:

                exit_price = take_price
                exit_date = current_date
                sell_reason = "TAKE_PROFIT"

                break

            # ------------------------------------------------
            # TRAILING STOP
            # ------------------------------------------------

            if trailing_stop > 0:

                trailing_price = highest_price * (
                    1 - trailing_stop
                )

                # entry 대비 상승한 이후에만 trailing
                if (
                    highest_price > entry_price
                    and low_price <= trailing_price
                ):

                    exit_price = trailing_price
                    exit_date = current_date
                    sell_reason = "TRAILING_STOP"

                    break

        # ----------------------------------------------------
        # BACKTEST END
        # ----------------------------------------------------

        if exit_price is None:

            last_date = future.index[-1]
            last_close = float(
                future.iloc[-1]["CLOSE"]
            )

            exit_price = last_close
            exit_date = last_date
            sell_reason = "BACKTEST_END"

        return_rate = (
            exit_price / entry_price - 1
        ) * 100

        trades.append(
            {
                "EntryDate": entry_date,
                "ExitDate": pd.Timestamp(exit_date),
                "Code": code,
                "Probability": row.BUY_PROBABILITY,
                "EntryPrice": entry_price,
                "ExitPrice": exit_price,
                "Return": return_rate,
                "HoldingDays": holding_days,
                "SellReason": sell_reason,
                "Threshold": threshold,
                "TOP_N": top_n,
                "StopLoss": stop_loss,
                "TakeProfit": take_profit,
                "TrailingStop": trailing_stop,
            }
        )

    if not trades:
        return [], pd.DataFrame()

    trades_df = pd.DataFrame(trades)

    # --------------------------------------------------------
    # EQUITY
    # --------------------------------------------------------

    capital = INITIAL_CAPITAL

    equity = []

    for trade in trades:

        capital *= (
            1 + trade["Return"] / 100
        )

        equity.append(
            {
                "ExitDate": trade["ExitDate"],
                "Capital": capital,
            }
        )

    equity_df = pd.DataFrame(equity)

    if equity_df.empty:
        mdd = 0.0

    else:

        equity_values = equity_df["Capital"]

        peak = equity_values.cummax()

        drawdown = (
            equity_values / peak - 1
        ) * 100

        mdd = drawdown.min()

    return trades, equity_df.assign(
        MDD=mdd
    )


# ============================================================
# PARAMETER SWEEP
# ============================================================

print()
print("=" * 70)
print(
    "BUY THRESHOLD + TOP_N + "
    "RISK MANAGEMENT PARAMETER SWEEP"
)
print("=" * 70)

results = []

best_trades = None
best_equity = None
best_score = -np.inf
best_params = None


for threshold in BUY_THRESHOLDS:

    for top_n in TOP_N_VALUES:

        for stop_loss in STOP_LOSSES:

            for take_profit in TAKE_PROFITS:

                for trailing_stop in TRAILING_STOPS:

                    print(
                        f"THRESHOLD={threshold:.2f} "
                        f"TOP_N={top_n} "
                        f"STOP={stop_loss * 100:5.1f}% "
                        f"TP={take_profit * 100:5.1f}% "
                        f"TRAIL={trailing_stop * 100:5.1f}%"
                    )

                    trades, equity_df = run_backtest(
                        threshold=threshold,
                        top_n=top_n,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trailing_stop=trailing_stop,
                    )

                    if not trades:
                        continue

                    trades_df = pd.DataFrame(trades)

                    returns = trades_df["Return"]

                    average_return = returns.mean()
                    median_return = returns.median()

                    win_rate = (
                        returns > 0
                    ).mean() * 100

                    if equity_df.empty:

                        mdd = 0.0
                        final_capital = INITIAL_CAPITAL

                    else:

                        mdd = float(
                            equity_df["MDD"].iloc[-1]
                        )

                        final_capital = float(
                            equity_df["Capital"].iloc[-1]
                        )

                    total_return = (
                        final_capital
                        / INITIAL_CAPITAL
                        - 1
                    ) * 100

                    result = {
                        "BUY_THRESHOLD": threshold,
                        "TOP_N": top_n,
                        "StopLoss": stop_loss,
                        "TakeProfit": take_profit,
                        "TrailingStop": trailing_stop,
                        "Trades": len(trades_df),
                        "AverageReturn": average_return,
                        "MedianReturn": median_return,
                        "WinRate": win_rate,
                        "MDD": mdd,
                        "FinalCapital": final_capital,
                        "TotalReturn": total_return,
                    }

                    results.append(result)

                    # MDD -30% 이내에서
                    # TotalReturn 최대 전략을 best로 선정
                    if (
                        mdd >= -30
                        and total_return > best_score
                    ):

                        best_score = total_return

                        best_params = result

                        best_trades = trades_df.copy()
                        best_equity = equity_df.copy()


# ============================================================
# RESULT
# ============================================================

results_df = pd.DataFrame(results)

results_df = results_df.sort_values(
    "TotalReturn",
    ascending=False
).reset_index(drop=True)


print()
print("=" * 70)
print("BUY + TOP_N + RISK MANAGEMENT 결과")
print("=" * 70)

print(
    results_df.to_string(
        index=False
    )
)


# ============================================================
# MDD FILTERS
# ============================================================

for limit in [-20, -30, -40, -50]:

    print()
    print("=" * 70)
    print(
        f"MDD {limit}% 이내 전략"
    )
    print("=" * 70)

    filtered = results_df[
        results_df["MDD"] >= limit
    ]

    if filtered.empty:

        print(
            f"MDD {limit}% 이내 전략이 없습니다."
        )

    else:

        print(
            filtered.head(20).to_string(
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

if best_params is None:

    print(
        "MDD -30% 이내의 전략이 없습니다."
    )

    # fallback
    best_params = results_df.iloc[0].to_dict()

    fallback = results_df.iloc[0]

    trades, equity = run_backtest(
        threshold=fallback["BUY_THRESHOLD"],
        top_n=int(fallback["TOP_N"]),
        stop_loss=fallback["StopLoss"],
        take_profit=fallback["TakeProfit"],
        trailing_stop=fallback["TrailingStop"],
    )

    best_trades = pd.DataFrame(trades)
    best_equity = equity

print(
    f"BUY Threshold : "
    f"{best_params['BUY_THRESHOLD']:.2f}"
)

print(
    f"TOP_N         : "
    f"{int(best_params['TOP_N'])}"
)

print(
    f"Stop Loss     : "
    f"{best_params['StopLoss'] * 100:.1f}%"
)

print(
    f"Take Profit   : "
    f"{best_params['TakeProfit'] * 100:.1f}%"
)

print(
    f"Trailing Stop : "
    f"{best_params['TrailingStop'] * 100:.1f}%"
)

print(
    f"Trades        : "
    f"{int(best_params['Trades'])}"
)

print(
    f"Win Rate      : "
    f"{best_params['WinRate']:.2f}%"
)

print(
    f"Average Return: "
    f"{best_params['AverageReturn']:.4f}%"
)

print(
    f"Median Return : "
    f"{best_params['MedianReturn']:.4f}%"
)

print(
    f"MDD           : "
    f"{best_params['MDD']:.2f}%"
)

print(
    f"Final Capital : "
    f"{best_params['FinalCapital']:,.0f}"
)

print(
    f"Total Return  : "
    f"{best_params['TotalReturn']:.2f}%"
)


# ============================================================
# EXIT BREAKDOWN
# ============================================================

if best_trades is not None and not best_trades.empty:

    print()
    print("Exit Breakdown")

    breakdown = (
        best_trades["SellReason"]
        .value_counts()
    )

    for reason in [
        "SELL_SIGNAL",
        "STOP_LOSS",
        "TAKE_PROFIT",
        "TRAILING_STOP",
        "BACKTEST_END",
    ]:

        print(
            f"{reason:16s}: "
            f"{int(breakdown.get(reason, 0))}"
        )


# ============================================================
# HOLDING ANALYSIS
# ============================================================

if best_trades is not None and not best_trades.empty:

    print()
    print("=" * 70)
    print("BEST STRATEGY 보유기간 분석")
    print("=" * 70)

    holding_stats = (
        best_trades
        .groupby("SellReason")
        .agg(
            Trades=("Return", "count"),
            AverageHoldingDays=(
                "HoldingDays",
                "mean"
            ),
            MedianHoldingDays=(
                "HoldingDays",
                "median"
            ),
            AverageReturn=(
                "Return",
                "mean"
            ),
            MedianReturn=(
                "Return",
                "median"
            ),
            WinRate=(
                "Return",
                lambda x: (
                    x > 0
                ).mean() * 100
            ),
        )
        .reset_index()
    )

    print(
        holding_stats.to_string(
            index=False
        )
    )

else:

    holding_stats = pd.DataFrame()


# ============================================================
# SAVE
# ============================================================

results_df.to_csv(
    "buy_topn_risk_management_sweep.csv",
    index=False
)

if best_trades is not None:
    best_trades.to_csv(
        "buy_topn_risk_management_best_trades.csv",
        index=False
    )

if best_equity is not None:
    best_equity.to_csv(
        "buy_topn_risk_management_best_equity.csv",
        index=False
    )

holding_stats.to_csv(
    "buy_topn_risk_management_holding_stats.csv",
    index=False
)

print()
print("=" * 70)
print("저장 완료")
print("=" * 70)

print(
    "buy_topn_risk_management_sweep.csv"
)

print(
    "buy_topn_risk_management_best_trades.csv"
)

print(
    "buy_topn_risk_management_best_equity.csv"
)

print(
    "buy_topn_risk_management_holding_stats.csv"
)