import pandas as pd
import numpy as np
import xgboost as xgb
from pykrx import stock

BUY_DATASET_PATH = "dataset.csv"
SELL_DATASET_PATH = "sell_dataset_v2.csv"

# IMPORTANT:
# These model filenames must match the model files in your project.
BUY_MODEL_PATH = "buy_xgboost.json"
SELL_MODEL_PATH = "sell_xgboost_v2.json"

TEST_START = pd.Timestamp("2023-04-13")
TEST_END = pd.Timestamp("2026-06-23")

BUY_THRESHOLD = 0.95
SELL_THRESHOLD = 0.50
TOP_N = 10

STOP_LOSS = -0.03
TAKE_PROFIT = 0.20
TRAILING_STOP = 0.03

INITIAL_CAPITAL = 10_000_000
OHLCV_EXTRA_DAYS = 60


def find_ticker_column(df):
    if "ticker" in df.columns:
        return "ticker"
    if "종목코드" in df.columns:
        return "종목코드"
    raise ValueError("종목 코드 컬럼을 찾을 수 없습니다.")


def normalize_ticker(df, column):
    df[column] = (
        df[column].astype(str)
        .str.replace(".0", "", regex=False)
        .str.zfill(6)
    )
    return df


def model_features(model):
    names = model.get_booster().feature_names
    if names is None:
        raise ValueError("XGBoost 모델에 feature name 정보가 없습니다.")
    return names


print("=" * 70)
print("BUY + SELL INTEGRATED BACKTEST")
print("=" * 70)

# ============================================================
# DATA
# ============================================================

buy_df = pd.read_csv(BUY_DATASET_PATH, low_memory=False)
sell_df = pd.read_csv(SELL_DATASET_PATH, low_memory=False)

buy_ticker_col = find_ticker_column(buy_df)
sell_ticker_col = find_ticker_column(sell_df)

buy_df["날짜"] = pd.to_datetime(buy_df["날짜"])
sell_df["날짜"] = pd.to_datetime(sell_df["날짜"])

normalize_ticker(buy_df, buy_ticker_col)
normalize_ticker(sell_df, sell_ticker_col)

buy_df = buy_df[
    (buy_df["날짜"] >= TEST_START) &
    (buy_df["날짜"] <= TEST_END)
].copy()

sell_df = sell_df[
    (sell_df["날짜"] >= TEST_START) &
    (sell_df["날짜"] <= TEST_END)
].copy()

print(f"BUY Test  : {len(buy_df):,}")
print(f"SELL Test : {len(sell_df):,}")

# ============================================================
# MODELS
# ============================================================

print()
print("=" * 70)
print("MODEL LOADING")
print("=" * 70)

buy_model = xgb.XGBClassifier()
buy_model.load_model(BUY_MODEL_PATH)

sell_model = xgb.XGBClassifier()
sell_model.load_model(SELL_MODEL_PATH)

buy_features = model_features(buy_model)
sell_features = model_features(sell_model)

missing_buy = [c for c in buy_features if c not in buy_df.columns]
missing_sell = [c for c in sell_features if c not in sell_df.columns]

if missing_buy:
    raise ValueError(f"BUY feature missing: {missing_buy}")

if missing_sell:
    raise ValueError(f"SELL feature missing: {missing_sell}")

print(f"BUY Feature 수  : {len(buy_features)}")
print(f"SELL Feature 수 : {len(sell_features)}")

# ============================================================
# PROBABILITIES
# ============================================================

print()
print("Probability 계산...")

buy_df["BUY_Probability"] = buy_model.predict_proba(
    buy_df[buy_features]
)[:, 1]

sell_df["SELL_Probability"] = sell_model.predict_proba(
    sell_df[sell_features]
)[:, 1]

# BUY threshold + daily TOP_N
buy_signals = buy_df[
    buy_df["BUY_Probability"] >= BUY_THRESHOLD
].copy()

buy_signals["rank"] = (
    buy_signals.groupby("날짜")["BUY_Probability"]
    .rank(method="first", ascending=False)
)

