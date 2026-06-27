import pandas as pd

HOLD_DAYS = 20

tickers = pd.read_csv(
    "tickers.csv",
    dtype={"ticker": str}
)

all_dataset = []

for _, row in tickers.iterrows():

    ticker = row["ticker"]
    name = row["name"]

    print(f"{name} 처리 중...")

    try:

        df = pd.read_csv(f"raw/{ticker}.csv")

    except Exception:

        continue