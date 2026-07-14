import pandas as pd

df = pd.read_csv("dataset.csv", nrows=1)

print(df.columns.tolist())