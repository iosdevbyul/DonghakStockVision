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

DATASET_PATH = "sell_dataset.csv"

TEST_RATIO = 0.2

RANDOM_STATE = 42


# ============================================================
# Feature
# ============================================================

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
]


TARGET = "SELL_TARGET"


# ============================================================
# Dataset Load
# ============================================================

print("SELL Dataset 불러오는 중...")

df = pd.read_csv(
    DATASET_PATH
)

print(
    f"전체 데이터: {len(df):,}"
)


# ============================================================
# 날짜 정렬
# ============================================================

df["날짜"] = pd.to_datetime(
    df["날짜"]
)

df = df.sort_values(
    "날짜"
).reset_index(
    drop=True
)


# ============================================================
# Feature / Target
# ============================================================

X = df[FEATURES]

y = df[TARGET].astype(int)


print()
print("=" * 60)
print("전체 Label 분포")
print("=" * 60)

print(
    y.value_counts()
)

print()

print(
    y.value_counts(
        normalize=True
    ) * 100
)


# ============================================================
# Time Series Split
# ============================================================

split_index = int(
    len(df)
    * (1 - TEST_RATIO)
)

X_train = X.iloc[
    :split_index
]

X_test = X.iloc[
    split_index:
]

y_train = y.iloc[
    :split_index
]

y_test = y.iloc[
    split_index:
]


print()
print("=" * 60)
print("Train / Test")
print("=" * 60)

print(
    f"Train: {len(X_train):,}"
)

print(
    f"Test : {len(X_test):,}"
)

print()

print(
    f"Train 시작: "
    f"{df.iloc[0]['날짜'].date()}"
)

print(
    f"Train 종료: "
    f"{df.iloc[split_index - 1]['날짜'].date()}"
)

print()

print(
    f"Test 시작: "
    f"{df.iloc[split_index]['날짜'].date()}"
)

print(
    f"Test 종료: "
    f"{df.iloc[-1]['날짜'].date()}"
)


# ============================================================
# XGBoost
# ============================================================

print()
print("=" * 60)
print("SELL XGBoost 학습 시작")
print("=" * 60)


model = XGBClassifier(

    n_estimators=150,

    max_depth=8,

    learning_rate=0.15,

    subsample=0.8,

    colsample_bytree=0.8,

    objective="binary:logistic",

    eval_metric="logloss",

    random_state=RANDOM_STATE,

    n_jobs=-1,
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

y_probability = model.predict_proba(
    X_test
)[:, 1]


# ============================================================
# Evaluation
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
    y_probability
)


# ============================================================
# 결과
# ============================================================

print()
print("=" * 60)
print("SELL XGBoost 결과")
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
            "SELL",
        ],
        zero_division=0
    )
)


# ============================================================
# Feature Importance
# ============================================================

feature_importance = pd.DataFrame({

    "feature": FEATURES,

    "importance": model.feature_importances_

})

feature_importance = (
    feature_importance
    .sort_values(
        "importance",
        ascending=False
    )
)


print()
print("=" * 60)
print("Feature Importance")
print("=" * 60)

print(
    feature_importance.to_string(
        index=False
    )
)


# ============================================================
# 저장
# ============================================================

model.save_model(
    "sell_xgboost.json"
)

feature_importance.to_csv(
    "sell_feature_importance.csv",
    index=False
)


print()
print("=" * 60)
print("저장 완료")
print("=" * 60)

print(
    "모델: sell_xgboost.json"
)

print(
    "Feature Importance: "
    "sell_feature_importance.csv"
)