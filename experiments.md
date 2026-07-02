# DonghakStockVision Experiments

## v1

Features
- 거래량비율
- MA20비율

Dataset
- Hanmi Semiconductor (042700)

Model
- RandomForestClassifier

Accuracy
- 53.12%

---

## v2

Features
- 거래량비율
- MA20비율
- MA60비율

Dataset
- All KRX stocks
- 6,333,530 samples

Model
- RandomForestClassifier

Accuracy
- 52.65%

---

## v3

Features
- 거래량비율
- MA20비율
- MA60비율
- RSI

Dataset
- All KRX stocks
- 6,333,530 samples

Model
- RandomForestClassifier

Accuracy
- 52.66%

---

## v4

Features
- 거래량비율
- MA20비율
- MA60비율
- RSI
- HIGH20비율

Dataset
- All KRX stocks
- 6,333,530 samples

Model
- RandomForestClassifier

Accuracy
- 54.18%

---

## v5

Features
- 거래량비율
- MA20비율
- MA60비율
- MA120비율
- RSI
- HIGH20비율

Dataset
- All KRX stocks
- 6,333,530 samples

Model
- RandomForestClassifier

Accuracy
- 55.57%

---

## v6

Features
- 거래량비율
- MA20비율
- MA60비율
- MA120비율
- RSI
- HIGH20비율
- Volatility20

Accuracy
57.27%

Dataset
6,330,000 rows

Notes
- Volatility20 improved accuracy significantly.

---

## v7

Changes
- Tuning dataset: 500,000 rows
- n_jobs = -1
- Added training time measurement

Accuracy
54.64%

Training Time
94.29 sec

Observation
- 학습 속도가 크게 향상됨.
- 정확도는 약 2.6% 하락.
- 이후 Hyperparameter Tuning은 50만 샘플 기준으로 진행.

## v8

Changes
- n_estimators = 200

Accuracy
55.06%

Training Time
183.67 sec

---

## v9

Changes
- n_estimators = 300

Accuracy
55.18%

Training Time
236.45 sec

---

## v10

Changes
- n_estimators = 500

Accuracy
55.28%

Training Time
454.65 sec

---

## v11

Changes
- n_estimators = 1000

Accuracy
55.31%

Training Time
1054.98 sec

Observation

- Accuracy improvement becomes very small after 300 trees.
- Training time increases rapidly.
- 300 trees provide the best balance between speed and accuracy.

---

## v12

Changes
- max_depth = 10

Accuracy

56.08%

Training Time

104.33 sec

---

## v13

Changes
- max_depth = 20

Accuracy

56.26%

Training Time

186.09 sec

---

## v14

Changes
- max_depth = None

Accuracy

55.18%

Training Time

290.18 sec

Observation

- Unlimited tree depth caused worse generalization (overfitting).

Decision

- Selected max_depth = 20

---

## v15

Changes
- min_samples_leaf = 1

Accuracy

56.26%

Training Time

163.35 sec

---

## v16

Changes
- min_samples_leaf = 5

Accuracy

56.26%

Training Time

168.95 sec

---

## v17

Changes
- min_samples_leaf = 10

Accuracy

56.21%

Training Time

179.28 sec

---

## v18

Changes
- min_samples_leaf = 20

Accuracy

56.23%

Training Time

197.07 sec

---

## v19

Changes
- min_samples_leaf = 50

Accuracy

56.29%

Training Time

173.30 sec

Observation

- Best accuracy.
- Feature importance became more concentrated on MA60 and MA120.

Decision

Selected Parameters

- n_estimators = 300
- max_depth = 20
- min_samples_leaf = 50

Best Accuracy

56.29%

---

## v20

Changes
- Added Momentum20 feature

Accuracy

56.27%

Observation

- Momentum20 was used by the model (importance 0.1280).
- However, it did not improve overall accuracy.
- Likely overlaps with MA20/MA60/MA120 related features.

Decision

- Keep the feature for now.
- Re-evaluate after adding more features or testing XGBoost.

---

## v21

Changes
- Added HIGH252 feature (52-week High)
- Added HIGH252비율 feature

Accuracy

56.75%

Training Time

206.44 sec

Observation

- Accuracy improved by +0.46%.
- HIGH252비율 became one of the important features.
- Long-term trend information improved model performance.

Decision

- Keep HIGH252비율 feature.

---

## v22

Changes
- Added MA20_MA60_Gap feature

Accuracy

56.74%

Training Time

232.07 sec

Observation

- MA20_MA60_Gap was actively used by the model.
- Accuracy remained almost unchanged (-0.01%).
- The feature appears to overlap with MA20 and MA60 related features.

Decision

- Keep the feature for now.
- Re-evaluate with XGBoost later.

---

## v25

Changes
- Added MA60_MA120_Gap feature

Hypothesis
- The relationship between the 60-day and 120-day moving averages will help the model understand long-term trends.

Accuracy

56.79%

Training Time

221.17 sec

Observation

- New best accuracy achieved.
- Feature importance was moderate (0.0819).
- The feature appears to complement existing trend-related features.

Decision

- Keep the feature.

---

## v26

Features
- Added BollingerPosition

Formula
- STD20 = rolling(20).std()
- UpperBand = MA20 + 2 × STD20
- LowerBand = MA20 - 2 × STD20
- BollingerPosition = ((Close - LowerBand) / (UpperBand - LowerBand)) × 100

Hypothesis
- The model will learn whether the current price is near the upper or lower Bollinger Band.
- Expected to improve prediction by incorporating volatility and price position.

Accuracy
56.72%

Training Time
264.88 sec

Observation
- Accuracy decreased slightly from 56.79%.
- Feature Importance: 0.0706
- The feature likely overlaps with MA20 Ratio and Volatility20.
- No meaningful improvement in RandomForest.

Decision
- Keep the feature for now.
- Reevaluate after switching to XGBoost.

---

## v27

Model
- XGBoost (Default)

Features
- Same as RandomForest

Accuracy
56.33%

Training Time
17.24 sec

Observation
- Slightly lower accuracy than RandomForest.
- Training speed improved dramatically (~13x faster).
- MA60 Ratio became the most important feature.
- Establishes XGBoost baseline before tuning.

---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---
---