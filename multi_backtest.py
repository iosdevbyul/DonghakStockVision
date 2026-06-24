import pandas as pd
from backtest_volume_signal import run_backtest

# tickers.csv 읽기
ticker_df = pd.read_csv("tickers.csv", dtype={"ticker": str})

# ticker 컬럼을 리스트로 변환
tickers = ticker_df["ticker"].tolist()

for ticker in tickers:

    result = run_backtest(ticker)

    stock_name = ticker_df.loc[
        ticker_df["ticker"] == ticker,
        "name"
    ].values[0]

    print(
        f"{stock_name} | "
        f"거래수:{result['trade_count']} | "
        f"승률:{result['win_rate']:.2f}% | "
        f"평균수익률:{result['average_return']:.2f}%"
    )