import pandas as pd
import numpy as np
from xgboost import XGBClassifier

# ============================================================
# 설정
# ============================================================

BUY_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
MAX_HOLDS = [5, 10, 20, 40]
TOP_NS = [1, 3, 5]

INITIAL_CAPITAL = 10_000_000
TRADING_COST = 0.003

BUY_FEATURES = [
    "거래량비율",
    "MA20비율",
    "MA60비율",
    "MA120비율",
    "RSI",
    "HIGH20비율",
    "Volatility20",
    "Momentum20",
    "HIGH252비율",
    "MA20_MA60_Gap",
    "MA60_MA120_Gap",
    "BollingerPosition",
    "MACD",
    "MACDSignal",
    "MACDHistogram",
    "ATR",
    "OBV",
    "ADX",
    "MFI",
    "CMF",
    "CCI",
    "DonchianPosition",
    "VWAPRatio",
    "ATRRatio",
    "VolumeSpike",
    "LOW20비율",
    "Position60",
    "Position120",
]

# ============================================================
# 1. Dataset Loading
# ============================================================

print("Dataset Loading...")

df = pd.read_csv(
    "dataset.csv",
    low_memory=False,
)

df["날짜"] = pd.to_datetime(df["날짜"])
df["ticker"] = df["ticker"].astype(str).str.zfill(6)

df = (
    df
    .sort_values(["날짜", "ticker"])
    .reset_index(drop=True)
)

print(f"Dataset: {len(df):,}")

# ============================================================
# 2. 날짜 기준 Train / Test
# ============================================================

all_dates = sorted(
    df["날짜"].drop_duplicates().tolist()
)

split_index = int(
    len(all_dates) * 0.8
)

split_date = all_dates[split_index]

train = df[
    df["날짜"] < split_date
].copy()

test = df[
    df["날짜"] >= split_date
].copy()

print(
    f"Train: {len(train):,}"
)

print(
    f"Test : {len(test):,}"
)

print(
    f"Train 기간: "
    f"{train['날짜'].min().date()} ~ "
    f"{train['날짜'].max().date()}"
)

print(
    f"Test 기간 : "
    f"{test['날짜'].min().date()} ~ "
    f"{test['날짜'].max().date()}"
)

# ============================================================
# 3. BUY XGBoost
# ============================================================

print()
print("=" * 70)
print("BUY XGBoost 학습")
print("=" * 70)

buy_model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8,
    n_estimators=150,
    learning_rate=0.15,
    max_depth=8,
    colsample_bytree=1.0,
)

buy_model.fit(
    train[BUY_FEATURES],
    train["Target"],
)

# ============================================================
# 4. BUY Probability
# ============================================================

print()
print("BUY Probability 계산 중...")

test["BUY_PROBABILITY"] = (
    buy_model
    .predict_proba(
        test[BUY_FEATURES]
    )[:, 1]
)

# ============================================================
# 5. Trading Dates
# ============================================================

test_dates = sorted(
    test["날짜"]
    .drop_duplicates()
    .tolist()
)

date_index = {
    date: index
    for index, date in enumerate(test_dates)
}

print()
print(
    f"Test 거래일 수: "
    f"{len(test_dates)}"
)

# ============================================================
# 6. Raw OHLCV Cache
# ============================================================

print()
print("=" * 70)
print("Raw OHLCV Loading")
print("=" * 70)

raw_cache = {}


def load_raw(ticker):

    ticker = str(ticker).zfill(6)

    if ticker in raw_cache:
        return raw_cache[ticker]

    path = f"raw/{ticker}.csv"

    try:

        raw = pd.read_csv(
            path,
            low_memory=False,
        )

    except FileNotFoundError:

        raw_cache[ticker] = None

        return None

    raw["날짜"] = pd.to_datetime(
        raw["날짜"]
    )

    raw = (
        raw
        .sort_values("날짜")
        .drop_duplicates("날짜")
        .set_index("날짜")
    )

    raw_cache[ticker] = raw

    return raw


def get_price(
    ticker,
    date,
    column,
):

    raw = load_raw(ticker)

    if raw is None:
        return None

    if date not in raw.index:
        return None

    value = raw.loc[
        date,
        column,
    ]

    if isinstance(value, pd.Series):
        value = value.iloc[0]

    if pd.isna(value):
        return None

    value = float(value)

    if value <= 0:
        return None

    return value


