import pandas as pd


# *1단계 
df = pd.read_csv("raw/042700.csv")   # 한미반도체

# 지표 계산
df["MA20"] = df["종가"].rolling(20).mean()

df["평균거래량20"] = df["거래량"].rolling(20).mean()

df["거래량비율"] = df["거래량"] / df["평균거래량20"]


# *2단계
HOLD_DAYS = 20

df["20일후종가"] = df["종가"].shift(-HOLD_DAYS)

df["20일후수익률"] = (
    (df["20일후종가"] - df["종가"])
    / df["종가"]
    * 100
)


# *3단계
dataset = df[
    [
        "날짜",
        "종가",
        "거래량",
        "거래량비율",
        "MA20",
        "20일후수익률"
    ]
]


# *4단계
dataset = dataset.dropna()


# *5단계
dataset.to_csv(
    "dataset.csv",
    index=False
)

print(dataset.head())
print()
print(f"총 {len(dataset)}개 학습 데이터 생성")
