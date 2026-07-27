import pandas as pd


def analyze_backtest():

    df = pd.read_csv("backtest_trades.csv")

    if df.empty:
        print("거래 데이터가 없습니다.")
        return

    total_trades = len(df)

    # 승리 / 손실 거래
    winning_trades = df[
        df["return_percent"] > 0
    ]

    losing_trades = df[
        df["return_percent"] < 0
    ]

    # 승률
    win_rate = (
        len(winning_trades)
        / total_trades
        * 100
    )

    # 평균 수익 거래
    average_win = (
        winning_trades["return_percent"].mean()
        if not winning_trades.empty
        else 0
    )

    # 평균 손실 거래
    average_loss = (
        losing_trades["return_percent"].mean()
        if not losing_trades.empty
        else 0
    )

    # 총 수익
    gross_profit = (
        winning_trades["return_percent"].sum()
    )

    # 총 손실
    gross_loss = abs(
        losing_trades["return_percent"].sum()
    )

    # Profit Factor
    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    else:
        profit_factor = 0

    # 기대값
    expected_value = (
        df["return_percent"].mean()
    )

    print()
    print(
        "========== 전체 거래 분석 =========="
    )

    print(
        f"전체 거래 수: {total_trades:,}"
    )

    print(
        f"승리 거래 수: "
        f"{len(winning_trades):,}"
    )

    print(
        f"손실 거래 수: "
        f"{len(losing_trades):,}"
    )

    print(
        f"승률: {win_rate:.2f}%"
    )

    print(
        f"평균 수익 거래: "
        f"{average_win:.2f}%"
    )

    print(
        f"평균 손실 거래: "
        f"{average_loss:.2f}%"
    )

    print(
        f"Profit Factor: "
        f"{profit_factor:.2f}"
    )

    print(
        f"거래당 기대값: "
        f"{expected_value:.2f}%"
    )

    # 청산 사유별 분석
    print()
    print(
        "========== 청산 사유별 분석 =========="
    )

    exit_summary = (
        df.groupby("exit_reason")
        .agg(
            trade_count=(
                "return_percent",
                "count"
            ),
            average_return=(
                "return_percent",
                "mean"
            ),
            total_return=(
                "return_percent",
                "sum"
            ),
            average_hold_days=(
                "hold_days",
                "mean"
            )
        )
        .sort_values(
            "trade_count",
            ascending=False
        )
    )

    print(
        exit_summary.to_string(
            float_format=lambda x:
            f"{x:.2f}"
        )
    )

    # 청산 사유별 비율
    print()
    print(
        "========== 청산 사유 비율 =========="
    )

    exit_counts = (
        df["exit_reason"]
        .value_counts()
    )

    exit_percent = (
        exit_counts
        / total_trades
        * 100
    )

    exit_summary_percent = pd.DataFrame(
        {
            "count": exit_counts,
            "percentage": exit_percent
        }
    )

    print(
        exit_summary_percent.to_string(
            float_format=lambda x:
            f"{x:.2f}%"
        )
    )


if __name__ == "__main__":

    analyze_backtest()
