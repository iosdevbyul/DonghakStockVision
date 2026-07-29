import glob
import os

import pandas as pd


# =========================
# 백테스트 설정
# =========================

INITIAL_CAPITAL = 100_000.0

POSITION_SIZE = 0.10

MAX_POSITIONS = 10

HOLD_DAYS = 20

VOLUME_THRESHOLD = 3

STOP_LOSS = -5.0

TAKE_PROFIT = 15.0


def load_stock_data(file_path):

    ticker = (
        os.path.basename(file_path)
        .replace(".csv", "")
    )

    df = pd.read_csv(file_path)

    df = df.sort_values(
        "날짜"
    ).reset_index(drop=True)

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
    df["signal"] = (
        (df["거래량비율"] >= VOLUME_THRESHOLD)
        & (df["종가"] > df["MA20"])
        & (df["MA20"] > df["MA60"])
        & (df["종가"] >= df["HIGH20"])
    )

    df["ticker"] = ticker

    return df


def build_market_data():

    files = glob.glob(
        "raw/*.csv"
    )

    print(
        f"전체 종목 파일 수: {len(files)}"
    )

    all_data = []

    for file_path in files:

        try:

            df = load_stock_data(
                file_path
            )

            all_data.append(
                df[
                    [
                        "날짜",
                        "ticker",
                        "시가",
                        "고가",
                        "저가",
                        "종가",
                        "거래량비율",
                        "signal",
                    ]
                ]
            )

        except Exception as e:

            ticker = (
                os.path.basename(file_path)
                .replace(".csv", "")
            )

            print(
                f"{ticker} 처리 실패: {e}"
            )

    if not all_data:

        return pd.DataFrame()

    market_data = pd.concat(
        all_data,
        ignore_index=True
    )

    market_data = market_data.sort_values(
        ["날짜", "ticker"]
    ).reset_index(drop=True)

    return market_data


def run_portfolio_backtest(
    market_data
):

    cash = INITIAL_CAPITAL

    positions = {}

    portfolio_history = []

    trade_history = []

    dates = sorted(
        market_data["날짜"].unique()
    )

    for date in dates:

        today_data = market_data[
            market_data["날짜"] == date
        ]

        # =========================
        # 1. 기존 포지션 관리
        # =========================

        positions_to_remove = []

        for ticker, position in positions.items():

            stock_data = today_data[
                today_data["ticker"]
                == ticker
            ]

            if stock_data.empty:

                continue

            row = stock_data.iloc[0]

            holding_days = (
                position["holding_days"]
                + 1
            )

            buy_price = position[
                "buy_price"
            ]

            stop_price = (
                buy_price
                * (1 + STOP_LOSS / 100)
            )

            take_profit_price = (
                buy_price
                * (1 + TAKE_PROFIT / 100)
            )

            sell_price = None

            exit_reason = None

            # 손절
            if row["저가"] <= stop_price:

                sell_price = stop_price

                exit_reason = (
                    "stop_loss"
                )

            # 익절
            elif (
                row["고가"]
                >= take_profit_price
            ):

                sell_price = (
                    take_profit_price
                )

                exit_reason = (
                    "take_profit"
                )

            # 최대 보유 기간
            elif holding_days >= HOLD_DAYS:

                sell_price = row[
                    "종가"
                ]

                exit_reason = (
                    "max_hold"
                )

            # 청산
            if sell_price is not None:

                shares = position[
                    "shares"
                ]

                proceeds = (
                    shares
                    * sell_price
                )

                cash += proceeds

                return_percent = (
                    (
                        sell_price
                        - buy_price
                    )
                    / buy_price
                    * 100
                )

                trade_history.append(
                    {
                        "ticker": ticker,
                        "buy_date": position[
                            "buy_date"
                        ],
                        "buy_price": buy_price,
                        "sell_date": date,
                        "sell_price": sell_price,
                        "return_percent":
                            return_percent,
                        "exit_reason":
                            exit_reason,
                        "hold_days":
                            holding_days,
                        "invested_amount":
                            position[
                                "invested_amount"
                            ],
                    }
                )

                positions_to_remove.append(
                    ticker
                )

            else:

                position[
                    "holding_days"
                ] = holding_days

        for ticker in positions_to_remove:

            del positions[ticker]

        # =========================
        # 2. 신규 매수
        # =========================

        available_slots = (
            MAX_POSITIONS
            - len(positions)
        )

        if available_slots > 0:

            candidates = today_data[
                today_data["signal"]
                == True
            ]

            # 이미 보유 중인 종목 제외
            candidates = candidates[
                ~candidates[
                    "ticker"
                ].isin(
                    positions.keys()
                )
            ]

            candidates = candidates.sort_values(
                "거래량비율",
                ascending=False
            )

            # 최대 슬롯 수만큼만 진입
            candidates = candidates.head(
                available_slots
            )

            for _, row in candidates.iterrows():

                if cash <= 0:

                    break

                ticker = row[
                    "ticker"
                ]

                buy_price = row[
                    "종가"
                ]

                # 종목당 목표 투자금
                target_amount = (
                    INITIAL_CAPITAL
                    * POSITION_SIZE
                )

                # 현금이 부족하면 남은 현금 사용
                invested_amount = min(
                    target_amount,
                    cash
                )

                shares = (
                    invested_amount
                    / buy_price
                )

                if shares <= 0:

                    continue

                cash -= (
                    shares
                    * buy_price
                )

                positions[
                    ticker
                ] = {
                    "buy_date": date,
                    "buy_price":
                        buy_price,
                    "shares": shares,
                    "invested_amount":
                        invested_amount,
                    "holding_days": 0,
                }

        # =========================
        # 3. 포트폴리오 평가
        # =========================

        portfolio_value = cash

        for ticker, position in positions.items():

            stock_data = today_data[
                today_data["ticker"]
                == ticker
            ]

            if stock_data.empty:

                continue

            current_price = stock_data.iloc[
                0
            ]["종가"]

            portfolio_value += (
                position["shares"]
                * current_price
            )

        portfolio_history.append(
            {
                "date": date,
                "cash": cash,
                "position_count":
                    len(positions),
                "portfolio_value":
                    portfolio_value,
            }
        )

    return (
        pd.DataFrame(
            portfolio_history
        ),
        pd.DataFrame(
            trade_history
        ),
    )


