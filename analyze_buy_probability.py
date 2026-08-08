import pandas as pd
import numpy as np
from xgboost import XGBClassifier

BUY_FEATURES = [
    "거래량비율","MA20비율","MA60비율","MA120비율","RSI","HIGH20비율",
    "Volatility20","Momentum20","HIGH252비율","MA20_MA60_Gap","MA60_MA120_Gap",
    "BollingerPosition","MACD","MACDSignal","MACDHistogram","ATR","OBV","ADX",
    "MFI","CMF","CCI","DonchianPosition","VWAPRatio","ATRRatio","VolumeSpike",
    "LOW20비율","Position60","Position120"
]

HOLD_DAYS = [5, 10, 20, 40]
TRADING_COST = 0.003

print("Dataset Loading...")

df = pd.read_csv("dataset.csv", low_memory=False)
df["날짜"] = pd.to_datetime(df["날짜"])
df["ticker"] = df["ticker"].astype(str).str.zfill(6)
df = df.sort_values(["날짜", "ticker"]).reset_index(drop=True)

dates = sorted(df["날짜"].drop_duplicates())
split_date = dates[int(len(dates) * 0.80)]

train = df[df["날짜"] < split_date].copy()
test = df[df["날짜"] >= split_date].copy()

print(f"Dataset: {len(df):,}")
print(f"Train: {len(train):,}")
print(f"Test : {len(test):,}")
print(f"Train 기간: {train['날짜'].min().date()} ~ {train['날짜'].max().date()}")
print(f"Test 기간 : {test['날짜'].min().date()} ~ {test['날짜'].max().date()}")

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)

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
    colsample_bytree=1.0,
)

model.fit(train[BUY_FEATURES], train["Target"])

print("BUY Probability 계산 중...")

test["BUY_PROBABILITY"] = model.predict_proba(
    test[BUY_FEATURES]
)[:, 1]

test_dates = sorted(test["날짜"].drop_duplicates())
date_index = {d: i for i, d in enumerate(test_dates)}

raw_cache = {}

def load_raw(ticker):
    ticker = str(ticker).zfill(6)
    if ticker in raw_cache:
        return raw_cache[ticker]

    try:
        raw = pd.read_csv(f"raw/{ticker}.csv", low_memory=False)
    except FileNotFoundError:
        raw_cache[ticker] = None
        return None

    raw["날짜"] = pd.to_datetime(raw["날짜"])
    raw = raw.sort_values("날짜").drop_duplicates("날짜").set_index("날짜")
    raw_cache[ticker] = raw
    return raw

def get_price(ticker, date, column):
    raw = load_raw(ticker)
    if raw is None or date not in raw.index:
        return None

    value = raw.loc[date, column]
    if isinstance(value, pd.Series):
        value = value.iloc[0]

    if pd.isna(value):
        return None

    value = float(value)
    return value if value > 0 else None

def future_date(date, days):
    index = date_index.get(date)
    if index is None:
        return None

    target = index + days
    if target >= len(test_dates):
        return None

    return test_dates[target]

print()
print("=" * 70)
print("BUY Probability 구간별 미래 수익률 분석")
print("=" * 70)

rows = []

for row in test.itertuples(index=False):
    signal_date = row.날짜
    ticker = row.ticker

    buy_date = future_date(signal_date, 1)
    if buy_date is None:
        continue

    buy_price = get_price(ticker, buy_date, "시가")
    if buy_price is None:
        continue

    result = {
        "SignalDate": signal_date,
        "BuyDate": buy_date,
        "ticker": ticker,
        "name": getattr(row, "name", ticker),
        "BUY_PROBABILITY": float(row.BUY_PROBABILITY),
    }

    for days in HOLD_DAYS:
        sell_date = future_date(buy_date, days)

        if sell_date is None:
            result[f"Return{days}D"] = np.nan
            continue

        sell_price = get_price(ticker, sell_date, "종가")

        if sell_price is None:
            result[f"Return{days}D"] = np.nan
            continue

        result[f"Return{days}D"] = (
            (sell_price - buy_price) / buy_price * 100
            - TRADING_COST * 100
        )

    rows.append(result)

analysis = pd.DataFrame(rows)

bins = [0.00, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01]
labels = [
    "0.00~0.50","0.50~0.60","0.60~0.70","0.70~0.75",
    "0.75~0.80","0.80~0.85","0.85~0.90","0.90~0.95","0.95~1.00"
]

analysis["ProbabilityRange"] = pd.cut(
    analysis["BUY_PROBABILITY"],
    bins=bins,
    labels=labels,
    right=False
)

range_rows = []

for probability_range, group in analysis.groupby(
    "ProbabilityRange",
    observed=False
):
    result = {
        "ProbabilityRange": str(probability_range),
        "Samples": len(group),
        "AverageProbability": group["BUY_PROBABILITY"].mean()
    }

    for days in HOLD_DAYS:
        returns = group[f"Return{days}D"].dropna()

        result[f"Trades{days}D"] = len(returns)

        if len(returns):
            result[f"AverageReturn{days}D"] = returns.mean()
            result[f"MedianReturn{days}D"] = returns.median()
            result[f"WinRate{days}D"] = (returns > 0).mean() * 100
        else:
            result[f"AverageReturn{days}D"] = np.nan
            result[f"MedianReturn{days}D"] = np.nan
            result[f"WinRate{days}D"] = np.nan

    range_rows.append(result)

range_summary = pd.DataFrame(range_rows)

thresholds = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
threshold_rows = []

for threshold in thresholds:
    filtered = analysis[
        analysis["BUY_PROBABILITY"] >= threshold
    ]

    result = {
        "Threshold": threshold,
        "Samples": len(filtered)
    }

    for days in HOLD_DAYS:
        returns = filtered[f"Return{days}D"].dropna()

        result[f"Trades{days}D"] = len(returns)

        if len(returns):
            result[f"AverageReturn{days}D"] = returns.mean()
            result[f"MedianReturn{days}D"] = returns.median()
            result[f"WinRate{days}D"] = (returns > 0).mean() * 100
        else:
            result[f"AverageReturn{days}D"] = np.nan
            result[f"MedianReturn{days}D"] = np.nan
            result[f"WinRate{days}D"] = np.nan

    threshold_rows.append(result)

threshold_summary = pd.DataFrame(threshold_rows)

print()
print("=" * 70)
print("Probability 구간별 결과")
print("=" * 70)
print(range_summary.to_string(index=False))

print()
print("=" * 70)
print("Threshold별 누적 결과")
print("=" * 70)
print(threshold_summary.to_string(index=False))

analysis.to_csv(
    "buy_probability_analysis.csv",
    index=False,
    encoding="utf-8-sig"
)

range_summary.to_csv(
    "buy_probability_range_summary.csv",
    index=False,
    encoding="utf-8-sig"
)

threshold_summary.to_csv(
    "buy_probability_threshold_summary.csv",
    index=False,
    encoding="utf-8-sig"
)

print()
print("=" * 70)
print("저장 완료")
print("=" * 70)
print("buy_probability_analysis.csv")
print("buy_probability_range_summary.csv")
print("buy_probability_threshold_summary.csv")
