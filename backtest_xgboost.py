import pandas as pd
from xgboost import XGBClassifier


# ==========================================
# 1. 데이터 불러오기
# ==========================================

df = pd.read_csv(
    "dataset.csv",
    low_memory=False
)


# ==========================================
# 2. Feature / Label 분리
# ==========================================

X = df.drop(
    columns=[
        "날짜",
        "ticker",
        "name",
        "Target",
        "20일후수익률"
    ]
)

y = df["Target"]


# ==========================================
# 3. Train / Test 분리
#
# 중요:
# shuffle=False
#
# 과거 데이터 -> Train
# 미래 데이터 -> Test
#
# 실제 투자 상황에 가까운 방식으로 백테스트
# ==========================================

# (
#     X_train,
#     X_test,
#     y_train,
#     y_test,
#     train_df,
#     test_df
# ) = train_test_split(
#     X,
#     y,
#     df,
#     test_size=0.2,
#     shuffle=False
# )

split_index = int(len(df) * 0.8)

X_train = X.iloc[:split_index]
X_test = X.iloc[split_index:]

y_train = y.iloc[:split_index]
y_test = y.iloc[split_index:]

train_df = df.iloc[:split_index]
test_df = df.iloc[split_index:]


print(f"Train 데이터: {len(X_train):,}개")
print(f"Test 데이터:  {len(X_test):,}개")

print()

print(
    f"Train 기간: "
    f"{train_df['날짜'].iloc[0]} ~ "
    f"{train_df['날짜'].iloc[-1]}"
)

print(
    f"Test 기간:  "
    f"{test_df['날짜'].iloc[0]} ~ "
    f"{test_df['날짜'].iloc[-1]}"
)


# ==========================================
# 4. XGBoost 모델 생성
#
# GridSearch에서 찾은 최적 파라미터 사용
# ==========================================

model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8,
    n_estimators=150,
    learning_rate=0.15,
    max_depth=8,
    colsample_bytree=1.0
)


# ==========================================
# 5. 모델 학습
# ==========================================

print()
print("학습 중...")

model.fit(
    X_train,
    y_train
)


# ==========================================
# 6. 미래 데이터 예측
# ==========================================

print("예측 중...")

probability = model.predict_proba(
    X_test
)[:, 1]


# ==========================================
# 7. 예측 결과 생성
# ==========================================

result = pd.DataFrame({
    "날짜": test_df["날짜"].values,
    "ticker": test_df["ticker"].values,
    "name": test_df["name"].values,
    "Probability": probability,
    "FutureReturn": test_df["20일후수익률"].values
})


print()

print("===== Prediction Result =====")

print(
    result.head()
)


# ==========================================
# 8. Threshold별 백테스트
# ==========================================

print()

print("===== Threshold Backtest =====")

print(
    f"{'Threshold':<10}"
    f"{'Count':<12}"
    f"{'AvgReturn':<15}"
    f"{'WinRate':<12}"
)


for threshold in [
    0.50,
    0.55,
    0.60,
    0.65,
    0.70,
    0.75,
    0.80,
    0.85,
    0.90
]:

    # 해당 Threshold 이상인 종목만 선택
    selected = result[
        result["Probability"] >= threshold
    ]


    # 선택된 종목이 없으면 건너뜀
    if len(selected) == 0:
        continue


    # 평균 20일 후 수익률
    average_return = (
        selected["FutureReturn"].mean()
    )


    # 실제 상승한 비율
    win_rate = (
        selected["FutureReturn"] > 0
    ).mean() * 100


    print(
        f"{threshold:<10.2f}"
        f"{len(selected):<12,}"
        f"{average_return:<15.2f}%"
        f"{win_rate:<12.2f}%"
    )

# ==========================================
# 9. Top N Portfolio Backtest
# ==========================================

THRESHOLD = 0.70
TOP_N = 5

print()
print("===== Top N Portfolio Backtest =====")

print(
    f"Threshold: {THRESHOLD:.2f}"
)

print(
    f"Top N: {TOP_N}"
)


# ==========================================
# 9-1. Threshold 이상인 종목 선택
# ==========================================

selected = result[
    result["Probability"] >= THRESHOLD
].copy()


# ==========================================
# 9-2. 날짜별 Probability 내림차순 정렬
# ==========================================

selected = selected.sort_values(
    by=[
        "날짜",
        "Probability"
    ],
    ascending=[
        True,
        False
    ]
)


# ==========================================
# 9-3. 날짜별 Top N 종목 선택
# ==========================================

portfolio = (
    selected
    .groupby("날짜")
    .head(TOP_N)
    .copy()
)


# ==========================================
# 9-4. 날짜별 포트폴리오 수익률 계산
# ==========================================

daily_portfolio_return = (
    portfolio
    .groupby("날짜")["FutureReturn"]
    .mean()
)


print()

print(
    f"투자 날짜 수: "
    f"{len(daily_portfolio_return):,}"
)

print(
    f"총 매수 종목 수: "
    f"{len(portfolio):,}"
)


# ==========================================
# 9-5. 전체 평균 수익률
# ==========================================

average_return = (
    daily_portfolio_return.mean()
)


# ==========================================
# 9-6. 포트폴리오 승률
# ==========================================

win_rate = (
    daily_portfolio_return > 0
).mean() * 100


