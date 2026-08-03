import pandas as pd
import numpy as np
import traceback


# ============================================================
# 설정
# ============================================================

BASE_DATASET_PATH = "sell_dataset.csv"
OUTPUT_DATASET_PATH = "sell_dataset_v2.csv"


# ============================================================
# SELL 전용 Feature 생성
# ============================================================

def create_sell_features(df):
    df = df.copy()

    # --------------------------------------------------------
    # 1. 단기 수익률
    # --------------------------------------------------------

    df["Return3"] = (
        df["종가"].pct_change(3) * 100
    )

    df["Return5"] = (
        df["종가"].pct_change(5) * 100
    )

    df["Return10"] = (
        df["종가"].pct_change(10) * 100
    )


    # --------------------------------------------------------
    # 2. 최근 고점 대비 하락률
    # --------------------------------------------------------

    high5 = (
        df["고가"]
        .rolling(5)
        .max()
    )

    high10 = (
        df["고가"]
        .rolling(10)
        .max()
    )

    high20 = (
        df["고가"]
        .rolling(20)
        .max()
    )

    df["Drawdown5"] = (
        (df["종가"] - high5)
        / high5
        * 100
    )

    df["Drawdown10"] = (
        (df["종가"] - high10)
        / high10
        * 100
    )

    df["Drawdown20"] = (
        (df["종가"] - high20)
        / high20
        * 100
    )


    # --------------------------------------------------------
    # 3. 이동평균 하락/상승 속도
    # --------------------------------------------------------

    ma20 = (
        df["종가"]
        .rolling(20)
        .mean()
    )

    ma60 = (
        df["종가"]
        .rolling(60)
        .mean()
    )

    ma120 = (
        df["종가"]
        .rolling(120)
        .mean()
    )

    df["MA20Slope5"] = (
        (ma20 - ma20.shift(5))
        / ma20.shift(5)
        * 100
    )

    df["MA60Slope5"] = (
        (ma60 - ma60.shift(5))
        / ma60.shift(5)
        * 100
    )

    df["MA120Slope5"] = (
        (ma120 - ma120.shift(5))
        / ma120.shift(5)
        * 100
    )


    # --------------------------------------------------------
    # 4. RSI 변화
    # --------------------------------------------------------

    delta = df["종가"].diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    avg_loss = avg_loss.replace(
        0,
        np.nan
    )

    rs = avg_gain / avg_loss

    rsi = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df["RSIChange3"] = (
        rsi - rsi.shift(3)
    )

    df["RSIChange5"] = (
        rsi - rsi.shift(5)
    )


    # --------------------------------------------------------
    # 5. 거래량 변화
    # --------------------------------------------------------

    volume_ma5 = (
        df["거래량"]
        .rolling(5)
        .mean()
    )

    volume_ma10 = (
        df["거래량"]
        .rolling(10)
        .mean()
    )

    df["VolumeRatio5"] = (
        df["거래량"]
        / volume_ma5
    )

    df["VolumeRatio10"] = (
        df["거래량"]
        / volume_ma10
    )


    # --------------------------------------------------------
    # 6. 가격 하락 + 거래량 증가
    # --------------------------------------------------------

    daily_return = (
        df["종가"].pct_change()
    )

    df["PriceVolumeDown"] = (
        (
            (daily_return < 0)
            &
            (df["VolumeRatio5"] > 1)
        )
        .astype(int)
    )


    return df


# ============================================================
# 기존 SELL Dataset
# ============================================================

print("기존 SELL Dataset 불러오는 중...")

base_dataset = pd.read_csv(
    BASE_DATASET_PATH,
    dtype={
        "ticker": str
    },
    low_memory=False
)

print(
    f"기존 데이터: "
    f"{len(base_dataset):,}"
)


base_dataset["날짜"] = pd.to_datetime(
    base_dataset["날짜"]
)


# ticker 목록

tickers = (
    base_dataset["ticker"]
    .dropna()
    .unique()
)

print(
    f"종목 수: {len(tickers):,}"
)


# ============================================================
# Feature 목록
# ============================================================

SELL_FEATURES_V2 = [
    "Return3",
    "Return5",
    "Return10",

    "Drawdown5",
    "Drawdown10",
    "Drawdown20",

    "MA20Slope5",
    "MA60Slope5",
    "MA120Slope5",

    "RSIChange3",
    "RSIChange5",

    "VolumeRatio5",
    "VolumeRatio10",

    "PriceVolumeDown",
]


all_dataset = []

processed = 0


# ============================================================
# 종목별 처리
# ============================================================

for ticker in tickers:

    processed += 1

    print(
        f"[{processed}/{len(tickers)}] "
        f"[{ticker}] 처리 중..."
    )

    try:

        # ----------------------------------------------------
        # 기존 SELL Dataset에서 해당 종목만 가져오기
        # ----------------------------------------------------

        ticker_dataset = base_dataset[
            base_dataset["ticker"] == ticker
        ].copy()

        if ticker_dataset.empty:
            continue


        # ----------------------------------------------------
        # Raw 데이터
        # ----------------------------------------------------

        raw = pd.read_csv(
            f"raw/{ticker}.csv"
        )

        raw["날짜"] = pd.to_datetime(
            raw["날짜"]
        )

        raw = raw.sort_values(
            "날짜"
        ).reset_index(
            drop=True
        )


        # ----------------------------------------------------
        # SELL 전용 Feature 생성
        # ----------------------------------------------------

        raw = create_sell_features(
            raw
        )


        # ----------------------------------------------------
        # 필요한 컬럼만 선택
        # ----------------------------------------------------

        feature_dataset = raw[
            [
                "날짜",
                *SELL_FEATURES_V2,
            ]
        ].copy()


        # ----------------------------------------------------
        # 날짜 기준 Merge
        # ----------------------------------------------------

        ticker_dataset = ticker_dataset.merge(
            feature_dataset,
            on="날짜",
            how="left"
        )


        all_dataset.append(
            ticker_dataset
        )


    except Exception as e:

        print(
            f"[{ticker}] 실패: {e}"
        )

        traceback.print_exc()

        continue


# ============================================================
# 전체 Dataset
# ============================================================

if not all_dataset:

    raise RuntimeError(
        "SELL Dataset v2가 생성되지 않았습니다."
    )


final_dataset = pd.concat(
    all_dataset,
    ignore_index=True
)


# ============================================================
# NaN 제거
# ============================================================

before_dropna = len(
    final_dataset
)

final_dataset = final_dataset.dropna(
    subset=SELL_FEATURES_V2
).reset_index(
    drop=True
)

after_dropna = len(
    final_dataset
)


# ============================================================
# 저장
# ============================================================

final_dataset.to_csv(
    OUTPUT_DATASET_PATH,
    index=False
)


# ============================================================
# 결과
# ============================================================

print()
print("=" * 60)
print("SELL Dataset v2 생성 완료")
print("=" * 60)

print(
    f"기존 데이터: "
    f"{before_dropna:,}"
)

print(
    f"최종 데이터: "
    f"{after_dropna:,}"
)

print(
    f"제외된 데이터: "
    f"{before_dropna - after_dropna:,}"
)

print()

print(
    "추가된 Feature:"
)

for feature in SELL_FEATURES_V2:

    print(
        f" - {feature}"
    )

print()

print(
    "SELL_TARGET 분포:"
)

print(
    final_dataset["SELL_TARGET"]
    .value_counts()
)

print()

print(
    final_dataset["SELL_TARGET"]
    .value_counts(
        normalize=True
    ) * 100
)

print()

print(
    f"저장 파일: "
    f"{OUTPUT_DATASET_PATH}"
)