buy_signals = buy_signals[
    buy_signals["rank"] <= TOP_N
].copy()

print(f"BUY Threshold : {BUY_THRESHOLD:.2f}")
print(f"BUY Signals   : {len(buy_signals):,}")

# SELL lookup
sell_signal_map = (
    sell_df[
        sell_df["SELL_Probability"] >= SELL_THRESHOLD
    ]
    .set_index(["날짜", sell_ticker_col])["SELL_Probability"]
    .to_dict()
)

print(f"SELL Threshold: {SELL_THRESHOLD:.2f}")
print(f"SELL Signals  : {len(sell_signal_map):,}")

# ============================================================
# OHLCV
# ============================================================

tickers = sorted(buy_signals[buy_ticker_col].unique())

print()
print("=" * 70)
print("pykrx OHLCV LOADING")
print("=" * 70)
print(f"종목 수 : {len(tickers):,}")

ohlcv_dict = {}

end_date = (
    TEST_END + pd.Timedelta(days=OHLCV_EXTRA_DAYS)
).strftime("%Y%m%d")
start_date = TEST_START.strftime("%Y%m%d")

for i, ticker in enumerate(tickers, 1):
    try:
        ohlcv = stock.get_market_ohlcv(
            start_date,
            end_date,
            ticker
        )

        if not ohlcv.empty:
            ohlcv.index = pd.to_datetime(ohlcv.index)

            for date, row in ohlcv.iterrows():
                values = [
                    row["시가"],
                    row["고가"],
                    row["저가"],
                    row["종가"],
                ]

                if any(pd.isna(v) for v in values):
                    continue

                ohlcv_dict[(ticker, date)] = {
                    "open": float(row["시가"]),
                    "high": float(row["고가"]),
                    "low": float(row["저가"]),
                    "close": float(row["종가"]),
                }

    except Exception as e:
        print(f"OHLCV 실패: {ticker} / {e}")

    if i % 100 == 0:
        print(f"Loading: {i} / {len(tickers)}")

print(f"OHLCV rows : {len(ohlcv_dict):,}")

# ticker별 거래일
ticker_dates = {}

for ticker, date in ohlcv_dict:
    ticker_dates.setdefault(ticker, []).append(date)

for ticker in ticker_dates:
    ticker_dates[ticker] = sorted(set(ticker_dates[ticker]))

# 날짜별 BUY signal
buy_by_date = {}

for row in buy_signals.itertuples(index=False):
    ticker = getattr(row, buy_ticker_col)
    buy_by_date.setdefault(row.날짜, []).append(
        (ticker, row.BUY_Probability)
    )

# ============================================================
# BACKTEST
# ============================================================

print()
print("=" * 70)
print("INTEGRATED BACKTEST")
print("=" * 70)

cash = float(INITIAL_CAPITAL)
positions = {}
trades = []
equity = []

trading_dates = sorted({
    date
    for (ticker, date) in ohlcv_dict
    if TEST_START <= date <= TEST_END
})