def get_future_date(
    date,
    days,
):

    index = date_index.get(date)

    if index is None:
        return None

    target_index = (
        index + days
    )

    if target_index >= len(test_dates):
        return None

    return test_dates[target_index]


# ============================================================
# 7. BUY Signal
# ============================================================

print()
print("=" * 70)
print("BUY Signal")
print("=" * 70)

minimum_threshold = min(
    BUY_THRESHOLDS
)

signal_df = test[
    test["BUY_PROBABILITY"]
    >= minimum_threshold
].copy()

signal_df = (
    signal_df
    .sort_values(
        [
            "날짜",
            "BUY_PROBABILITY",
        ],
        ascending=[
            True,
            False,
        ],
    )
)

print(
    f"최소 BUY Threshold: "
    f"{minimum_threshold:.2f}"
)

print(
    f"전체 후보 Signal: "
    f"{len(signal_df):,}"
)

# ============================================================
# 8. 후보 거래 생성
# ============================================================

print()
print("BUY 후보 거래 생성 중...")

candidate_records = []

for row in signal_df.itertuples(
    index=False
):

    buy_date = get_future_date(
        row.날짜,
        1,
    )

    if buy_date is None:
        continue

    buy_price = get_price(
        row.ticker,
        buy_date,
        "시가",
    )

    if buy_price is None:
        continue

    candidate_records.append(
        {
            "SignalDate": row.날짜,
            "BuyDate": buy_date,
            "ticker": row.ticker,
            "name": getattr(
                row,
                "name",
                row.ticker,
            ),
            "BUY_PROBABILITY":
                float(
                    row.BUY_PROBABILITY
                ),
            "BuyPrice":
                buy_price,
        }
    )

candidate_df = pd.DataFrame(
    candidate_records
)

print(
    f"실제 매수 후보: "
    f"{len(candidate_df):,}"
)

# ============================================================
# 9. Parameter Sweep
# ============================================================

print()
print("=" * 70)
print("BUY Portfolio Parameter Sweep")
print("=" * 70)

results = []

