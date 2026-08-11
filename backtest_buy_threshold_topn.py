import pandas as pd
import numpy as np
import xgboost as xgb
from pykrx import stock
from itertools import product
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================

BUY_DATASET_PATH = "dataset.csv"
SELL_DATASET_PATH = "sell_dataset_v2.csv"

INITIAL_CAPITAL = 10_000_000

# 이번 실험 대상
BUY_THRESHOLD_LIST = [
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

# Risk Management는 현재 BEST 조합으로 고정
STOP_LOSS = -0.03
TAKE_PROFIT = 0.20
TRAILING_STOP = 0.03

COMMISSION = 0.00015
SLIPPAGE = 0.0005

TRAIN_END = pd.Timestamp("2023-04-12")
TEST_START = pd.Timestamp("2023-04-13")


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
        c
        for c in columns
        if c not in df.columns
    ]

    if missing:
        raise ValueError(
            f"{name}에 필요한 컬럼이 없습니다:\n{missing}"
        )


# ============================================================
# LOAD BUY DATASET
# ============================================================

print("=" * 70)
print("BUY DATASET Loading")
print("=" * 70)

buy_df = pd.read_csv(
    BUY_DATASET_PATH,
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
    BUY_FEATURE_COLUMNS + ["Target"],
    "BUY dataset"
)

buy_df = (
    buy_df
    .sort_values(
        ["날짜", buy_ticker_col]
    )
    .reset_index(drop=True)
)

buy_train_df = buy_df[
    buy_df["날짜"] <= TRAIN_END
].copy()

buy_test_df = buy_df[
    buy_df["날짜"] >= TEST_START
].copy()

print(
    f"BUY Dataset : {len(buy_df):,}"
)

print(
    f"BUY Train   : {len(buy_train_df):,}"
)

print(
    f"BUY Test    : {len(buy_test_df):,}"
)

print(
    f"BUY Train 기간 : "
    f"{buy_train_df['날짜'].min().date()} ~ "
    f"{buy_train_df['날짜'].max().date()}"
)

print(
    f"BUY Test 기간  : "
    f"{buy_test_df['날짜'].min().date()} ~ "
    f"{buy_test_df['날짜'].max().date()}"
)


# ============================================================
# LOAD SELL DATASET
# ============================================================

print()
print("=" * 70)
print("SELL DATASET Loading")
print("=" * 70)

sell_df = pd.read_csv(
    SELL_DATASET_PATH,
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
    SELL_FEATURE_COLUMNS + ["SELL_TARGET"],
    "SELL dataset"
)

sell_df = (
    sell_df
    .sort_values(
        ["날짜", sell_ticker_col]
    )
    .reset_index(drop=True)
)

sell_train_df = sell_df[
    sell_df["날짜"] <= TRAIN_END
].copy()

sell_test_df = sell_df[
    sell_df["날짜"] >= TEST_START
].copy()

print(
    f"SELL Dataset : {len(sell_df):,}"
)

print(
    f"SELL Train   : {len(sell_train_df):,}"
)

print(
    f"SELL Test    : {len(sell_test_df):,}"
)


# ============================================================
# BUY MODEL
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
    buy_train_df[BUY_FEATURE_COLUMNS],
    buy_train_df["Target"]
)


# ============================================================
# SELL MODEL
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
    sell_train_df[SELL_FEATURE_COLUMNS],
    sell_train_df["SELL_TARGET"]
)


# ============================================================
# PROBABILITY
# ============================================================

print()
print("=" * 70)
print("Probability 계산")
print("=" * 70)

buy_test_df["BUY_PROBABILITY"] = (
    buy_model.predict_proba(
        buy_test_df[BUY_FEATURE_COLUMNS]
    )[:, 1]
)

sell_test_df["SELL_PROBABILITY"] = (
    sell_model.predict_proba(
        sell_test_df[SELL_FEATURE_COLUMNS]
    )[:, 1]
)

print(
    "BUY Probability 완료"
)

print(
    "SELL Probability 완료"
)


# ============================================================
# TRADING CALENDAR
# ============================================================

trading_dates = sorted(
    buy_test_df["날짜"].unique()
)

next_date_map = {}

for i in range(
    len(trading_dates) - 1
):
    next_date_map[
        trading_dates[i]
    ] = trading_dates[i + 1]


# ============================================================
# REQUIRED TICKERS
#
# threshold가 낮아지면 BUY 후보가 늘어날 수 있으므로
# BUY 전체 테스트 데이터의 종목을 preload한다.
# ============================================================

