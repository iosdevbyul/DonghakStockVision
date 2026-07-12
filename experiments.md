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
## v28

Model
- XGBoost

Experiment
- Tune n_estimators

Results

| n_estimators | Accuracy | Time |
|--------------|----------|------|
| 100 | 56.33% | 17.16 sec |
| 300 | 55.89% | 19.51 sec |
| 500 | 55.61% | 21.37 sec |
| 1000 | 55.04% | 27.42 sec |

Observation

- Accuracy decreased as the number of trees increased.
- The model appears to overfit with too many trees.
- n_estimators=100 selected for future experiments.

---

## v29

Model
- XGBoost

Experiment
- Tune learning_rate

Results

| learning_rate | Accuracy |
|---------------|----------|
| 0.30 | 56.33% |
| 0.10 | 56.62% |
| 0.05 | 56.54% |
| 0.01 | 56.36% |

Observation

- Best performance at learning_rate=0.1.
- Lower learning rates increasingly concentrated importance on MA60 Ratio.
- Very low learning rates likely require more trees.
- Selected learning_rate=0.1 for future experiments.

---

## v30

Model
- XGBoost

Experiment
- Tune max_depth

Parameters
- n_estimators = 100
- learning_rate = 0.1

Results

| max_depth | Accuracy | Time |
|-----------|----------|------|
| 3 | 56.47% | 15.99 sec |
| 5 | 56.57% | 16.29 sec |
| 7 | 56.74% | 16.52 sec |
| 10 | 56.67% | 18.30 sec |

Observation

- Best accuracy at max_depth=7.
- Deeper trees beyond 7 did not improve performance.
- Shallower trees relied heavily on MA60 Ratio.
- Deeper trees distributed importance across more features.

Decision

- Selected:
  - n_estimators = 100
  - learning_rate = 0.1
  - max_depth = 7

---

## vX - XGBoost GridSearchCV v1

Features
- 거래량비율
- MA20비율
- MA60비율
- MA120비율
- RSI
- HIGH20비율
- Volatility20
- Momentum20
- HIGH252비율
- MA20_MA60_Gap
- MA60_MA120_Gap
- BollingerPosition

GridSearch
- n_estimators: [50, 100, 150]
- learning_rate: [0.05, 0.1, 0.15]
- max_depth: [6, 7, 8]

Best Parameters
- n_estimators = 150
- learning_rate = 0.15
- max_depth = 8

Best CV Accuracy
- 73.22%

Test Accuracy
- 73.28%

---

vX - XGBoost GridSearchCV + scale_pos_weight

Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- scale_pos_weight = 2.66

Accuracy
- 57.45%

Precision
- 0.34

Recall
- 0.59

F1-score
- 0.43

Result
- Accuracy는 감소했지만 Recall이 크게 향상되어 상승 종목 탐지 성능이 개선됨.

---

v11 - XGBoost + min_child_weight

Parameters
- n_estimators : [50,100,150]
- learning_rate : [0.05,0.1,0.15]
- max_depth : [6,7,8]
- min_child_weight : [1,3,5,10]
- scale_pos_weight = 2.66

Best
- n_estimators = 150
- learning_rate = 0.15
- max_depth = 8
- min_child_weight = 1

Accuracy
57.45%

Precision
0.34

Recall
0.59

F1
0.43

Conclusion
min_child_weight는 현재 데이터셋에서 성능 향상을 가져오지 않았으며, 기본값(1)이 최적이었다.

---

v13 - XGBoost + subsample

Parameters
- n_estimators : [100, 150]
- learning_rate : [0.1, 0.15]
- max_depth : [7, 8]
- subsample : [0.6, 0.8, 1.0]

Fixed
- scale_pos_weight = 2.66
- gamma = 0
- min_child_weight = 1

Best
- n_estimators = 150
- learning_rate = 0.15
- max_depth = 8
- subsample = 0.8

Accuracy
57.45%

Precision
0.34

Recall
0.59

F1
0.43

Conclusion
subsample=0.8이 가장 좋은 성능을 보였다. 전체 데이터를 사용하는 것보다 일부 데이터를 랜덤하게 사용하는 것이 일반화 성능에 약간 더 유리했다.

---

v14 - XGBoost + colsample_bytree

Parameters
- n_estimators : [100,150]
- learning_rate : [0.1,0.15]
- max_depth : [7,8]
- colsample_bytree : [0.6,0.8,1.0]

Fixed
- scale_pos_weight = 2.66
- gamma = 0
- min_child_weight = 1
- subsample = 0.8

Best
- n_estimators = 150
- learning_rate = 0.15
- max_depth = 8
- colsample_bytree = 1.0

Accuracy
57.45%

Precision
0.34

Recall
0.59

F1
0.43

Conclusion
모든 Feature를 사용하는 것이 가장 좋은 성능을 보였다.

---

## vX

Feature
- MACD
- MACDSignal
- MACDHistogram 추가

Model
- XGBoost + GridSearchCV

Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

Accuracy
- CV : 57.86%
- Test : 57.81%

