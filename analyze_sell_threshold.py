import pandas as pd

from xgboost import XGBClassifier

from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
)

DATASET = "sell_dataset_v2.csv"
MODEL = "sell_xgboost_v2.json"

FEATURES = [
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

TARGET = "SELL_TARGET"

print("Dataset Loading...")

df = pd.read_csv(
    DATASET,
    dtype={"ticker": str},
    low_memory=False
)

df["날짜"] = pd.to_datetime(df["날짜"])

TEST_START = "2024-07-29"

test = df[df["날짜"] >= TEST_START].copy()

X_test = test[FEATURES]
y_test = test[TARGET]

print(f"Test Data : {len(test):,}")

model = XGBClassifier()

model.load_model(MODEL)

prob = model.predict_proba(X_test)[:, 1]

thresholds = [
    0.50,
    0.45,
    0.40,
    0.35,
    0.30,
    0.25,
    0.20,
    0.15,
    0.10,
]

print()
print("=" * 90)
print(
    f"{'Threshold':<10}"
    f"{'Accuracy':>12}"
    f"{'Precision':>12}"
    f"{'Recall':>12}"
    f"{'F1':>12}"
    f"{'SELL Count':>15}"
    f"{'SELL %':>10}"
)
print("=" * 90)

for threshold in thresholds:

    pred = (prob >= threshold).astype(int)

    accuracy = accuracy_score(
        y_test,
        pred
    )

    precision = precision_score(
        y_test,
        pred,
        zero_division=0
    )

    recall = recall_score(
        y_test,
        pred,
        zero_division=0
    )

    f1 = f1_score(
        y_test,
        pred,
        zero_division=0
    )

    sell_count = pred.sum()

    sell_ratio = (
        sell_count
        / len(pred)
        * 100
    )

    print(
        f"{threshold:<10.2f}"
        f"{accuracy:>12.4f}"
        f"{precision:>12.4f}"
        f"{recall:>12.4f}"
        f"{f1:>12.4f}"
        f"{sell_count:>15,}"
        f"{sell_ratio:>10.2f}%"
    )