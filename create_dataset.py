import pandas as pd
from ta.trend import ADXIndicator

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

    #MACD
    df["EMA12"] = (
    df["종가"]
    .ewm(span=12, adjust=False)
    .mean()
    )

    df["EMA26"] = (
        df["종가"]
        .ewm(span=26, adjust=False)
        .mean()
    )

    df["MACD"] = (
        df["EMA12"]
        - df["EMA26"]
    )

    df["MACDSignal"] = (
        df["MACD"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    df["MACDHistogram"] = (
        df["MACD"]
        - df["MACDSignal"]
    )

    #ATR
    # 이전 종가
    df["PrevClose"] = df["종가"].shift(1)

    # True Range
    tr1 = df["고가"] - df["저가"]

    tr2 = (
        df["고가"]
        - df["PrevClose"]
    ).abs()

    tr3 = (
        df["저가"]
        - df["PrevClose"]
    ).abs()

    df["TR"] = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    # ATR(14)
    df["ATR"] = (
        df["TR"]
        .rolling(14)
        .mean()
    )

    #OBV
    price_change = df["종가"].diff()

    direction = price_change.apply(
        lambda x:
        1 if x > 0 else (
            -1 if x < 0 else 0
        )
    )

    obv = (
        direction
        * df["거래량"]
    )

    df["OBV"] = obv.cumsum()

    #ADX
    adx = ADXIndicator(
        high=df["고가"],
        low=df["저가"],
        close=df["종가"],
        window=14
    )

    df["ADX"] = adx.adx()

    #MFI
    df["TypicalPrice"] = (
        df["고가"]
        + df["저가"]
        + df["종가"]
    ) / 3

    df["MoneyFlow"] = (
        df["TypicalPrice"]
        * df["거래량"]
    )

    price_change = (
        df["TypicalPrice"]
        .diff()
    )

    df["PositiveFlow"] = df["MoneyFlow"].where(
        price_change > 0,
        0
    )

    df["NegativeFlow"] = df["MoneyFlow"].where(
        price_change < 0,
        0
    )

    positive = (
        df["PositiveFlow"]
        .rolling(14)
        .sum()
    )

    negative = (
        df["NegativeFlow"]
        .rolling(14)
        .sum()
    )

    money_ratio = (
        positive
        / negative
    )

    df["MFI"] = (
        100
        -
        (
            100
            /
            (1 + money_ratio)
        )
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
            "Target",
            "MACD", 
            "MACDSignal", 
            "MACDHistogram",
            "ATR",
            "OBV",
            "ADX",
            "MFI"
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
