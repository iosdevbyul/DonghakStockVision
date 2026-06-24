import pandas as pd

df = pd.read_csv("raw/005930.csv")

print(df.head())
print(df.columns)

df["20일평균거래량"] = df["거래량"].rolling(20).mean()

df["거래량비율"] = (
    df["거래량"] / df["20일평균거래량"]
)

print(
    df[["날짜", "거래량", "20일평균거래량", "거래량비율"]].tail()
)