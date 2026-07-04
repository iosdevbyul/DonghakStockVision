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

    df["MA120"] = df["종가"].rolling(120).mean()

    df["MA120비율"] = (
        (df["종가"] - df["MA120"])
        / df["MA120"]
        * 100
    )

    # 최근 20일 최고가
    df["HIGH20"] = (
        df["고가"]
        .rolling(20)
        .max()
    )

    # 최고가 대비 위치(%)
    df["HIGH20비율"] = (
        (df["종가"] - df["HIGH20"])
        / df["HIGH20"]
        * 100
    )

    # 일간 수익률
    df["일간수익률"] = df["종가"].pct_change()

    # 최근 20일 변동성(%)
    df["Volatility20"] = (
        df["일간수익률"]
        .rolling(20)
        .std()
        * 100
    )

    # Momentum20
    df["Momentum20"] = (
        (df["종가"] - df["종가"].shift(20))
        / df["종가"].shift(20)
        * 100
    )

    # 최근 252일 최고가 (52주 신고가)
    df["HIGH252"] = (
        df["고가"]
        .rolling(252)
        .max()
    )

    # 52주 최고가 대비 위치(%)
    df["HIGH252비율"] = (
        (df["종가"] - df["HIGH252"])
        / df["HIGH252"]
        * 100
    )

    # 20일선과 60일선의 관계
    df["MA20_MA60_Gap"] = (
        (df["MA20"] - df["MA60"])
        / df["MA60"]
        * 100
    )

    # 60일선과 120일선의 관계
    df["MA60_MA120_Gap"] = (
        (df["MA60"] - df["MA120"])
        / df["MA120"]
        * 100
    )

    #BollingerPosition
    df["STD20"] = (
        df["종가"]
        .rolling(20)
        .std()
    )
    df["UpperBand"] = df["MA20"] + 2 * df["STD20"]
    df["LowerBand"] = df["MA20"] - 2 * df["STD20"]
    df["BollingerPosition"] = (
        (df["종가"] - df["LowerBand"])
        /
        (df["UpperBand"] - df["LowerBand"])
        * 100
    )

    # RSI(14)
    delta = df["종가"].diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()

    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = 100 - (
        100 / (1 + rs)
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

    df["Target"] = (
        df["20일후수익률"] >= 5
    ).astype(int)

    return df



def make_dataset(df):

    dataset = df[
        [
            "날짜",
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
            "Target"
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
