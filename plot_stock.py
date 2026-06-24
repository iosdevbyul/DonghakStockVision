import pandas as pd
import matplotlib.pyplot as plt

# CSV 읽기
df = pd.read_csv("raw/005930.csv")

# 날짜 컬럼을 날짜 타입으로 변환
df["날짜"] = pd.to_datetime(df["날짜"])
# 이동평균 계산
df["MA20"] = df["종가"].rolling(20).mean()
df["MA60"] = df["종가"].rolling(60).mean()
# 그래프 크기
plt.figure(figsize=(15, 6))

plt.plot(df["날짜"], df["종가"], label="Close")
plt.plot(df["날짜"], df["MA20"], label="MA20")
plt.plot(df["날짜"], df["MA60"], label="MA60")

plt.title("Samsung Electronics")
plt.xlabel("Date")
plt.ylabel("Price")

plt.legend()
plt.grid()

plt.show()