import pandas as pd
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV
import numpy as np

# ==========================================
# 1. 데이터 불러오기
# ==========================================

df = pd.read_csv(
    "dataset.csv",
    low_memory=False
)


# 날짜를 datetime으로 변환
df["날짜"] = pd.to_datetime(
    df["날짜"]
)

# 날짜 기준 정렬
df = df.sort_values(
    by="날짜"
).reset_index(
    drop=True
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
# 3. Walk-Forward 기간 설정
# ==========================================

periods = [

    {
        "name": "2017-2019",
        "train_end": "2017-01-01",
        "test_start": "2017-01-01",
        "test_end": "2019-01-01"
    },

    {
        "name": "2019-2021",
        "train_end": "2019-01-01",
        "test_start": "2019-01-01",
        "test_end": "2021-01-01"
    },

    {
        "name": "2021-2023",
        "train_end": "2021-01-01",
        "test_start": "2021-01-01",
        "test_end": "2023-01-01"
    },

    {
        "name": "2023-2025",
        "train_end": "2023-01-01",
        "test_start": "2023-01-01",
        "test_end": "2025-01-01"
    }

]


# ==========================================
# 4. 백테스트 설정
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

INITIAL_CAPITAL = 10_000_000

TRADING_COST = 0.003


# ==========================================
# 5. 전체 결과 저장
# ==========================================

all_results = []
all_predictions = []

# ==========================================
# 6. Walk-Forward 실행
# ==========================================

for period in periods:

    print()
    print("=" * 70)

    print(
        f"Walk-Forward Period: "
        f"{period['name']}"
    )

    print("=" * 70)


    # ======================================
    # Train / Test 날짜 범위
    # ======================================

    train_mask = (
        df["날짜"] <
        pd.Timestamp(
            period["train_end"]
        )
    )

    test_mask = (
        (df["날짜"] >= pd.Timestamp(
            period["test_start"]
        ))
        &
        (df["날짜"] < pd.Timestamp(
            period["test_end"]
        ))
    )


    train_df = df[
        train_mask
    ].copy()

    test_df = df[
        test_mask
    ].copy()


    print()

    print(
        f"Train 기간: "
        f"{train_df['날짜'].min().date()} ~ "
        f"{train_df['날짜'].max().date()}"
    )

    print(
        f"Test 기간: "
        f"{test_df['날짜'].min().date()} ~ "
        f"{test_df['날짜'].max().date()}"
    )

    print(
        f"Train 데이터: "
        f"{len(train_df):,}개"
    )

    print(
        f"Test 데이터: "
        f"{len(test_df):,}개"
    )


    # ======================================
    # Train / Test Feature
    # ======================================

    X_train = train_df.drop(
        columns=[
            "날짜",
            "ticker",
            "name",
            "Target",
            "20일후수익률"
        ]
    )

    y_train = train_df[
        "Target"
    ]


    X_test = test_df.drop(
        columns=[
            "날짜",
            "ticker",
            "name",
            "Target",
            "20일후수익률"
        ]
    )


    # ======================================
    # XGBoost 모델
    # ======================================

    base_model = XGBClassifier(

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

    # model = CalibratedClassifierCV(
    #     base_model,
    #     method="isotonic",
    #     cv=3
    # )


    # ======================================
    # 모델 학습
    # ======================================

    print()

    print(
        "학습 중..."
    )

    # model.fit(
    #     X_train,
    #     y_train
    # )

    base_model.fit(
        X_train,
        y_train
    )

    # importance = np.mean(
    #     [
    #         clf.estimator.feature_importances_
    #         for clf in model.calibrated_classifiers_
    #     ],
    #     axis=0
    # )

    importance = base_model.feature_importances_

    feature_importance = pd.DataFrame({
        "Feature": X_train.columns,
        "Importance": importance
    })

    feature_importance = feature_importance.sort_values(
        "Importance",
        ascending=False
    )

    feature_importance.to_csv(
        f"feature_importance_{period['name']}.csv",
        index=False
    )

    print()
    print(feature_importance)



    raw_probability = base_model.fit(
        X_train,
        y_train
    ).predict_proba(X_test)[:, 1]

    # cal_probability = model.predict_proba(X_test)[:, 1]

    
    # ======================================
    # 예측
    # ======================================

    print(
        "예측 중..."
    )

    # probability = model.predict_proba(
    #     X_test
    # )[:, 1]

    probability = base_model.predict_proba(
        X_test
    )[:, 1]

    # ======================================
    # 예측 결과 생성
    # ======================================

    result = pd.DataFrame({

        "날짜":
            test_df["날짜"].values,

        "ticker":
            test_df["ticker"].values,

        "name":
            test_df["name"].values,

        "Probability":
            probability,

        "FutureReturn":
            test_df[
                "20일후수익률"
            ].values

    })

    all_predictions.append(result)


    # ======================================
    # Probability 확인
    # ======================================

    print()
    print("=" * 60)
    print(period["name"])
    print("=" * 60)

    print(result["Probability"].describe())

    print()
    print(
        result["Probability"].quantile([
            0.90,
            0.95,
            0.99,
            0.999
        ])
    )

    print()
    print("0.70 이상 :", (result["Probability"] >= 0.70).sum())
    print("0.75 이상 :", (result["Probability"] >= 0.75).sum())
    print("0.80 이상 :", (result["Probability"] >= 0.80).sum())
    print("0.85 이상 :", (result["Probability"] >= 0.85).sum())
    print("0.90 이상 :", (result["Probability"] >= 0.90).sum())

    print("=" * 60)


    # ==================================
    # 거래일 목록
    # ==================================

    trading_dates = (

        result[
            "날짜"
        ]

        .drop_duplicates()

        .sort_values()

        .tolist()

    )

    print("=" * 60)
    print(period["name"])

    print("전체 예측 :", len(result))
    print("거래일 :", result["날짜"].nunique())

    print(result["Probability"].describe())

    # ======================================
    # Threshold × Top N
    # ======================================

    for threshold in THRESHOLDS:

        if threshold == 0.70:
            daily_counts = (
                result[result["Probability"] >= threshold]
                .groupby("날짜")
                .size()
            )

            count = (result["Probability"] >= threshold).sum()
            days = result.loc[
                result["Probability"] >= threshold,
                "날짜"
            ].nunique()

        for top_n in TOP_NS:

            # ==================================
            # 자본 초기화
            # ==================================

            capital = (
                INITIAL_CAPITAL
            )


            backtest_results = []


            # ==================================
            # 20거래일 단위 투자
            # ==================================

            for i in range(
                0,
                len(trading_dates),
                HOLDING_DAYS
            ):


                buy_date = (
                    trading_dates[i]
                )


                sell_index = (

                    i
                    +
                    HOLDING_DAYS

                )


                if (

                    sell_index
                    >=
                    len(
                        trading_dates
                    )

                ):

                    break


                sell_date = (

                    trading_dates[
                        sell_index
                    ]

                )


                # ==================================
                # 종목 후보
                # ==================================

                candidates = result[

                    (result["날짜"]
                     == buy_date)

                    &

                    (
                        result[
                            "Probability"
                        ]
                        >=
                        threshold
                    )

                ].copy()


                if len(
                    candidates
                ) == 0:
                    continue


                # ==================================
                # Probability 정렬
                # ==================================

                candidates = (

                    candidates

                    .sort_values(

                        by="Probability",

                        ascending=False

                    )

                )


                # ==================================
                # Top N
                # ==================================

                portfolio = (

                    candidates

                    .head(
                        top_n
                    )

                )

                print(
                    buy_date,
                    "Threshold:", threshold,
                    "TopN:", top_n,
                    "선택종목:", len(portfolio)
                )

                print(
                    portfolio[
                        ["ticker", "Probability", "FutureReturn"]
                    ]
                )
                # ==================================
                # 수익률 계산
                # ==================================

                gross_return = (

                    portfolio[
                        "FutureReturn"
                    ]

                    .mean()

                )


                portfolio_return = (

                    gross_return

                    -
                    
                    TRADING_COST * 100

                )


                # ==================================
                # 복리 계산
                # ==================================

                capital *= (

                    1
                    +
                    portfolio_return
                    /
                    100

                )


                # ==================================
                # 결과 저장
                # ==================================

                backtest_results.append({

                    "BuyDate":
                        buy_date,

                    "SellDate":
                        sell_date,

                    "Count":
                        len(
                            portfolio
                        ),

                    "PortfolioReturn":
                        portfolio_return,

                    "Capital":
                        capital

                })


            # ==================================
            # 결과 DataFrame
            # ==================================

            backtest_df = pd.DataFrame(

                backtest_results

            )


            if len(
                backtest_df
            ) == 0:

                continue


            # ==================================
            # 평균 수익률
            # ==================================

            average_return = (

                backtest_df[
                    "PortfolioReturn"
                ]

                .mean()

            )


            # ==================================
            # 승률
            # ==================================

            win_rate = (

                backtest_df[
                    "PortfolioReturn"
                ]

                > 0

            ).mean() * 100


            # ==================================
            # MDD
            # ==================================

            capital_series = (

                backtest_df[
                    "Capital"
                ]

            )


            running_max = (

                capital_series

                .cummax()

            )


            drawdown = (

                capital_series

                /
                running_max

                -
                1

            )


            max_drawdown = (

                drawdown.min()

                *
                100

            )


            # ==================================
            # 누적 수익률
            # ==================================

            total_return = (

                capital

                /
                INITIAL_CAPITAL

                -
                1

            ) * 100


            # ==================================
            # 결과 저장
            # ==================================

            all_results.append({

                "Period":
                    period["name"],

                "Threshold":
                    threshold,

                "TopN":
                    top_n,

                "InvestmentCount":
                    len(
                        backtest_df
                    ),

                "TotalStocks":
                    backtest_df[
                        "Count"
                    ].sum(),

                "AverageReturn":
                    average_return,

                "WinRate":
                    win_rate,

                "MDD":
                    max_drawdown,

                "FinalCapital":
                    capital,

                "TotalReturn":
                    total_return

            })


# ==========================================
# 7. 전체 결과
# ==========================================
prediction_df = pd.concat(
    all_predictions,
    ignore_index=True
)

prediction_df.to_csv(
    "prediction.csv",
    index=False
)

print()
print("all_results 개수 :", len(all_results))

results_df = pd.DataFrame(

    all_results

)

results_df.to_csv(
    "walk_forward_results.csv",
    index=False
)

pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)

print(results_df)

print("=" * 50)
print(period["name"])

print("전체 예측 개수 :", len(result))
print("거래일 수 :", result["날짜"].nunique())

print(result["Probability"].describe())