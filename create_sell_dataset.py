import pandas as pd
import numpy as np
import traceback

from create_dataset import create_features


# ============================================================
# SELL Label 설정
# ============================================================

UPPER_ATR_MULTIPLIER = 1.0
LOWER_ATR_MULTIPLIER = 2.0


def create_sell_label(df):
    """
    현재 시점의 ATR을 기준으로 상/하단 Barrier를 만든다.

    현재가 + ATR * UPPER_ATR_MULTIPLIER
        -> 상승 Barrier

    현재가 - ATR * LOWER_ATR_MULTIPLIER
        -> 하락 Barrier

    이후 미래의 가격에서 어느 Barrier가 먼저 도달하는지 확인한다.

    SELL_TARGET
        0 = 상승 Barrier가 먼저 도달
        1 = 하락 Barrier가 먼저 도달
        NaN = 판단할 수 없음

    주의:
    일봉 데이터이므로 같은 날 상/하단 Barrier를 모두 건드린 경우
    어느 것이 먼저 발생했는지 알 수 없다.
    이런 경우는 NaN으로 제외한다.
    """

    df = df.copy()

    close = df["종가"].to_numpy(dtype=float)
    high = df["고가"].to_numpy(dtype=float)
    low = df["저가"].to_numpy(dtype=float)
    atr = df["ATR"].to_numpy(dtype=float)

    n = len(df)

    sell_target = np.full(n, np.nan)

    for i in range(n):

        current_close = close[i]
        current_atr = atr[i]

        # ATR이 없는 구간은 판단 불가
        if (
            not np.isfinite(current_close)
            or not np.isfinite(current_atr)
            or current_atr <= 0
        ):
            continue

        upper_barrier = (
            current_close
            + current_atr * UPPER_ATR_MULTIPLIER
        )

        lower_barrier = (
            current_close
            - current_atr * LOWER_ATR_MULTIPLIER
        )

        # 현재 봉 이후부터 탐색
        for j in range(i + 1, n):

            hit_upper = high[j] >= upper_barrier
            hit_lower = low[j] <= lower_barrier

            # 상승/하락 Barrier를 같은 날 모두 터치
            # 일봉만으로는 순서를 알 수 없으므로 제외
            if hit_upper and hit_lower:
                sell_target[i] = np.nan
                break

            # 상승 Barrier가 먼저 도달
            if hit_upper:
                sell_target[i] = 0
                break

            # 하락 Barrier가 먼저 도달
            if hit_lower:
                sell_target[i] = 1
                break

        # 끝까지 어느 Barrier도 도달하지 않은 경우
        # 판단할 수 없으므로 제외
        if j == n - 1:
            if (
                not (high[j] >= upper_barrier)
                and not (low[j] <= lower_barrier)
            ):
                sell_target[i] = np.nan

    df["SELL_TARGET"] = sell_target

    return df


def make_sell_dataset(df):
    """
    SELL 모델 학습에 사용할 Feature만 선택한다.
    """

    dataset = df[
        [
            "날짜",
            "ticker",
            "name",

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
            "ATRRatio",

            "OBV",
            "ADX",
            "MFI",
            "CMF",
            "CCI",

            "DonchianPosition",

            "VWAPRatio",

            "VolumeSpike",

            "LOW20비율",
            "Position60",
            "Position120",

            "SELL_TARGET",
        ]
    ]

    return dataset.dropna()


# ============================================================
# Ticker 목록
# ============================================================

tickers = pd.read_csv(
    "tickers.csv",
    dtype={"ticker": str}
)


all_dataset = []

total_sell_0 = 0
total_sell_1 = 0


# ============================================================
# 종목별 처리
# ============================================================

for _, row in tickers.iterrows():

    ticker = row["ticker"]
    name = row["name"]

    print(f"[{ticker}] {name} 처리 중...")

    try:

        # Raw 데이터
        df = pd.read_csv(
            f"raw/{ticker}.csv"
        )

        df["ticker"] = ticker
        df["name"] = name

        # 기존 Feature Engineering 재사용
        df = create_features(df)

        # SELL Label 생성
        df = create_sell_label(df)

        # SELL Dataset 생성
        dataset = make_sell_dataset(df)

        if len(dataset) == 0:
            print(f"[{ticker}] 유효한 SELL 데이터 없음")
            continue

        sell_0_count = (
            dataset["SELL_TARGET"] == 0
        ).sum()

        sell_1_count = (
            dataset["SELL_TARGET"] == 1
        ).sum()

        total_sell_0 += sell_0_count
        total_sell_1 += sell_1_count

        print(
            f"[{ticker}] "
            f"SELL=0: {sell_0_count:,}, "
            f"SELL=1: {sell_1_count:,}"
        )

        all_dataset.append(dataset)

    except Exception as e:

        print(
            f"[{ticker}] 실패: {e}"
        )

        traceback.print_exc()

        continue


# ============================================================
# 전체 Dataset 생성
# ============================================================

if not all_dataset:
    raise RuntimeError(
        "생성된 SELL Dataset이 없습니다."
    )


final_dataset = pd.concat(
    all_dataset,
    ignore_index=True
)


# ============================================================
# 저장
# ============================================================

final_dataset.to_csv(
    "sell_dataset.csv",
    index=False
)


# ============================================================
# 결과 출력
# ============================================================

total_count = len(final_dataset)

print()
print("=" * 60)
print("SELL Dataset 생성 완료")
print("=" * 60)

print(
    f"전체 데이터: {total_count:,}"
)

print(
    f"SELL_TARGET = 0: "
    f"{total_sell_0:,}"
)

print(
    f"SELL_TARGET = 1: "
    f"{total_sell_1:,}"
)

if total_count > 0:

    sell_0_ratio = (
        total_sell_0
        / total_count
        * 100
    )

    sell_1_ratio = (
        total_sell_1
        / total_count
        * 100
    )

    print(
        f"SELL_TARGET = 0 비율: "
        f"{sell_0_ratio:.2f}%"
    )

    print(
        f"SELL_TARGET = 1 비율: "
        f"{sell_1_ratio:.2f}%"
    )

print()
print(
    "저장 파일: sell_dataset.csv"
)