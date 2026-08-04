import pandas as pd
import numpy as np

from xgboost import XGBClassifier

from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
    classification_report,
)


# ============================================================
# 설정
# ============================================================

DATASET_PATH = "sell_dataset_v2.csv"

MODEL_PATH = "sell_xgboost_v2.json"
IMPORTANCE_PATH = "sell_feature_importance_v2.csv"


# ============================================================
# Feature
# ============================================================

BASE_FEATURES = [
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


SELL_FEATURES_V2 = [
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


FEATURES = BASE_FEATURES + SELL_FEATURES_V2


TARGET = "SELL_TARGET"


# ============================================================
# Dataset
# ============================================================

print("SELL Dataset v2 불러오는 중...")

df = pd.read_csv(
    DATASET_PATH,
    dtype={
        "ticker": str
    },
    low_memory=False
)

print(
    f"전체 데이터: {len(df):,}"
)


# ============================================================
# 날짜
# ============================================================

df["날짜"] = pd.to_datetime(
    df["날짜"]
)


# ============================================================
# Label 확인
# ============================================================

print()
print("=" * 60)
print("전체 Label 분포")
print("=" * 60)

print(
    df[TARGET].value_counts()
)

print()

print(
    df[TARGET]
    .value_counts(
        normalize=True
    )
    * 100
)


# ============================================================
# 필요한 컬럼 확인
# ============================================================

missing_features = [
    feature
    for feature in FEATURES
    if feature not in df.columns
]

if missing_features:

    raise ValueError(
        "Missing features: "
        + ", ".join(missing_features)
    )


# ============================================================
# Train / Test
# ============================================================

TRAIN_END_DATE = "2024-07-29"


train = df[
    df["날짜"] < TRAIN_END_DATE
].copy()

test = df[
    df["날짜"] >= TRAIN_END_DATE
].copy()


print()
print("=" * 60)
print("Train / Test")
print("=" * 60)

print(
    f"Train: {len(train):,}"
)

print(
    f"Test : {len(test):,}"
)

print()

print(
    f"Train 시작: "
    f"{train['날짜'].min().date()}"
)

print(
    f"Train 종료: "
    f"{train['날짜'].max().date()}"
)

print(
    f"Test 시작: "
    f"{test['날짜'].min().date()}"
)

print(
    f"Test 종료: "
    f"{test['날짜'].max().date()}"
)


# ============================================================
# X / y
# ============================================================

X_train = train[FEATURES]
y_train = train[TARGET]

X_test = test[FEATURES]
y_test = test[TARGET]


# ============================================================
# XGBoost
# ============================================================

print()
print("=" * 60)
print("SELL XGBoost v2 학습 시작")
print("=" * 60)


model = XGBClassifier(
    n_estimators=150,
    max_depth=8,
    learning_rate=0.15,

    objective="binary:logistic",

    eval_metric="logloss",

    random_state=42,

    n_jobs=-1,

    tree_method="hist",
)


model.fit(
    X_train,
    y_train
)


# ============================================================
# Prediction
# ============================================================

print()
print("예측 중...")

y_pred = model.predict(
    X_test
)

y_prob = model.predict_proba(
    X_test
)[:, 1]


# ============================================================
# Metrics
# ============================================================

accuracy = accuracy_score(
    y_test,
    y_pred
)

precision = precision_score(
    y_test,
    y_pred,
    zero_division=0
)

recall = recall_score(
    y_test,
    y_pred,
    zero_division=0
)

f1 = f1_score(
    y_test,
    y_pred,
    zero_division=0
)

roc_auc = roc_auc_score(
    y_test,
    y_prob
)


print()
print("=" * 60)
print("SELL XGBoost v2 결과")
print("=" * 60)

print(
    f"Accuracy : {accuracy:.4f}"
)

print(
    f"Precision: {precision:.4f}"
)

print(
    f"Recall   : {recall:.4f}"
)

print(
    f"F1 Score : {f1:.4f}"
)

print(
    f"ROC-AUC  : {roc_auc:.4f}"
)


# ============================================================
# Confusion Matrix
# ============================================================

cm = confusion_matrix(
    y_test,
    y_pred
)


print()
print("=" * 60)
print("Confusion Matrix")
print("=" * 60)

print(cm)


# ============================================================
# Classification Report
# ============================================================

print()
print("=" * 60)
print("Classification Report")
print("=" * 60)

print(
    classification_report(
        y_test,
        y_pred,
        target_names=[
            "NO SELL",
            "SELL"
        ],
        zero_division=0
    )
)


# ============================================================
# Feature Importance
# ============================================================

importance = pd.DataFrame({
    "feature": FEATURES,
    "importance": model.feature_importances_
})

importance = (
    importance
    .sort_values(
        "importance",
        ascending=False
    )
    .reset_index(
        drop=True
    )
)


print()
print("=" * 60)
print("Feature Importance")
print("=" * 60)

print(
    importance.to_string(
        index=False
    )
)


# ============================================================
# 저장
# ============================================================

model.save_model(
    MODEL_PATH
)

importance.to_csv(
    IMPORTANCE_PATH,
    index=False
)


print()
print("=" * 60)
print("저장 완료")
print("=" * 60)

print(
    f"모델: {MODEL_PATH}"
)

print(
    f"Feature Importance: "
    f"{IMPORTANCE_PATH}"
)