for threshold in BUY_THRESHOLDS:

    threshold_df = candidate_df[
        candidate_df[
            "BUY_PROBABILITY"
        ]
        >= threshold
    ].copy()

    print()
    print(
        f"Threshold = "
        f"{threshold:.2f}"
    )

    print(
        f"Signal = "
        f"{len(threshold_df):,}"
    )

    if threshold_df.empty:
        continue

    for top_n in TOP_NS:

        # ----------------------------------------------------
        # 날짜별 BUY Probability 상위 TOP_N
        # ----------------------------------------------------

        selected_df = (
            threshold_df
            .sort_values(
                [
                    "SignalDate",
                    "BUY_PROBABILITY",
                ],
                ascending=[
                    True,
                    False,
                ],
            )
            .groupby(
                "SignalDate",
                group_keys=False,
            )
            .head(top_n)
            .copy()
        )

        for max_hold in MAX_HOLDS:

            trades = []

            # ------------------------------------------------
            # 거래 생성
            # ------------------------------------------------

            for row in selected_df.itertuples(
                index=False
            ):

                sell_date = get_future_date(
                    row.BuyDate,
                    max_hold,
                )

                forced_test_end = False

                if sell_date is None:

                    sell_date = test_dates[-1]

                    forced_test_end = True

                sell_price = get_price(
                    row.ticker,
                    sell_date,
                    "종가",
                )

                if sell_price is None:
                    continue

                gross_return = (
                    (
                        sell_price
                        - row.BuyPrice
                    )
                    / row.BuyPrice
                    * 100
                )

                net_return = (
                    gross_return
                    - TRADING_COST * 100
                )

                trades.append(
                    {
                        "SignalDate":
                            row.SignalDate,

                        "BuyDate":
                            row.BuyDate,

                        "SellDate":
                            sell_date,

                        "ticker":
                            row.ticker,

                        "name":
                            row.name,

                        "BUY_PROBABILITY":
                            row.BUY_PROBABILITY,

                        "BuyPrice":
                            row.BuyPrice,

                        "SellPrice":
                            sell_price,

                        "NetReturn":
                            net_return,

                        "ForcedTestEnd":
                            forced_test_end,
                    }
                )

            trades_df = pd.DataFrame(
                trades
            )

            if trades_df.empty:
                continue

            # ------------------------------------------------
            # Portfolio
            # ------------------------------------------------

            cash = float(
                INITIAL_CAPITAL
            )

            open_positions = []

            completed_trades = []

            equity_history = []

            event_dates = sorted(
                set(
                    trades_df[
                        "BuyDate"
                    ].tolist()
                    +
                    trades_df[
                        "SellDate"
                    ].tolist()
                )
            )

            for current_date in event_dates:

                # ==========================================
                # SELL
                # ==========================================

                remaining_positions = []

                for position in open_positions:

                    if (
                        position[
                            "SellDate"
                        ]
                        != current_date
                    ):

                        remaining_positions.append(
                            position
                        )

                        continue

                    sell_value = (
                        position["shares"]
                        *
                        position["SellPrice"]
                    )

                    sell_value *= (
                        1 - TRADING_COST
                    )

                    cash += sell_value

                    completed_trades.append(
                        position
                    )

                open_positions = (
                    remaining_positions
                )

                # ==========================================
                # BUY
                # ==========================================

                buy_rows = trades_df[
                    trades_df[
                        "BuyDate"
                    ]
                    == current_date
                ].sort_values(
                    "BUY_PROBABILITY",
                    ascending=False,
                )

                available_slots = (
                    top_n
                    - len(
                        open_positions
                    )
                )

                if (
                    available_slots > 0
                    and not buy_rows.empty
                ):

                    selected_rows = []

                    for row in buy_rows.itertuples(
                        index=False
                    ):

                        already_holding = any(
                            position[
                                "ticker"
                            ]
                            == row.ticker
                            for position
                            in open_positions
                        )

                        if already_holding:
                            continue

                        selected_rows.append(
                            row
                        )

                        if (
                            len(
                                selected_rows
                            )
                            >= available_slots
                        ):
                            break

                    # --------------------------------------
                    # Equal Weight
                    # --------------------------------------

                    if selected_rows and cash > 0:

                        allocation = (
                            cash
                            / len(
                                selected_rows
                            )
                        )

                        for row in selected_rows:

                            investment = min(
                                allocation,
                                cash,
                            )

                            investment_after_cost = (
                                investment
                                * (
                                    1
                                    - TRADING_COST
                                )
                            )

                            shares = (
                                investment_after_cost
                                / row.BuyPrice
                            )

                            if shares <= 0:
                                continue

                            cash -= investment

                            open_positions.append(
                                {
                                    "SignalDate":
                                        row.SignalDate,

                                    "BuyDate":
                                        row.BuyDate,

                                    "SellDate":
                                        row.SellDate,

                                    "ticker":
                                        row.ticker,

                                    "name":
                                        row.name,

                                    "BUY_PROBABILITY":
                                        row.BUY_PROBABILITY,

                                    "BuyPrice":
                                        row.BuyPrice,

                                    "SellPrice":
                                        row.SellPrice,

                                    "shares":
                                        shares,

                                    "invested":
                                        investment,

                                    "NetReturn":
                                        row.NetReturn,
                                }
                            )

                # ==========================================
                # EQUITY
                # ==========================================

                position_value = 0.0

                for position in open_positions:

                    current_price = get_price(
                        position[
                            "ticker"
                        ],
                        current_date,
                        "종가",
                    )

                    if current_price is None:

                        current_price = (
                            position[
                                "BuyPrice"
                            ]
                        )

                    position_value += (
                        position["shares"]
                        *
                        current_price
                    )

                equity = (
                    cash
                    + position_value
                )

                equity_history.append(
                    {
                        "Date":
                            current_date,

                        "Equity":
                            equity,
                    }
                )

            # ------------------------------------------------
            # 마지막 테스트 날짜 강제 청산
            # ------------------------------------------------

            if open_positions:

                final_date = test_dates[-1]

                for position in open_positions:

                    final_price = get_price(
                        position[
                            "ticker"
                        ],
                        final_date,
                        "종가",
                    )

                    if final_price is None:

                        final_price = (
                            position[
                                "BuyPrice"
                            ]
                        )

                    sell_value = (
                        position["shares"]
                        *
                        final_price
                    )

                    sell_value *= (
                        1 - TRADING_COST
                    )

                    cash += sell_value

                    completed_trades.append(
                        position
                    )

            # ------------------------------------------------
            # Metrics
            # ------------------------------------------------

            completed_df = pd.DataFrame(
                completed_trades
            )

            if completed_df.empty:

                average_return = np.nan
                median_return = np.nan
                win_rate = np.nan

            else:

                returns = (
                    completed_df[
                        "NetReturn"
                    ]
                )

                average_return = (
                    returns.mean()
                )

                median_return = (
                    returns.median()
                )

                win_rate = (
                    returns > 0
                ).mean() * 100

            # ------------------------------------------------
            # MDD
            # ------------------------------------------------

            if equity_history:

                equity_df = pd.DataFrame(
                    equity_history
                )

                final_equity_value = (
                    equity_df[
                        "Equity"
                    ].iloc[-1]
                )

                running_max = (
                    equity_df[
                        "Equity"
                    ].cummax()
                )

                drawdown = (
                    equity_df[
                        "Equity"
                    ]
                    / running_max
                    - 1
                )

                mdd = (
                    drawdown.min()
                    * 100
                )

            else:

                final_equity_value = (
                    INITIAL_CAPITAL
                )

                mdd = 0.0

            # ------------------------------------------------
            # 강제청산 이후 최종 자본
            # ------------------------------------------------

            final_capital = cash

            total_return = (
                (
                    final_capital
                    / INITIAL_CAPITAL
                )
                - 1
            ) * 100

            results.append(
                {
                    "Threshold":
                        threshold,

                    "TOP_N":
                        top_n,

                    "MaxHoldDays":
                        max_hold,

                    "SignalCount":
                        len(threshold_df),

                    "CandidateTrades":
                        len(trades_df),

                    "CompletedTrades":
                        len(completed_df),

                    "AverageReturn":
                        average_return,

                    "MedianReturn":
                        median_return,

                    "WinRate":
                        win_rate,

                    "MDD":
                        mdd,

                    "FinalCapital":
                        final_capital,

                    "TotalReturn":
                        total_return,
                }
            )

            print(
                f"  TOP_N={top_n:>1}, "
                f"HOLD={max_hold:>2}일 → "
                f"Return={total_return:>7.2f}%, "
                f"MDD={mdd:>7.2f}%, "
                f"Trades={len(completed_df):>5}"
            )

