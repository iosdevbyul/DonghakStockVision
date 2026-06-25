import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score


# 데이터 읽기
df = pd.read_csv("dataset.csv")

# Label 생성
df["상승"] = (df["20일후수익률"] > 0).astype(int)

# 입력 데이터(Feature)
X = df[
    [
        "거래량비율",
        "MA20비율"
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
model = RandomForestClassifier(
    random_state=42
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