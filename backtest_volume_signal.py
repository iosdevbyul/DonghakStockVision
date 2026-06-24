import pandas as pd

HOLD_DAYS = 20
VOLUME_THRESHOLD = 3


def run_backtest(ticker):

    win_count = 0
    total_return = 0
    trade_count = 0

    df = pd.read_csv(f"raw/{ticker}.csv")

    # 지표 계산
    df["평균거래량20"] = df["거래량"].rolling(20).mean()
    df["거래량비율"] = df["거래량"] / df["평균거래량20"]

    df["MA20"] = df["종가"].rolling(20).mean()
    df["MA60"] = df["종가"].rolling(60).mean()

    df["HIGH20"] = (
        df["고가"]
        .rolling(20)
        .max()
        .shift(1)
    )

    # 시그널
    signals = df[
        (df["거래량비율"] >= VOLUME_THRESHOLD)
        & (df["종가"] > df["MA20"])
        & (df["MA20"] > df["MA60"])
        & (df["종가"] >= df["HIGH20"])
    ]

    for index in signals.index:

        if index + HOLD_DAYS >= len(df):
            continue

        buy_price = df.loc[index, "종가"]
        sell_price = df.loc[index + HOLD_DAYS, "종가"]

        return_percent = (
            (sell_price - buy_price)
            / buy_price
            * 100
        )

        trade_count += 1
        total_return += return_percent

        if return_percent > 0:
            win_count += 1

    if trade_count == 0:
        return {
            "trade_count": 0,
            "win_rate": 0,
            "average_return": 0
        }

    return {
        "trade_count": trade_count,
        "win_rate": win_count / trade_count * 100,
        "average_return": total_return / trade_count
    }


if __name__ == "__main__":
    result = run_backtest("005930")
    print(result)