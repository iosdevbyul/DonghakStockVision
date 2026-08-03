import pandas as pd
import numpy as np

from create_dataset import create_features
from create_sell_dataset import (
    create_sell_label,
    UPPER_ATR_MULTIPLIER,
    LOWER_ATR_MULTIPLIER,
)


TICKER = "005930"  # 삼성전자 예시


def verify_sample(df, target):

    samples = df[
        df["SELL_TARGET"] == target
    ].dropna(
        subset=["ATR"]
    )

    if samples.empty:
        print(
            f"SELL_TARGET={target} 샘플이 없습니다."
        )
        return

    # 가운데 샘플 하나 선택
    sample_index = samples.index[
        len(samples) // 2
    ]

    position = df.index.get_loc(
        sample_index
    )

    current_close = df.loc[
        sample_index,
        "종가"
    ]

    current_atr = df.loc[
        sample_index,
        "ATR"
    ]

    upper_barrier = (
        current_close
        + current_atr
        * UPPER_ATR_MULTIPLIER
    )

    lower_barrier = (
        current_close
        - current_atr
        * LOWER_ATR_MULTIPLIER
    )

    print()
    print("=" * 60)
    print(
        f"SELL_TARGET={target} 검증"
    )
    print("=" * 60)

    print(
        f"날짜: "
        f"{df.loc[sample_index, '날짜']}"
    )

    print(
        f"현재가: "
        f"{current_close:,.2f}"
    )

    print(
        f"ATR: "
        f"{current_atr:,.2f}"
    )

    print(
        f"상단 Barrier (+{UPPER_ATR_MULTIPLIER} ATR): "
        f"{upper_barrier:,.2f}"
    )

    print(
        f"하단 Barrier (-{LOWER_ATR_MULTIPLIER} ATR): "
        f"{lower_barrier:,.2f}"
    )

    print()
    print("미래 가격:")
    print("-" * 60)

    result = None

    future_df = df.iloc[
        position + 1:
    ]

    for _, row in future_df.iterrows():

        high = row["고가"]
        low = row["저가"]

        hit_upper = (
            high >= upper_barrier
        )

        hit_lower = (
            low <= lower_barrier
        )

        print(
            f"{row['날짜']} | "
            f"시가={row['시가']:,.0f} "
            f"고가={high:,.0f} "
            f"저가={low:,.0f} "
            f"종가={row['종가']:,.0f}"
        )

        if hit_upper and hit_lower:

            print(
                "→ 같은 날 상/하단 Barrier "
                "동시 도달"
            )

            result = None
            break

        if hit_upper:

            print(
                "→ 상단 Barrier 먼저 도달"
            )

            result = 0
            break

        if hit_lower:

            print(
                "→ 하단 Barrier 먼저 도달"
            )

            result = 1
            break

    print()
    print(
        f"저장된 SELL_TARGET: {target}"
    )

    print(
        f"실제 Barrier 결과: {result}"
    )

    print()

    if result == target:

        print("결과: PASS")

    else:

        print("결과: FAIL")


# ============================================================
# 실행
# ============================================================

df = pd.read_csv(
    f"raw/{TICKER}.csv"
)

df["ticker"] = TICKER

df = create_features(df)

df = create_sell_label(df)

print(
    f"검증 종목: {TICKER}"
)

print(
    f"전체 데이터: {len(df):,}"
)

print(
    f"SELL_TARGET=0: "
    f"{(df['SELL_TARGET'] == 0).sum():,}"
)

print(
    f"SELL_TARGET=1: "
    f"{(df['SELL_TARGET'] == 1).sum():,}"
)

verify_sample(
    df,
    target=0
)

verify_sample(
    df,
    target=1
)