for current_date in trading_dates:

    # --------------------------------------------------------
    # EXIT
    # --------------------------------------------------------
    for ticker in list(positions):

        pos = positions[ticker]
        data = ohlcv_dict.get((ticker, current_date))

        if data is None:
            continue

        entry = pos["entry_price"]
        high = data["high"]
        low = data["low"]

        pos["highest_price"] = max(
            pos["highest_price"],
            high
        )

        stop_price = entry * (1 + STOP_LOSS)
        tp_price = entry * (1 + TAKE_PROFIT)
        trail_price = pos["highest_price"] * (1 - TRAILING_STOP)

        exit_reason = None
        exit_price = None

        # Conservative priority when one day's range hits
        # multiple levels.
        if low <= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price = stop_price

        elif high >= tp_price:
            exit_reason = "TAKE_PROFIT"
            exit_price = tp_price

        elif low <= trail_price:
            exit_reason = "TRAILING_STOP"
            exit_price = trail_price

        # SELL signal was generated on previous close.
        if (
            exit_reason is None
            and pos.get("sell_signal_date") == current_date
        ):
            exit_reason = "SELL_SIGNAL"
            exit_price = data["open"]

        if exit_reason is None:
            continue

        proceeds = pos["shares"] * exit_price
        cash += proceeds

        ret = (exit_price / entry - 1) * 100

        trades.append({
            "ticker": ticker,
            "entry_date": pos["entry_date"],
            "exit_date": current_date,
            "entry_price": entry,
            "exit_price": exit_price,
            "shares": pos["shares"],
            "BUY_Probability": pos["BUY_Probability"],
            "SELL_Probability": pos.get("SELL_Probability", np.nan),
            "return": ret,
            "holding_days": (current_date - pos["entry_date"]).days,
            "sell_reason": exit_reason,
        })

        del positions[ticker]

    # --------------------------------------------------------
    # REGISTER SELL SIGNAL FOR NEXT TRADING DAY
    # --------------------------------------------------------
    for ticker in list(positions):

        pos = positions[ticker]

        sell_probability = sell_signal_map.get(
            (current_date, ticker)
        )

        if sell_probability is None:
            continue

        dates = ticker_dates.get(ticker, [])

        try:
            idx = dates.index(current_date)
        except ValueError:
            continue

        if idx + 1 >= len(dates):
            continue

        next_date = dates[idx + 1]

        if next_date <= TEST_END:
            pos["sell_signal_date"] = next_date
            pos["SELL_Probability"] = sell_probability

    # --------------------------------------------------------
    # BUY: close signal -> next trading day's OPEN
    # --------------------------------------------------------
    for ticker, probability in buy_by_date.get(current_date, []):

        if ticker in positions:
            continue

        if len(positions) >= TOP_N:
            break

        dates = ticker_dates.get(ticker, [])

        try:
            idx = dates.index(current_date)
        except ValueError:
            continue

        if idx + 1 >= len(dates):
            continue

        entry_date = dates[idx + 1]

        if entry_date > TEST_END:
            continue

        next_data = ohlcv_dict.get((ticker, entry_date))

        if next_data is None:
            continue

        entry_price = next_data["open"]

        if entry_price <= 0 or cash <= 0:
            continue

        slots = TOP_N - len(positions)
        allocation = cash / slots
        shares = allocation / entry_price
        cost = shares * entry_price

        cash -= cost

        positions[ticker] = {
            "entry_date": entry_date,
            "entry_price": entry_price,
            "shares": shares,
            "BUY_Probability": probability,
            "SELL_Probability": np.nan,
            "highest_price": entry_price,
            "sell_signal_date": None,
        }

    # --------------------------------------------------------
    # EQUITY
    # --------------------------------------------------------
    value = cash

    for ticker, pos in positions.items():
        data = ohlcv_dict.get((ticker, current_date))
        if data is not None:
            value += pos["shares"] * data["close"]

    equity.append({
        "date": current_date,
        "cash": cash,
        "positions": len(positions),
        "equity": value,
    })

# ============================================================
# FORCE CLOSE AT TEST END
# ============================================================

for ticker in list(positions):

    pos = positions[ticker]
    dates = [
        d for d in ticker_dates.get(ticker, [])
        if d <= TEST_END
    ]

    if not dates:
        continue

    exit_date = dates[-1]
    data = ohlcv_dict.get((ticker, exit_date))

    if data is None:
        continue

    exit_price = data["close"]
    cash += pos["shares"] * exit_price

    trades.append({
        "ticker": ticker,
        "entry_date": pos["entry_date"],
        "exit_date": exit_date,
        "entry_price": pos["entry_price"],
        "exit_price": exit_price,
        "shares": pos["shares"],
        "BUY_Probability": pos["BUY_Probability"],
        "SELL_Probability": pos.get("SELL_Probability", np.nan),
        "return": (exit_price / pos["entry_price"] - 1) * 100,
        "holding_days": (exit_date - pos["entry_date"]).days,
        "sell_reason": "BACKTEST_END",
    })

    del positions[ticker]

