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

BUY_THRESHOLD = 0.85
SELL_THRESHOLD = 0.50

TOP_N = 5

STOP_LOSS_LIST = [-0.03, -0.05, -0.07, -0.10]
TAKE_PROFIT_LIST = [0.05, 0.10, 0.15, 0.20]
TRAILING_STOP_LIST = [0.00, 0.03, 0.05]

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

    raise ValueError("종목 코드 컬럼을 찾을 수 없습니다.")


def validate_columns(df, columns, name):
    missing = [c for c in columns if c not in df.columns]

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

buy_ticker_col = find_ticker_column(buy_df)

buy_df["날짜"] = pd.to_datetime(buy_df["날짜"])
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
    .sort_values(["날짜", buy_ticker_col])
    .reset_index(drop=True)
)

buy_train_df = buy_df[
    buy_df["날짜"] <= TRAIN_END
].copy()

buy_test_df = buy_df[
    buy_df["날짜"] >= TEST_START
].copy()

print(f"BUY Dataset : {len(buy_df):,}")
print(f"BUY Train   : {len(buy_train_df):,}")
print(f"BUY Test    : {len(buy_test_df):,}")

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

sell_ticker_col = find_ticker_column(sell_df)

sell_df["날짜"] = pd.to_datetime(sell_df["날짜"])
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
    .sort_values(["날짜", sell_ticker_col])
    .reset_index(drop=True)
)

sell_train_df = sell_df[
    sell_df["날짜"] <= TRAIN_END
].copy()

sell_test_df = sell_df[
    sell_df["날짜"] >= TEST_START
].copy()

print(f"SELL Dataset : {len(sell_df):,}")
print(f"SELL Train   : {len(sell_train_df):,}")
print(f"SELL Test    : {len(sell_test_df):,}")

print(
    f"SELL Train 기간 : "
    f"{sell_train_df['날짜'].min().date()} ~ "
    f"{sell_train_df['날짜'].max().date()}"
)