required_tickers = set(
    buy_test_df[
        buy_test_df["BUY_PROBABILITY"] >=
        min(BUY_THRESHOLD_LIST)
    ][buy_ticker_col].unique()
)

required_tickers.update(
    sell_test_df[
        sell_test_df["SELL_PROBABILITY"] >= 0.50
    ][sell_ticker_col].unique()
)

print()
print("=" * 70)
print("필요 종목 계산")
print("=" * 70)

print(
    f"필요 종목 수 : "
    f"{len(required_tickers)}"
)


# ============================================================
# OHLCV PRELOAD
# ============================================================

print()
print("=" * 70)
print("pykrx OHLCV 사전 로딩")
print("=" * 70)

ohlcv_dict = {}

start_date = TEST_START.strftime(
    "%Y%m%d"
)

end_date = pd.Timestamp(
    trading_dates[-1]
).strftime(
    "%Y%m%d"
)

for count, ticker in enumerate(
    sorted(required_tickers),
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
            ] = (
                float(open_price),
                float(high_price),
                float(low_price),
                float(close_price),
            )

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

print(
    f"OHLCV 로딩 완료: "
    f"{len(ohlcv_dict):,} rows"
)


# ============================================================
# SELL SIGNAL INDEX
# ============================================================

sell_signals_by_date = defaultdict(set)