def calculate_metrics(
    portfolio_history,
    trade_history
):

    if portfolio_history.empty:

        return

    initial_value = (
        INITIAL_CAPITAL
    )

    final_value = (
        portfolio_history[
            "portfolio_value"
        ].iloc[-1]
    )

    total_return = (
        (
            final_value
            - initial_value
        )
        / initial_value
        * 100
    )

    # 누적 최고점
    portfolio_history[
        "peak"
    ] = portfolio_history[
        "portfolio_value"
    ].cummax()

    # Drawdown
    portfolio_history[
        "drawdown"
    ] = (
        portfolio_history[
            "portfolio_value"
        ]
        / portfolio_history[
            "peak"
        ]
        - 1
    ) * 100

    max_drawdown = (
        portfolio_history[
            "drawdown"
        ].min()
    )

    print()
    print(
        "========== 포트폴리오 백테스트 =========="
    )

    print(
        f"초기 자본: "
        f"${initial_value:,.2f}"
    )

    print(
        f"최종 자산: "
        f"${final_value:,.2f}"
    )

    print(
        f"누적 수익률: "
        f"{total_return:.2f}%"
    )

    print(
        f"최대 낙폭(MDD): "
        f"{max_drawdown:.2f}%"
    )

    if not trade_history.empty:

        total_trades = len(
            trade_history
        )

        winning_trades = (
            trade_history[
                trade_history[
                    "return_percent"
                ] > 0
            ]
        )

        win_rate = (
            len(winning_trades)
            / total_trades
            * 100
        )

        print(
            f"전체 거래 수: "
            f"{total_trades:,}"
        )

        print(
            f"승률: "
            f"{win_rate:.2f}%"
        )

        print()
        print(
            "========== 청산 사유 =========="
        )

        print(
            trade_history[
                "exit_reason"
            ].value_counts()
        )

    # 결과 저장
    portfolio_history.to_csv(
        "portfolio_history.csv",
        index=False
    )

    trade_history.to_csv(
        "portfolio_trades.csv",
        index=False
    )

    print()
    print(
        "결과 저장 완료:"
    )

    print(
        "- portfolio_history.csv"
    )

    print(
        "- portfolio_trades.csv"
    )


if __name__ == "__main__":

    market_data = (
        build_market_data()
    )

    if not market_data.empty:

        (
            portfolio_history,
            trade_history,
        ) = run_portfolio_backtest(
            market_data
        )

        calculate_metrics(
            portfolio_history,
            trade_history
        )

