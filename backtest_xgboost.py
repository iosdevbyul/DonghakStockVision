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
# 10. 20거래일마다 포트폴리오 교체
# ==========================================

initial_capital = 10_000_000

capital = initial_capital


# 날짜 순서대로 정렬
daily_portfolio_return = (
    daily_portfolio_return
    .sort_index()
)


# # 20거래일마다 포트폴리오 교체
# rebalance_returns = (
#     daily_portfolio_return
#     .iloc[::20]
# )


# print()

# print(
#     f"실제 투자 횟수: "
#     f"{len(rebalance_returns):,}"
# )


# ==========================================
# 11. 복리 계산
# ==========================================

for return_rate in rebalance_returns:

    capital *= (
        1 + return_rate / 100
    )


# ==========================================
# 12. 누적 수익률 계산
# ==========================================

total_return = (
    capital / initial_capital - 1
) * 100


print()

print(
    f"초기 자본: "
    f"{initial_capital:,.0f}원"
)

print(
    f"최종 자산: "
    f"{capital:,.0f}원"
)

print(
    f"누적 수익률: "
    f"{total_return:.2f}%"
)