# ============================================================
# 10. 결과
# ============================================================

result_df = pd.DataFrame(
    results
)

print()
print("=" * 70)
print("Parameter Sweep 결과")
print("=" * 70)

result_df = (
    result_df
    .sort_values(
        "TotalReturn",
        ascending=False,
    )
    .reset_index(drop=True)
)

print(
    result_df.to_string(
        index=False
    )
)

# ============================================================
# 11. 저장
# ============================================================

result_df.to_csv(
    "buy_portfolio_parameter_sweep.csv",
    index=False,
    encoding="utf-8-sig",
)

print()
print("=" * 70)
print("수익률 상위 10개")
print("=" * 70)

print(
    result_df
    .head(10)
    .to_string(
        index=False
    )
)

# ============================================================
# 12. MDD 제한 전략
# ============================================================

print()
print("=" * 70)
print("MDD -30% 이내 전략")
print("=" * 70)

safe_df = result_df[
    result_df["MDD"] >= -30
].copy()

if safe_df.empty:

    print(
        "MDD -30% 이내 전략이 없습니다."
    )

else:

    safe_df = (
        safe_df
        .sort_values(
            "TotalReturn",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    print(
        safe_df
        .head(10)
        .to_string(
            index=False
        )
    )

# ============================================================
# 13. MDD -40% 이내 전략
# ============================================================

print()
print("=" * 70)
print("MDD -40% 이내 전략")
print("=" * 70)

safe_df = result_df[
    result_df["MDD"] >= -40
].copy()

if safe_df.empty:

    print(
        "MDD -40% 이내 전략이 없습니다."
    )

else:

    safe_df = (
        safe_df
        .sort_values(
            "TotalReturn",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    print(
        safe_df
        .head(10)
        .to_string(
            index=False
        )
    )

print()
print("=" * 70)
print("저장 완료")
print("=" * 70)

print(
    "buy_portfolio_parameter_sweep.csv"
)