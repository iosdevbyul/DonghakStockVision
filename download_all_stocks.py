from pykrx import stock
from datetime import datetime
import pandas as pd

today = datetime.today().strftime("%Y%m%d")

# -----------------------------
# 종목 목록 생성
# -----------------------------
tickers = stock.get_market_ticker_list(
    date=today,
    market="ALL"
)

rows = []

for ticker in tickers:

    rows.append({
        "ticker": ticker,
        "name": stock.get_market_ticker_name(ticker)
    })

ticker_df = pd.DataFrame(rows)

ticker_df.to_csv(
    "tickers.csv",
    index=False
)

print(f"총 {len(ticker_df)}개 종목 저장 완료")

# -----------------------------
# OHLCV 다운로드
# -----------------------------
saved = 0
skipped = 0

for _, row in ticker_df.iterrows():

    ticker = row["ticker"]
    name = row["name"]

    try:

        print(f"{name} ({ticker}) 다운로드 중...")

        df = stock.get_market_ohlcv_by_date(
            fromdate="20100101",
            todate=today,
            ticker=ticker
        )

        # 데이터가 없는 경우
        if df.empty:
            print(" -> 데이터 없음")
            skipped += 1
            continue

        # 252일 미만 데이터는 학습에서 제외
        if len(df) < 252:
            print(f" -> 데이터 부족 ({len(df)}일)")
            skipped += 1
            continue

        df.to_csv(
            f"raw/{ticker}.csv"
        )

        saved += 1
        print(" -> 완료")

    except Exception as e:

        print(f"{name} ({ticker}) 실패 : {e}")
        skipped += 1

print()
print(f"저장 완료 : {saved}개")
print(f"제외 : {skipped}개")