print(
    f"SELL Test 기간  : "
    f"{sell_test_df['날짜'].min().date()} ~ "
    f"{sell_test_df['날짜'].max().date()}"
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
# BUY PROBABILITY
# ============================================================

print()
print("=" * 70)
print("BUY Probability 계산")
print("=" * 70)

buy_test_df["BUY_PROBABILITY"] = (
    buy_model.predict_proba(
        buy_test_df[BUY_FEATURE_COLUMNS]
    )[:, 1]
)

buy_signals = buy_test_df[
    buy_test_df["BUY_PROBABILITY"] >= BUY_THRESHOLD
].copy()

print(f"BUY Threshold : {BUY_THRESHOLD}")
print(f"BUY Signal 수 : {len(buy_signals):,}")


# ============================================================
# SELL PROBABILITY
# ============================================================

print()
print("=" * 70)
print("SELL Probability 계산")
print("=" * 70)

sell_test_df["SELL_PROBABILITY"] = (
    sell_model.predict_proba(
        sell_test_df[SELL_FEATURE_COLUMNS]
    )[:, 1]
)

sell_signals = sell_test_df[
    sell_test_df["SELL_PROBABILITY"] >= SELL_THRESHOLD
].copy()

print(f"SELL Threshold : {SELL_THRESHOLD}")
print(f"SELL Signal 수 : {len(sell_signals):,}")


# ============================================================
# TRADING CALENDAR
# ============================================================

trading_dates = sorted(
    buy_test_df["날짜"].unique()
)

next_date_map = {}

for i in range(len(trading_dates) - 1):
    next_date_map[
        trading_dates[i]
    ] = trading_dates[i + 1]


# ============================================================
# REQUIRED TICKERS
# ============================================================

required_tickers = set(
    buy_signals[buy_ticker_col].unique()
)

required_tickers.update(
    sell_signals[sell_ticker_col].unique()
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

start_date = TEST_START.strftime("%Y%m%d")

end_date = (
    pd.Timestamp(trading_dates[-1])
    .strftime("%Y%m%d")
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
# SIGNAL INDEX
# ============================================================

buy_signals_by_date = defaultdict(list)

for row in buy_signals.itertuples():

    buy_signals_by_date[
        row.날짜
    ].append(row)


sell_signals_by_date = defaultdict(set)

for row in sell_signals.itertuples():

    sell_signals_by_date[
        row.날짜
    ].add(
        getattr(
            row,
            sell_ticker_col
        )
    )


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(
    stop_loss,
    take_profit,
    trailing_stop
):

    cash = float(INITIAL_CAPITAL)

    positions = []

    trades = []

    equity_curve = []

    sell_signal_exit_count = 0
    stop_loss_exit_count = 0
    take_profit_exit_count = 0
    trailing_stop_exit_count = 0
    backtest_end_exit_count = 0


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

            # ------------------------------------------------
            # 매수 당일에는 risk exit을 판정하지 않는다.
            # ------------------------------------------------

            if current_date <= entry_date:

                remaining_positions.append(pos)
                continue


            ohlcv = ohlcv_dict.get(
                (ticker, current_date)
            )

            if ohlcv is None:

                remaining_positions.append(pos)
                continue


            open_p, high_p, low_p, close_p = ohlcv


            # ------------------------------------------------
            # 가격 계산
            # ------------------------------------------------

            stop_price = (
                buy_price *
                (1 + stop_loss)
            )

            tp_price = (
                buy_price *
                (1 + take_profit)
            )

            trail_price = None

            if trailing_stop > 0:

                trail_price = (
                    previous_peak *
                    (1 - trailing_stop)
                )


            exit_reason = None
            exit_price = None


            # ------------------------------------------------
            # STOP LOSS
            # ------------------------------------------------

            if low_p <= stop_price:

                exit_price = stop_price
                exit_reason = "STOP_LOSS"


            # ------------------------------------------------
            # TAKE PROFIT
            # ------------------------------------------------

            elif high_p >= tp_price:

                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"


            # ------------------------------------------------
            # TRAILING STOP
            # ------------------------------------------------

            elif (
                trail_price is not None
                and previous_peak > buy_price
                and low_p <= trail_price
            ):

                exit_price = trail_price
                exit_reason = "TRAILING_STOP"


            # ------------------------------------------------
            # EXIT
            # ------------------------------------------------

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

                holding_days = (
                    pd.Timestamp(current_date)
                    - pd.Timestamp(entry_date)
                ).days

                trades.append({
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "holding_days": holding_days,
                    "buy_price": buy_price,
                    "sell_price": exit_price,
                    "NetReturn": net_return * 100,
                    "Profit": profit,
                    "SellReason": exit_reason,
                })


                if exit_reason == "STOP_LOSS":
                    stop_loss_exit_count += 1

                elif exit_reason == "TAKE_PROFIT":
                    take_profit_exit_count += 1

                elif exit_reason == "TRAILING_STOP":
                    trailing_stop_exit_count += 1


            else:

                # ------------------------------------------------
                # 오늘 장중 HIGH는 오늘 장 종료 후 peak으로 반영.
                # ------------------------------------------------

                pos["peak_price"] = max(
                    previous_peak,
                    high_p
                )

                remaining_positions.append(pos)


        positions = remaining_positions


        # ====================================================
        # 2. SELL SIGNAL EXIT
        #
        # current_date SELL signal
        # -> next trading day OPEN 매도
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

                        remaining_positions.append(pos)
                        continue


                    next_ohlcv = ohlcv_dict.get(
                        (ticker, next_date)
                    )

                    if next_ohlcv is None:

                        remaining_positions.append(pos)
                        continue


                    next_open = next_ohlcv[0]

                    if next_open <= 0:

                        remaining_positions.append(pos)
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

                    holding_days = (
                        pd.Timestamp(next_date)
                        - pd.Timestamp(pos["entry_date"])
                    ).days

                    trades.append({
                        "ticker": ticker,
                        "entry_date": pos["entry_date"],
                        "exit_date": next_date,
                        "holding_days": holding_days,
                        "buy_price": buy_price,
                        "sell_price": next_open,
                        "NetReturn": net_return * 100,
                        "Profit": profit,
                        "SellReason": "SELL_SIGNAL",
                    })

                    sell_signal_exit_count += 1


                positions = remaining_positions


        # ====================================================
        # 3. BUY SIGNAL
        #
        # current_date BUY signal
        # -> next trading day OPEN 매수
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
                key=lambda x: x.BUY_PROBABILITY,
                reverse=True
            )


            available_slots = (
                TOP_N -
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

                    next_ohlcv = ohlcv_dict.get(
                        (ticker, next_date)
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

                    held_tickers.add(ticker)


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
    #
    # 시간 제한 없음.
    # 단순히 테스트 기간 종료 시점에서 청산.
    # ========================================================

    if positions:

        last_date = trading_dates[-1]

        for pos in positions:

            ticker = pos["ticker"]

            ohlcv = ohlcv_dict.get(
                (ticker, last_date)
            )

            if ohlcv is None:

                exit_price = pos["buy_price"]

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


            holding_days = (
                pd.Timestamp(last_date)
                - pd.Timestamp(pos["entry_date"])
            ).days


            trades.append({
                "ticker": ticker,
                "entry_date": pos["entry_date"],
                "exit_date": last_date,
                "holding_days": holding_days,
                "buy_price": pos["buy_price"],
                "sell_price": exit_price,
                "NetReturn": net_return * 100,
                "Profit": profit,
                "SellReason": "BACKTEST_END",
            })

            backtest_end_exit_count += 1


    # ========================================================
    # METRICS
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_curve
    )


    # --------------------------------------------------------
    # 마지막 청산 이후 실제 최종 자산
    # --------------------------------------------------------

    final_capital = cash


    # --------------------------------------------------------
    # Equity curve 마지막 값도 최종 자산과 일치하도록 수정
    # --------------------------------------------------------

    if not equity_df.empty:

        equity_df.loc[
            equity_df.index[-1],
            "equity"
        ] = final_capital


    if equity_df.empty:

        return {
            "StopLoss": stop_loss,
            "TakeProfit": take_profit,
            "TrailingStop": trailing_stop,
            "Trades": 0,
            "AverageReturn": 0,
            "MedianReturn": 0,
            "WinRate": 0,
            "MDD": 0,
            "FinalCapital": INITIAL_CAPITAL,
            "TotalReturn": 0,
            "SellSignalExits": 0,
            "StopLossExits": 0,
            "TakeProfitExits": 0,
            "TrailingStopExits": 0,
            "BacktestEndExits": 0,
            "_trades": trades_df,
            "_equity": equity_df,
        }


    # ========================================================
    # MDD
    # ========================================================

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


    # ========================================================
    # TRADE METRICS
    # ========================================================

    if trades_df.empty:

        average_return = 0
        median_return = 0
        win_rate = 0

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


    total_return = (
        final_capital /
        INITIAL_CAPITAL
        - 1
    ) * 100


    # ========================================================
    # RESULT
    # ========================================================

    return {
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

        "SellSignalExits": sell_signal_exit_count,
        "StopLossExits": stop_loss_exit_count,
        "TakeProfitExits": take_profit_exit_count,
        "TrailingStopExits": trailing_stop_exit_count,
        "BacktestEndExits": backtest_end_exit_count,

        "_trades": trades_df,
        "_equity": equity_df,
    }


# ============================================================
# PARAMETER SWEEP
# ============================================================

print()
print("=" * 70)
print("BUY + SELL + RISK MANAGEMENT PARAMETER SWEEP")
print("=" * 70)

results = []

parameter_combinations = list(
    product(
        STOP_LOSS_LIST,
        TAKE_PROFIT_LIST,
        TRAILING_STOP_LIST,
    )
)


for (
    stop_loss,
    take_profit,
    trailing_stop
) in parameter_combinations:

    print(
        f"STOP={stop_loss * 100:5.1f}% "
        f"TP={take_profit * 100:5.1f}% "
        f"TRAIL={trailing_stop * 100:5.1f}%"
    )

    result = run_backtest(
        stop_loss,
        take_profit,
        trailing_stop
    )

    results.append({
        key: value
        for key, value in result.items()
        if not key.startswith("_")
    })


# ============================================================
# RESULTS
# ============================================================

results_df = pd.DataFrame(
    results
)

results_df = (
    results_df
    .sort_values(
        "TotalReturn",
        ascending=False
    )
    .reset_index(drop=True)
)


print()
print("=" * 70)
print("Risk Management 결과")
print("=" * 70)

print(
    results_df.to_string(
        index=False
    )
)


# ============================================================
# MDD -30%
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


# ============================================================
# MDD -40%
# ============================================================

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
# BEST STRATEGY
# ============================================================

best = results_df.iloc[0]


print()
print("=" * 70)
print("BEST STRATEGY")
print("=" * 70)

print(
    f"Stop Loss     : "
    f"{best['StopLoss'] * 100:.1f}%"
)

print(
    f"Take Profit   : "
    f"{best['TakeProfit'] * 100:.1f}%"
)

print(
    f"Trailing Stop : "
    f"{best['TrailingStop'] * 100:.1f}%"
)

print(
    f"Trades        : "
    f"{int(best['Trades'])}"
)

print(
    f"Win Rate      : "
    f"{best['WinRate']:.2f}%"
)

print(
    f"Average Return: "
    f"{best['AverageReturn']:.4f}%"
)

print(
    f"Median Return : "
    f"{best['MedianReturn']:.4f}%"
)

print(
    f"MDD           : "
    f"{best['MDD']:.2f}%"
)

print(
    f"Final Capital : "
    f"{best['FinalCapital']:,.0f}"
)

print(
    f"Total Return  : "
    f"{best['TotalReturn']:.2f}%"
)

print()
print("Exit Breakdown")
print(
    f"SELL SIGNAL   : "
    f"{int(best['SellSignalExits'])}"
)

print(
    f"STOP LOSS     : "
    f"{int(best['StopLossExits'])}"
)

print(
    f"TAKE PROFIT   : "
    f"{int(best['TakeProfitExits'])}"
)

print(
    f"TRAILING STOP : "
    f"{int(best['TrailingStopExits'])}"
)

print(
    f"BACKTEST END  : "
    f"{int(best['BacktestEndExits'])}"
)


# ============================================================
# BEST STRATEGY DETAILED BACKTEST
# ============================================================

print()
print("=" * 70)
print("BEST STRATEGY 상세 백테스트 저장")
print("=" * 70)

best_result = run_backtest(
    best["StopLoss"],
    best["TakeProfit"],
    best["TrailingStop"],
)

best_trades = best_result["_trades"]
best_equity = best_result["_equity"]


# ============================================================
# HOLDING PERIOD ANALYSIS
# ============================================================

if not best_trades.empty:

    print()
    print("=" * 70)
    print("BEST STRATEGY 보유기간 분석")
    print("=" * 70)

    holding_stats = (
        best_trades
        .groupby("SellReason")
        .agg(
            Trades=("NetReturn", "count"),
            AverageHoldingDays=(
                "holding_days",
                "mean"
            ),
            MedianHoldingDays=(
                "holding_days",
                "median"
            ),
            AverageReturn=(
                "NetReturn",
                "mean"
            ),
            MedianReturn=(
                "NetReturn",
                "median"
            ),
            WinRate=(
                "NetReturn",
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


# ============================================================
# SAVE
# ============================================================

best_trades.to_csv(
    "buy_best_strategy_trades.csv",
    index=False
)

best_equity.to_csv(
    "buy_best_strategy_equity.csv",
    index=False
)

results_df.to_csv(
    "buy_sell_risk_management_sweep.csv",
    index=False
)

if not best_trades.empty:

    holding_stats.to_csv(
        "buy_best_strategy_holding_stats.csv",
        index=False
    )


print()
print(
    "buy_best_strategy_trades.csv"
)

print(
    "buy_best_strategy_equity.csv"
)

print(
    "buy_sell_risk_management_sweep.csv"
)

if not best_trades.empty:

    print(
        "buy_best_strategy_holding_stats.csv"
    )

print()
print("=" * 70)
print("저장 완료")
print("=" * 70)