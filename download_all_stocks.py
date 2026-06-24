from pykrx import stock
from datetime import datetime
import pandas as pd

import FinanceDataReader as fdr

df = fdr.StockListing('KRX')
print(df.head())
print(len(df))

today = datetime.today().strftime("%Y%m%d")

krx_df = fdr.StockListing("KRX")

ticker_df = krx_df[["Code", "Name"]].rename(
    columns={
        "Code": "ticker",
        "Name": "name"
    }
)

ticker_df.to_csv(
    "tickers.csv",
    index=False
)

print(f"총 {len(ticker_df)}개 종목 저장 완료")


# CSV 읽기
tickers = pd.read_csv(
    "tickers.csv",
    dtype={"ticker": str}
)

for _, row in tickers.iterrows():

    ticker = row["ticker"]
    name = row["name"]

    try:

        print(f"{name} ({ticker}) 다운로드 중...")

        df = stock.get_market_ohlcv_by_date(
            "20100101",
            today,
            ticker
        )

        df.to_csv(f"raw/{ticker}.csv")

        print("완료")

    except Exception as e:

        print(f"{name} 실패 : {e}")