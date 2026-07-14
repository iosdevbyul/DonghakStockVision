import pandas as pd
from xgboost import XGBClassifier

# 데이터 불러오기
df = pd.read_csv(
    "dataset.csv",
    low_memory=False
)

# 학습에 사용하지 않는 컬럼 제거
X = df.drop(
    columns=[
        "날짜",
        "ticker",
        "name",
        "Target",
        "20일후수익률"
    ]
)

y = df["Target"]

future_return = df["20일후수익률"]

# 모델 생성
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
    colsample_bytree=1.0
)

print("학습 중...")

model.fit(X, y)

print("예측 중...")

probability = model.predict_proba(X)[:, 1]

result = pd.DataFrame({
    "날짜": df["날짜"],
    "ticker": df["ticker"],
    "name": df["name"],
    "Probability": probability,
    "FutureReturn": future_return
})

print(result.head())


print()

print("===== Threshold Backtest =====")

for threshold in [
    0.50,
    0.55,
    0.60,
    0.65,
    0.70,
    0.75,
    0.80,
    0.85,
    0.90
]:

    selected = result[
        result["Probability"] >= threshold
    ]

    if len(selected) == 0:
        continue

    average_return = selected["FutureReturn"].mean()

    win_rate = (
        selected["FutureReturn"] > 0
    ).mean() * 100

    print(
        f"{threshold:.2f}"
        f" | Count={len(selected):6d}"
        f" | AvgReturn={average_return:7.2f}%"
        f" | WinRate={win_rate:6.2f}%"
    )