Result
- MACD Feature 유지

---

## v13

### Added Feature
- MACD

### Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

### Result
CV Accuracy : 57.86%
Test Accuracy : 57.81%

---

## v14

### Added Feature
- ATR

### Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

### Result
CV Accuracy : 58.08%
Test Accuracy : 58.00%

---

## v15

### Added Feature
- OBV

### Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

### Result
CV Accuracy : 59.08%
Test Accuracy : 59.13%

---

## v8

Features
- 거래량비율
- MA20비율
- MA60비율
- MA120비율
- RSI
- HIGH20비율
- Volatility20
- Momentum20
- HIGH252비율
- MA20_MA60_Gap
- MA60_MA120_Gap
- BollingerPosition
- MACD
- ATR
- OBV
- ADX
- MFI
- CCI

Model
- XGBoost
- GridSearchCV

Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

Accuracy
- CV Accuracy : 59.28%
- Test Accuracy : 59.27%

Result
- CCI 추가 후 소폭 성능 향상 (+0.02%)

---

## v9

Features
- Previous Features
- Williams %R

Model
- XGBoost
- GridSearchCV

Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

Accuracy
- CV Accuracy : 59.22%
- Test Accuracy : 59.20%

Result
- Williams %R 추가
- 성능 변화 거의 없음

---

## v10

Features
- Previous Features
- Gap

Removed
- Williams %R

Model
- XGBoost
- GridSearchCV

Best Parameters
- learning_rate = 0.15
- max_depth = 8
- n_estimators = 150
- colsample_bytree = 1.0

Accuracy
- CV Accuracy : 59.29%
- Test Accuracy : 59.14%

Result
- Gap Feature 추가
- Williams %R 제거
- 성능 향상 없음

---


## v9

### Added Features
- K
- D
- J (KDJ)

### Result

Best CV Accuracy
59.22%

Test Accuracy
59.16%

### Decision

Remove

Reason:
- Test Accuracy was almost unchanged.
- CV Accuracy decreased from 59.29% to 59.22%.
- No meaningful improvement.
---

## v13

### Added Feature
- CCI

### Why
- 가격이 평균에서 얼마나 벗어났는지 나타내는 지표
- RSI와 다른 관점의 모멘텀 정보를 기대

### Result
CV Accuracy
0.5925 → 0.5920

Test Accuracy
0.5920 → 0.5918

### Decision
Remove

CCI는 기존 Feature들과 중복되는 정보가 많거나,
현재 데이터셋에서는 성능 향상에 기여하지 못함.

---

## v14

### Added Features
- VWAPRatio
- ATRRatio
- VolumeSpike
- LOW20비율
- Position60
- Position120

### Why
- 가격과 거래량을 함께 반영하는 Feature 추가
- 변동성을 종목별 가격 수준에 맞게 정규화
- 중장기 가격 위치 정보 추가

### Result

CV Accuracy
0.5925 → 0.6005

Test Accuracy
0.5920 → 0.6013

Precision (Class 1)
0.35 → 0.36

Recall (Class 1)
0.60 → 0.61

Weighted F1
0.61 → 0.62

### Decision

Keep

여러 Feature를 동시에 추가한 결과 약 0.93%의 Accuracy 향상을 확인.
다음 단계에서는 Ablation Study를 통해 각 Feature의 기여도를 분석한다.

---

## v14

### Added Features
- VWAPRatio
- ATRRatio
- VolumeSpike
- LOW20비율
- Position60
- Position120
- DonchianPosition

### Analysis
- Added XGBoost Feature Importance visualization
- Export feature importance to CSV

### Result

Best CV Accuracy
0.6005

Test Accuracy
0.6013

### Decision

Keep all newly added features.

Feature Importance analysis showed that:
- Volatility20 is the most influential feature.
- ATRRatio is one of the top-ranked newly added features.
- LOW20비율 and Position120 also contribute significantly.

---

## v15

### Added Features

- VWAPRatio
- ATRRatio
- VolumeSpike
- LOW20비율
- Position60
- Position120
- DonchianPosition

### Model

XGBoost + GridSearchCV

Best Parameters

```python
{
    "colsample_bytree": 1.0,
    "learning_rate": 0.15,
    "max_depth": 8,
    "n_estimators": 150
}

---

# v10

Features
- Removed duplicated features
- Added Feature Importance visualization
- Added SHAP Summary visualization
- Added prediction probability output
- Optimized SHAP execution using 5,000 sampled rows
- Saved SHAP summary as image file

Best Parameters
- n_estimators: 150
- learning_rate: 0.15
- max_depth: 8
- colsample_bytree: 1.0

Accuracy
- CV Accuracy: 60.05%
- Test Accuracy: 60.13%

Observations
- SHAP confirmed Volatility20 as the most influential feature.
- ATRRatio, Position120, 거래량비율, OBV, ATR were also highly influential.
- Feature Importance and SHAP rankings were largely consistent.
- Sampling 5,000 rows greatly reduced SHAP execution time while preserving interpretability.

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