for row in sell_test_df.itertuples():
    if row.SELL_PROBABILITY < 0.50:
        continue

    sell_signals_by_date[
        row.날짜
    ].add(
        getattr(
            row,
            sell_ticker_col
        )
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    buy_threshold,
    top_n
):
    buy_signals_by_date = defaultdict(list)

    filtered_buy = buy_test_df[
        buy_test_df["BUY_PROBABILITY"]
        >= buy_threshold
    ]

    for row in filtered_buy.itertuples():
        buy_signals_by_date[
            row.날짜
        ].append(row)

    cash = float(
        INITIAL_CAPITAL
    )

    positions = []

    trades = []

    equity_curve = []

    # ========================================================
    # DATE LOOP
    # ========================================================

    for current_date in trading_dates:

        # ====================================================
        # 1. RISK EXIT
        # ====================================================

        remaining_positions = []

        for pos in positions:
            ticker = pos["ticker"]

            buy_price = pos["buy_price"]

            previous_peak = pos["peak_price"]

            entry_date = pos["entry_date"]

            # -----------------------------------------------
            # 매수 당일에는 risk exit하지 않음
            # -----------------------------------------------

            if current_date <= entry_date:
                remaining_positions.append(
                    pos
                )

                continue

            ohlcv = ohlcv_dict.get(
                (ticker, current_date)
            )

            if ohlcv is None:
                remaining_positions.append(
                    pos
                )

                continue

            open_p, high_p, low_p, close_p = (
                ohlcv
            )

            stop_price = (
                buy_price *
                (1 + STOP_LOSS)
            )

            tp_price = (
                buy_price *
                (1 + TAKE_PROFIT)
            )

            trail_price = (
                previous_peak *
                (1 - TRAILING_STOP)
            )

            exit_reason = None
            exit_price = None

            # STOP LOSS
            if low_p <= stop_price:
                exit_price = stop_price
                exit_reason = "STOP_LOSS"

            # TAKE PROFIT
            elif high_p >= tp_price:
                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"

            # TRAILING STOP
            elif (
                previous_peak > buy_price
                and low_p <= trail_price
            ):
                exit_price = trail_price
                exit_reason = "TRAILING_STOP"

            # -----------------------------------------------
            # EXIT
            # -----------------------------------------------

            if exit_price is not None:
                effective_buy_price = (
                    buy_price *
                    (1 + COMMISSION + SLIPPAGE)
                )

                effective_sell_price = (
                    exit_price *
                    (1 - COMMISSION - SLIPPAGE)
                )

                net_return = (
                    effective_sell_price /
                    effective_buy_price
                ) - 1

                profit = (
                    pos["capital"] *
                    net_return
                )

                cash += (
                    pos["capital"] +
                    profit
                )

                trades.append({
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "buy_price": buy_price,
                    "sell_price": exit_price,
                    "NetReturn": net_return * 100,
                    "Profit": profit,
                    "SellReason": exit_reason,
                })

            else:
                pos["peak_price"] = max(
                    previous_peak,
                    high_p
                )

                remaining_positions.append(
                    pos
                )

        positions = remaining_positions

        # ====================================================
        # 2. SELL SIGNAL EXIT
        # ====================================================

        next_date = next_date_map.get(
            current_date
        )

        if next_date is not None:
            current_sell_tickers = (
                sell_signals_by_date.get(
                    current_date,
                    set()
                )
            )

            if current_sell_tickers:
                remaining_positions = []

                for pos in positions:
                    ticker = pos["ticker"]

                    if (
                        ticker
                        not in current_sell_tickers
                    ):
                        remaining_positions.append(
                            pos
                        )

                        continue

                    next_ohlcv = ohlcv_dict.get(
                        (ticker, next_date)
                    )

                    if next_ohlcv is None:
                        remaining_positions.append(
                            pos
                        )

                        continue

                    next_open = next_ohlcv[0]

                    if next_open <= 0:
                        remaining_positions.append(
                            pos
                        )

                        continue

                    buy_price = pos["buy_price"]

                    effective_buy_price = (
                        buy_price *
                        (1 + COMMISSION + SLIPPAGE)
                    )

                    effective_sell_price = (
                        next_open *
                        (1 - COMMISSION - SLIPPAGE)
                    )

                    net_return = (
                        effective_sell_price /
                        effective_buy_price
                    ) - 1

                    profit = (
                        pos["capital"] *
                        net_return
                    )

                    cash += (
                        pos["capital"] +
                        profit
                    )

                    trades.append({
                        "ticker": ticker,
                        "entry_date": pos["entry_date"],
                        "exit_date": next_date,
                        "buy_price": buy_price,
                        "sell_price": next_open,
                        "NetReturn": net_return * 100,
                        "Profit": profit,
                        "SellReason": "SELL_SIGNAL",
                    })

                positions = remaining_positions

        # ====================================================
        # 3. BUY SIGNAL
        # ====================================================

        day_buy_signals = (
            buy_signals_by_date.get(
                current_date,
                []
            )
        )

        next_date = next_date_map.get(
            current_date
        )

        if (
            day_buy_signals
            and next_date is not None
        ):

            held_tickers = {
                pos["ticker"]
                for pos in positions
            }

            candidates = []

            for sig in day_buy_signals:
                ticker = getattr(
                    sig,
                    buy_ticker_col
                )

                if ticker in held_tickers:
                    continue

                next_ohlcv = ohlcv_dict.get(
                    (ticker, next_date)
                )

                if next_ohlcv is None:
                    continue

                next_open = next_ohlcv[0]

                if next_open <= 0:
                    continue

                candidates.append(sig)

            candidates.sort(
                key=lambda x:
                    x.BUY_PROBABILITY,
                reverse=True
            )

            available_slots = (
                top_n -
                len(positions)
            )

            if (
                available_slots > 0
                and cash > 0
                and candidates
            ):
                selected = candidates[
                    :available_slots
                ]

                alloc_capital = (
                    cash /
                    len(selected)
                )

                for sig in selected:
                    ticker = getattr(
                        sig,
                        buy_ticker_col
                    )

                    next_ohlcv = (
                        ohlcv_dict.get(
                            (ticker, next_date)
                        )
                    )

                    if next_ohlcv is None:
                        continue

                    next_open = next_ohlcv[0]

                    if next_open <= 0:
                        continue

                    actual_capital = min(
                        alloc_capital,
                        cash
                    )

                    if actual_capital <= 0:
                        continue

                    cash -= actual_capital

                    positions.append({
                        "ticker": ticker,
                        "buy_price": next_open,
                        "capital": actual_capital,
                        "peak_price": next_open,
                        "entry_date": next_date,
                    })

                    held_tickers.add(
                        ticker
                    )

        # ====================================================
        # 4. EQUITY
        # ====================================================

        portfolio_value = cash

        for pos in positions:
            ticker = pos["ticker"]

            ohlcv = ohlcv_dict.get(
                (ticker, current_date)
            )

            if ohlcv is None:
                current_price = pos[
                    "buy_price"
                ]

            else:
                current_price = ohlcv[3]

            effective_buy_price = (
                pos["buy_price"] *
                (1 + COMMISSION + SLIPPAGE)
            )

            effective_current_price = (
                current_price *
                (1 - COMMISSION - SLIPPAGE)
            )

            unrealized_ratio = (
                effective_current_price /
                effective_buy_price
            )

            portfolio_value += (
                pos["capital"] *
                unrealized_ratio
            )

        equity_curve.append({
            "date": current_date,
            "equity": portfolio_value,
        })

    # ========================================================
    # 5. BACKTEST END EXIT
    # ========================================================

    if positions:
        last_date = trading_dates[-1]

        for pos in positions:
            ticker = pos["ticker"]

            ohlcv = ohlcv_dict.get(
                (ticker, last_date)
            )

            if ohlcv is None:
                exit_price = pos[
                    "buy_price"
                ]

            else:
                exit_price = ohlcv[3]

            effective_buy_price = (
                pos["buy_price"] *
                (1 + COMMISSION + SLIPPAGE)
            )

            effective_sell_price = (
                exit_price *
                (1 - COMMISSION - SLIPPAGE)
            )

            net_return = (
                effective_sell_price /
                effective_buy_price
            ) - 1

            profit = (
                pos["capital"] *
                net_return
            )

            cash += (
                pos["capital"] +
                profit
            )

            trades.append({
                "ticker": ticker,
                "entry_date": pos["entry_date"],
                "exit_date": last_date,
                "buy_price": pos["buy_price"],
                "sell_price": exit_price,
                "NetReturn": net_return * 100,
                "Profit": profit,
                "SellReason": "BACKTEST_END",
            })

    # ========================================================
    # METRICS
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_curve
    )

    if equity_df.empty:
        return {
            "BUY_THRESHOLD": buy_threshold,
            "TOP_N": top_n,
            "Trades": 0,
            "AverageReturn": 0,
            "MedianReturn": 0,
            "WinRate": 0,
            "ProfitFactor": 0,
            "MDD": 0,
            "CAGR": 0,
            "FinalCapital": INITIAL_CAPITAL,
            "TotalReturn": 0,
            "SellSignalExits": 0,
            "StopLossExits": 0,
            "TakeProfitExits": 0,
            "TrailingStopExits": 0,
            "BacktestEndExits": 0,
        }

    equity_series = (
        equity_df["equity"]
    )

    running_max = (
        equity_series.cummax()
    )

    drawdown = (
        equity_series /
        running_max
    ) - 1

    mdd = (
        drawdown.min() *
        100
    )

    final_capital = (
        equity_series.iloc[-1]
    )

    total_return = (
        final_capital /
        INITIAL_CAPITAL
        - 1
    ) * 100

    # ========================================================
    # CAGR
    # ========================================================

    first_date = pd.Timestamp(
        equity_df["date"].iloc[0]
    )

    last_date = pd.Timestamp(
        equity_df["date"].iloc[-1]
    )

    years = (
        last_date - first_date
    ).days / 365.25

    if (
        years > 0
        and final_capital > 0
    ):
        cagr = (
            (
                final_capital /
                INITIAL_CAPITAL
            ) ** (1 / years)
            - 1
        ) * 100

    else:
        cagr = 0

    # ========================================================
    # TRADE METRICS
    # ========================================================

    if trades_df.empty:
        average_return = 0
        median_return = 0
        win_rate = 0
        profit_factor = 0

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

        gross_profit = (
            trades_df.loc[
                trades_df["Profit"] > 0,
                "Profit"
            ].sum()
        )

        gross_loss = abs(
            trades_df.loc[
                trades_df["Profit"] < 0,
                "Profit"
            ].sum()
        )

        if gross_loss > 0:
            profit_factor = (
                gross_profit /
                gross_loss
            )

        else:
            profit_factor = np.inf

    # ========================================================
    # EXIT BREAKDOWN
    # ========================================================

    if trades_df.empty:
        exit_counts = {}

    else:
        exit_counts = (
            trades_df["SellReason"]
            .value_counts()
            .to_dict()
        )

    return {
        "BUY_THRESHOLD": buy_threshold,
        "TOP_N": top_n,
        "Trades": len(trades_df),
        "AverageReturn": average_return,
        "MedianReturn": median_return,
        "WinRate": win_rate,
        "ProfitFactor": profit_factor,
        "MDD": mdd,
        "CAGR": cagr,
        "FinalCapital": final_capital,
        "TotalReturn": total_return,

        "SellSignalExits":
            exit_counts.get(
                "SELL_SIGNAL",
                0
            ),

        "StopLossExits":
            exit_counts.get(
                "STOP_LOSS",
                0
            ),

        "TakeProfitExits":
            exit_counts.get(
                "TAKE_PROFIT",
                0
            ),

        "TrailingStopExits":
            exit_counts.get(
                "TRAILING_STOP",
                0
            ),

        "BacktestEndExits":
            exit_counts.get(
                "BACKTEST_END",
                0
            ),
    }