print()

print(
    f"평균 20일 포트폴리오 수익률: "
    f"{average_return:.2f}%"
)

print(
    f"포트폴리오 승률: "
    f"{win_rate:.2f}%"
)






# ==========================================
# 10. Threshold × Top N 조합 백테스트
# ==========================================

THRESHOLDS = [
    0.70,
    0.75,
    0.80,
    0.85,
    0.90
]

TOP_NS = [
    1,
    3,
    5,
    10
]

HOLDING_DAYS = 20

initial_capital = 10_000_000


print()
print("===== Threshold × Top N Backtest =====")

print(
    f"Thresholds: {THRESHOLDS}"
)

print(
    f"Top N: {TOP_NS}"
)

print(
    f"Holding Period: {HOLDING_DAYS} trading days"
)


# ==========================================
# 10-1. 날짜 정렬
# ==========================================

result = result.sort_values(
    by="날짜"
).copy()


# ==========================================
# 10-2. 거래일 목록 생성
# ==========================================

trading_dates = (
    result["날짜"]
    .drop_duplicates()
    .sort_values()
    .tolist()
)


# ==========================================
# 10-3. 결과 저장
# ==========================================

all_results = []


# ==========================================
# 10-4. Threshold별 백테스트
# ==========================================

for threshold in THRESHOLDS:

    print()
    print(
        f"===== Threshold: {threshold:.2f} ====="
    )


    # ======================================
    # Top N별 백테스트
    # ======================================

    for top_n in TOP_NS:

        print(
            f"Top N: {top_n}"
        )


        capital = initial_capital

        backtest_results = []


        # ==================================
        # 20거래일마다 포트폴리오 구성
        # ==================================

        for i in range(
            0,
            len(trading_dates),
            HOLDING_DAYS
        ):

            # 현재 매수 날짜
            buy_date = trading_dates[i]


            # 매도 날짜 인덱스
            sell_index = (
                i + HOLDING_DAYS
            )


            # 20거래일 후 날짜가 없으면 종료
            if sell_index >= len(trading_dates):
                break


            sell_date = trading_dates[
                sell_index
            ]


            # ==============================
            # 현재 날짜 후보 종목
            # ==============================

            candidates = result[
                (result["날짜"] == buy_date) &
                (result["Probability"] >= threshold)
            ].copy()


            # 후보가 없으면 건너뜀
            if len(candidates) == 0:
                continue


            # Probability 높은 순 정렬
            candidates = candidates.sort_values(
                by="Probability",
                ascending=False
            )


            # Top N 선택
            portfolio = candidates.head(
                top_n
            )


            # 실제 투자 종목 수
            actual_top_n = len(portfolio)


            # ==============================
            # 거래 비용 반영
            # ==============================

            TRADING_COST = 0.003


            gross_return = (
                portfolio["FutureReturn"].mean()
            )


            portfolio_return = (
                gross_return
                - TRADING_COST * 100
            )


            # ==============================
            # 복리 계산
            # ==============================

            capital *= (
                1 + portfolio_return / 100
            )


            # ==============================
            # 결과 저장
            # ==============================

            backtest_results.append({

                "BuyDate": buy_date,

                "SellDate": sell_date,

                "Count": actual_top_n,

                "PortfolioReturn": portfolio_return,

                "Capital": capital

            })


        # ==================================
        # 백테스트 결과 DataFrame
        # ==================================

        backtest_df = pd.DataFrame(
            backtest_results
        )


        # 결과가 없으면 건너뜀
        if len(backtest_df) == 0:
            continue


        # ==================================
        # 평균 수익률
        # ==================================

        average_return = (
            backtest_df[
                "PortfolioReturn"
            ].mean()
        )


        # ==================================
        # 승률
        # ==================================

        win_rate = (
            backtest_df[
                "PortfolioReturn"
            ] > 0
        ).mean() * 100


        # ==================================
        # MDD 계산
        # ==================================

        capital_series = (
            backtest_df["Capital"]
        )


        running_max = (
            capital_series
            .cummax()
        )


        drawdown = (
            capital_series /
            running_max -
            1
        )


        max_drawdown = (
            drawdown.min() * 100
        )


        # ==================================
        # 누적 수익률
        # ==================================

        total_return = (
            capital /
            initial_capital -
            1
        ) * 100


        # ==================================
        # 결과 저장
        # ==================================

        all_results.append({

            "Threshold": threshold,

            "TopN": top_n,

            "InvestmentCount": len(backtest_df),

            "TotalStocks": backtest_df["Count"].sum(),

            "AverageReturn": average_return,

            "WinRate": win_rate,

            "MDD": max_drawdown,

            "FinalCapital": capital,

            "TotalReturn": total_return

        })


# ==========================================
# 11. 전체 결과 출력
# ==========================================

comparison_df = pd.DataFrame(
    all_results
)


print()

print(
    "=" * 90
)

print(
    "Threshold × Top N 백테스트 비교"
)

print(
    "=" * 90
)


print(
    comparison_df.to_string(
        index=False,
        formatters={
            "AverageReturn": "{:.2f}%".format,
            "WinRate": "{:.2f}%".format,
            "MDD": "{:.2f}%".format,
            "FinalCapital": "{:,.0f}원".format,
            "TotalReturn": "{:.2f}%".format
        }
    )
)