# ============================================================
# RESULTS
# ============================================================

trades_df = pd.DataFrame(trades)
equity_df = pd.DataFrame(equity)

if equity_df.empty:
    raise RuntimeError("Equity 결과가 없습니다.")

equity_df["peak"] = equity_df["equity"].cummax()
equity_df["drawdown"] = (
    equity_df["equity"] / equity_df["peak"] - 1
)

mdd = equity_df["drawdown"].min() * 100
final_capital = cash
total_return = (
    final_capital / INITIAL_CAPITAL - 1
) * 100

print()
print("=" * 70)
print("RESULT")
print("=" * 70)

print(f"BUY Threshold  : {BUY_THRESHOLD:.2f}")
print(f"SELL Threshold : {SELL_THRESHOLD:.2f}")
print(f"TOP_N          : {TOP_N}")
print(f"Stop Loss      : {STOP_LOSS * 100:.1f}%")
print(f"Take Profit    : {TAKE_PROFIT * 100:.1f}%")
print(f"Trailing Stop  : {TRAILING_STOP * 100:.1f}%")
print()

print(f"Trades         : {len(trades_df):,}")

if not trades_df.empty:
    print(
        f"Average Return: "
        f"{trades_df['return'].mean():.4f}%"
    )
    print(
        f"Median Return : "
        f"{trades_df['return'].median():.4f}%"
    )
    print(
        f"Win Rate      : "
        f"{(trades_df['return'] > 0).mean() * 100:.2f}%"
    )

print(f"MDD            : {mdd:.2f}%")
print(f"Initial Capital: {INITIAL_CAPITAL:,.0f}")
print(f"Final Capital  : {final_capital:,.0f}")
print(f"Total Return   : {total_return:.2f}%")

print()
print("Exit Breakdown")

if trades_df.empty:
    print("거래 없음")
else:
    print(
        trades_df["sell_reason"]
        .value_counts()
        .to_string()
    )

# ============================================================
# SAVE
# ============================================================

trades_df.to_csv(
    "buy_sell_integrated_trades.csv",
    index=False
)

equity_df.to_csv(
    "buy_sell_integrated_equity.csv",
    index=False
)

if trades_df.empty:
    holding_stats = pd.DataFrame()
else:
    holding_stats = (
        trades_df
        .groupby("sell_reason")
        .agg(
            Trades=("return", "count"),
            AverageHoldingDays=("holding_days", "mean"),
            MedianHoldingDays=("holding_days", "median"),
            AverageReturn=("return", "mean"),
            MedianReturn=("return", "median"),
            WinRate=(
                "return",
                lambda x: (x > 0).mean() * 100
            ),
        )
        .reset_index()
    )

holding_stats.to_csv(
    "buy_sell_integrated_holding_stats.csv",
    index=False
)

pd.DataFrame([{
    "BUY_THRESHOLD": BUY_THRESHOLD,
    "SELL_THRESHOLD": SELL_THRESHOLD,
    "TOP_N": TOP_N,
    "StopLoss": STOP_LOSS,
    "TakeProfit": TAKE_PROFIT,
    "TrailingStop": TRAILING_STOP,
    "Trades": len(trades_df),
    "AverageReturn": (
        trades_df["return"].mean()
        if not trades_df.empty else np.nan
    ),
    "MedianReturn": (
        trades_df["return"].median()
        if not trades_df.empty else np.nan
    ),
    "WinRate": (
        (trades_df["return"] > 0).mean() * 100
        if not trades_df.empty else np.nan
    ),
    "MDD": mdd,
    "InitialCapital": INITIAL_CAPITAL,
    "FinalCapital": final_capital,
    "TotalReturn": total_return,
}]).to_csv(
    "buy_sell_integrated_summary.csv",
    index=False
)

print()
print("저장 완료")
print("buy_sell_integrated_trades.csv")
print("buy_sell_integrated_equity.csv")
print("buy_sell_integrated_holding_stats.csv")
print("buy_sell_integrated_summary.csv")