# ============================================================
# PARAMETER SWEEP
# ============================================================

print()
print("=" * 70)
print("BUY THRESHOLD + TOP_N SWEEP")
print("=" * 70)

print(
    f"STOP LOSS     : {STOP_LOSS * 100:.1f}%"
)

print(
    f"TAKE PROFIT   : {TAKE_PROFIT * 100:.1f}%"
)

print(
    f"TRAILING STOP : {TRAILING_STOP * 100:.1f}%"
)

print()

results = []

parameter_combinations = list(
    product(
        BUY_THRESHOLD_LIST,
        TOP_N_LIST,
    )
)

for (
    buy_threshold,
    top_n
) in parameter_combinations:
    print(
        f"BUY_THRESHOLD="
        f"{buy_threshold:.2f} "
        f"TOP_N={top_n}"
    )

    result = run_backtest(
        buy_threshold,
        top_n
    )

    results.append(result)


# ============================================================
# RESULTS
# ============================================================

results_df = pd.DataFrame(
    results
)

results_df = results_df.sort_values(
    [
        "TotalReturn",
        "MDD",
    ],
    ascending=[
        False,
        False,
    ]
).reset_index(
    drop=True
)


print()
print("=" * 70)
print("BUY THRESHOLD + TOP_N 결과")
print("=" * 70)

