import pandas as pd
import time

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

start = time.time()

# 데이터 읽기
SAMPLE_SIZE = 500_000

df = pd.read_csv("dataset.csv")

df = df.sample(
    n=SAMPLE_SIZE,
    random_state=42
)
# Label 생성
df["상승"] = (df["20일후수익률"] > 0).astype(int)

# 입력 데이터(Feature)
X = df[
    [
        "거래량비율",
        "MA20비율",
        "MA60비율",
        "MA120비율",
        "RSI",
        "HIGH20비율",
        "Volatility20",
        "Momentum20"
    ]
]

# 정답(Label)
y = df["상승"]

# 학습용 / 테스트용 분리
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

# AI 모델 생성
# model = RandomForestClassifier(
#     random_state=42
# )
# model = RandomForestClassifier(
#     n_estimators=300,
#     max_depth=10,
#     min_samples_leaf=10,
#     random_state=42,
#     n_jobs=-1
# )
print("min_samples_leaf 값 :50")
model = RandomForestClassifier(
    n_estimators=300,
    random_state=42,
    n_jobs=-1,
    max_depth = 20,
    min_samples_leaf = 50
)

# 학습
model.fit(X_train, y_train)

# 예측
predictions = model.predict(X_test)

# 정확도
accuracy = accuracy_score(
    y_test,
    predictions
)

print(f"정확도 : {accuracy * 100:.2f}%")

print("===== Feature Importance =====")

feature_names = X.columns

for name, importance in zip(
    feature_names,
    model.feature_importances_
):
    print(f"{name}: {importance:.4f}")

model.fit(X_train, y_train)

end = time.time()

print(f"학습시간 : {end - start:.2f}초")