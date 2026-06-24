from pykrx import stock

ticker = "005930"

df = stock.get_market_ohlcv_by_date(
    "20100101",
    "20260622",
    ticker
)

df.to_csv("raw/005930.csv")

print("저장 완료!")