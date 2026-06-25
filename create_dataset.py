import pandas as pd

# 한미반도체 데이터 읽기
df = pd.read_csv("raw/042700.csv")

# ==========================
# 지표 계산
# ==========================

df["MA20"] = df["종가"].rolling(20).mean()

df["평균거래량20"] = df["거래량"].rolling(20).mean()

df["거래량비율"] = df["거래량"] / df["평균거래량20"]

# MA20 대비 위치(%)
df["MA20비율"] = (
    (df["종가"] - df["MA20"])
    / df["MA20"]
    * 100
)

# ==========================
# 정답(Label)
# ==========================

HOLD_DAYS = 20

df["20일후종가"] = df["종가"].shift(-HOLD_DAYS)

df["20일후수익률"] = (
    (df["20일후종가"] - df["종가"])
    / df["종가"]
    * 100
)

# ==========================
# 학습 데이터 생성
# ==========================

dataset = df[
    [
        "날짜",
        "거래량비율",
        "MA20비율",
        "20일후수익률"
    ]
]

dataset = dataset.dropna()

dataset.to_csv(
    "dataset.csv",
    index=False
)

print(dataset.head())
print()
print(f"총 {len(dataset)}개 학습 데이터 생성")