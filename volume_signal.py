import pandas as pd

df = pd.read_csv("raw/005930.csv")

# 최근 20일 평균 거래량
df["평균거래량20"] = df["거래량"].rolling(20).mean()

# 거래량 비율
df["거래량비율"] = df["거래량"] / df["평균거래량20"]

# 거래량이 3배 이상인 날
signal_df = df[df["거래량비율"] >= 3]

print(signal_df[["날짜", "거래량", "평균거래량20", "거래량비율"]])