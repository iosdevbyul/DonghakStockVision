
import glob
import os

import pandas as pd


HOLD_DAYS = 20
VOLUME_THRESHOLD = 3

STOP_LOSS = -5.0
TAKE_PROFIT = 15.0


def find_signals(df):

    # 지표 계산
    df["평균거래량20"] = (
        df["거래량"]
        .rolling(20)
        .mean()
    )

    df["거래량비율"] = (
        df["거래량"]
        / df["평균거래량20"]
    )

    df["MA20"] = (
        df["종가"]
        .rolling(20)
        .mean()
    )

    df["MA60"] = (
        df["종가"]
        .rolling(60)
        .mean()
    )

    df["HIGH20"] = (
        df["고가"]
        .rolling(20)
        .max()
        .shift(1)
    )

    # 매수 시그널
    signals = (
        (df["거래량비율"] >= VOLUME_THRESHOLD)
        & (df["종가"] > df["MA20"])
        & (df["MA20"] > df["MA60"])
        & (df["종가"] >= df["HIGH20"])
    )

    return signals


def backtest_ticker(ticker):

    file_path = f"raw/{ticker}.csv"

    df = pd.read_csv(file_path)

    # 날짜순 정렬
    df = df.sort_values("날짜").reset_index(drop=True)

    # 매수 시그널 계산
    signals = find_signals(df)

    trades = []

    index = 0

    while index < len(df):

        # 매수 시그널이 아니면 다음 날
        if not signals.iloc[index]:
            index += 1
            continue

        # 최대 보유 기간을 확인할 수 없는 경우
        if index + HOLD_DAYS >= len(df):
            break

        # 매수
        buy_date = df.loc[index, "날짜"]
        buy_price = df.loc[index, "종가"]

        stop_price = (
            buy_price
            * (1 + STOP_LOSS / 100)
        )

        take_profit_price = (
            buy_price
            * (1 + TAKE_PROFIT / 100)
        )

        sell_price = None
        sell_date = None
        exit_reason = None

        # 매수 다음 거래일부터 확인
        for holding_day in range(
            1,
            HOLD_DAYS + 1
        ):

            current_index = (
                index
                + holding_day
            )

            low_price = (
                df.loc[
                    current_index,
                    "저가"
                ]
            )

            high_price = (
                df.loc[
                    current_index,
                    "고가"
                ]
            )

            current_date = (
                df.loc[
                    current_index,
                    "날짜"
                ]
            )

            # 손절
            if low_price <= stop_price:

                sell_price = stop_price
                sell_date = current_date
                exit_reason = "stop_loss"

                break

            # 익절
            if (
                high_price
                >= take_profit_price
            ):

                sell_price = (
                    take_profit_price
                )

                sell_date = current_date
                exit_reason = "take_profit"

                break

            # 최대 보유 기간
            if holding_day == HOLD_DAYS:

                sell_price = (
                    df.loc[
                        current_index,
                        "종가"
                    ]
                )

                sell_date = current_date
                exit_reason = "max_hold"

        # 거래 결과 계산
        if sell_price is None:
            break

        return_percent = (
            (
                sell_price
                - buy_price
            )
            / buy_price
            * 100
        )

        trades.append(
            {
                "ticker": ticker,
                "buy_date": buy_date,
                "buy_price": buy_price,
                "sell_date": sell_date,
                "sell_price": sell_price,
                "return_percent": return_percent,
                "exit_reason": exit_reason,
                "hold_days": holding_day,
            }
        )

        # 포지션 종료 이후부터 다시 탐색
        index = (
            index
            + holding_day
            + 1
        )

    return trades


def run_all_backtest():

    all_trades = []

    files = glob.glob(
        "raw/*.csv"
    )

    print(
        f"전체 종목 파일 수: {len(files)}"
    )

    for file_path in files:

        ticker = (
            os.path
            .basename(file_path)
            .replace(".csv", "")
        )

        try:

            trades = backtest_ticker(
                ticker
            )

            all_trades.extend(
                trades
            )

        except Exception as e:

            print(
                f"{ticker} 처리 실패: {e}"
            )

    # 거래 결과가 없는 경우
    if not all_trades:

        print()
        print(
            "거래 결과가 없습니다."
        )

        return

    trades_df = pd.DataFrame(
        all_trades
    )

    # 전체 거래 통계
    total_trades = len(
        trades_df
    )

    win_count = (
        trades_df[
            "return_percent"
        ] > 0
    ).sum()

    win_rate = (
        win_count
        / total_trades
        * 100
    )

    average_return = (
        trades_df[
            "return_percent"
        ].mean()
    )

    total_return = (
        trades_df[
            "return_percent"
        ].sum()
    )

    # 청산 사유별 통계
    exit_counts = (
        trades_df[
            "exit_reason"
        ]
        .value_counts()
    )

    print()
    print(
        "========== 백테스트 결과 =========="
    )

    print(
        f"전체 거래 수: {total_trades}"
    )

    print(
        f"승리 거래 수: {win_count}"
    )

    print(
        f"승률: {win_rate:.2f}%"
    )

    print(
        f"평균 수익률: "
        f"{average_return:.2f}%"
    )

    print(
        f"거래 수익률 합계: "
        f"{total_return:.2f}%"
    )

    print()
    print(
        "========== 청산 사유 =========="
    )

    print(
        exit_counts
    )

    # 거래 결과 저장
    trades_df.to_csv(
        "backtest_trades.csv",
        index=False
    )

    print()
    print(
        "거래 결과 저장 완료:"
        " backtest_trades.csv"
    )


if __name__ == "__main__":

    run_all_backtest()

