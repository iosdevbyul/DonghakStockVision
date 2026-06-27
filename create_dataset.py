import pandas as pd


def create_features(df):

    df["MA20"] = df["종가"].rolling(20).mean()

    df["평균거래량20"] = df["거래량"].rolling(20).mean()

    df["거래량비율"] = (
        df["거래량"]
        / df["평균거래량20"]
    )

    df["MA20비율"] = (
        (df["종가"] - df["MA20"])
        / df["MA20"]
        * 100
    )

    df["MA60"] = df["종가"].rolling(60).mean()

    df["MA60비율"] = (
        (df["종가"] - df["MA60"])
        / df["MA60"]
        * 100
    )

    return df



HOLD_DAYS = 20

def create_label(df):

    df["20일후종가"] = (
        df["종가"]
        .shift(-HOLD_DAYS)
    )

    df["20일후수익률"] = (
        (df["20일후종가"] - df["종가"])
        / df["종가"]
        * 100
    )

    return df



def make_dataset(df):

    dataset = df[
        [
            "날짜",
            "거래량비율",
            "MA20비율",
            "MA60비율",
            "20일후수익률"
        ]
    ]

    return dataset.dropna()


tickers = pd.read_csv(
    "tickers.csv",
    dtype={"ticker": str}
)

all_dataset = []

for _, row in tickers.iterrows():

    ticker = row["ticker"]
    name = row["name"]

    print(f"[{ticker}] {name} 처리 중...")

    try:

        df = pd.read_csv(f"raw/{ticker}.csv")

        df = create_features(df)

        df = create_label(df)

        dataset = make_dataset(df)

        all_dataset.append(dataset)

    except Exception as e:
        print(f"{ticker} 실패: {e}")
        continue

final_dataset = pd.concat(
    all_dataset,
    ignore_index=True
)

final_dataset.to_csv(
    "dataset.csv",
    index=False
)

print()
print(f"총 {len(final_dataset)}개 학습 데이터 생성")