print(
    results_df.to_string(
        index=False
    )
)


# ============================================================
# MDD FILTER
# ============================================================

print()
print("=" * 70)
print("MDD -30% 이내 전략")
print("=" * 70)

mdd_30_df = results_df[
    results_df["MDD"] >= -30
]

if mdd_30_df.empty:
    print(
        "MDD -30% 이내 전략이 없습니다."
    )

else:
    print(
        mdd_30_df.to_string(
            index=False
        )
    )


print()
print("=" * 70)
print("MDD -40% 이내 전략")
print("=" * 70)

mdd_40_df = results_df[
    results_df["MDD"] >= -40
]

if mdd_40_df.empty:
    print(
        "MDD -40% 이내 전략이 없습니다."
    )

else:
    print(
        mdd_40_df.to_string(
            index=False
        )
    )


# ============================================================
# BEST RETURN
# ============================================================

best_return = results_df.iloc[0]

print()
print("=" * 70)
print("BEST RETURN STRATEGY")
print("=" * 70)

print(
    f"BUY Threshold : "
    f"{best_return['BUY_THRESHOLD']:.2f}"
)

print(
    f"TOP N         : "
    f"{int(best_return['TOP_N'])}"
)

print(
    f"Trades        : "
    f"{int(best_return['Trades'])}"
)

print(
    f"Win Rate      : "
    f"{best_return['WinRate']:.2f}%"
)

print(
    f"Profit Factor : "
    f"{best_return['ProfitFactor']:.4f}"
)

print(
    f"CAGR          : "
    f"{best_return['CAGR']:.2f}%"
)

print(
    f"MDD           : "
    f"{best_return['MDD']:.2f}%"
)

print(
    f"Final Capital : "
    f"{best_return['FinalCapital']:,.0f}"
)

print(
    f"Total Return  : "
    f"{best_return['TotalReturn']:.2f}%"
)


# ============================================================
# BEST MDD-CONSTRAINED STRATEGY
# ============================================================

if not mdd_30_df.empty:
    best_mdd30 = mdd_30_df.iloc[0]

    print()
    print("=" * 70)
    print("BEST MDD -30% STRATEGY")
    print("=" * 70)

    print(
        f"BUY Threshold : "
        f"{best_mdd30['BUY_THRESHOLD']:.2f}"
    )

    print(
        f"TOP N         : "
        f"{int(best_mdd30['TOP_N'])}"
    )

    print(
        f"Trades        : "
        f"{int(best_mdd30['Trades'])}"
    )

    print(
        f"Win Rate      : "
        f"{best_mdd30['WinRate']:.2f}%"
    )

    print(
        f"Profit Factor : "
        f"{best_mdd30['ProfitFactor']:.4f}"
    )

    print(
        f"CAGR          : "
        f"{best_mdd30['CAGR']:.2f}%"
    )

    print(
        f"MDD           : "
        f"{best_mdd30['MDD']:.2f}%"
    )

    print(
        f"Final Capital : "
        f"{best_mdd30['FinalCapital']:,.0f}"
    )

    print(
        f"Total Return  : "
        f"{best_mdd30['TotalReturn']:.2f}%"
    )


# ============================================================
# SAVE
# ============================================================

results_df.to_csv(
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