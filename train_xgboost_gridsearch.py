import pandas as pd
import matplotlib.pyplot as plt
import shap

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    classification_report,
    precision_score,
    recall_score,
    f1_score
)

from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score, f1_score


from xgboost import XGBClassifier

USE_DEV_DATA = False #True


def show_feature_importance(model, feature_names):

    importance = model.feature_importances_

    feature_importance = pd.DataFrame({
        "Feature": feature_names,
        "Importance": importance
    })

    # 중요도 높은 순으로 정렬
    feature_importance = feature_importance.sort_values(
        by="Importance",
        ascending=False
    )

    print()
    print("===== Feature Importance =====")
    print(feature_importance)

    # CSV 저장
    feature_importance.to_csv(
        "feature_importance.csv",
        index=False
    )

    # 그래프 출력
    plt.figure(figsize=(10, 8))

    plt.barh(
        feature_importance["Feature"][::-1],
        feature_importance["Importance"][::-1]
    )

    plt.xlabel("Importance")
    plt.title("XGBoost Feature Importance")

    plt.tight_layout()
    plt.show()

def show_shap_summary(model, X):

    explainer = shap.TreeExplainer(model)

    shap_values = explainer.shap_values(X)

    shap.summary_plot(
        shap_values,
        X,
        show=False
    )

    plt.tight_layout()
    plt.savefig("shap_summary.png", dpi=300)
    plt.close()

    print("SHAP 저장 완료")

def evaluate_model(model, X_test, y_test):

    prediction = model.predict(X_test)

    accuracy = accuracy_score(
        y_test,
        prediction
    )

    print(f"Test Accuracy: {accuracy:.4f}")

    print()

    print(confusion_matrix(
        y_test,
        prediction
    ))

    print()

    print(classification_report(
        y_test,
        prediction
    ))
    


def show_prediction_probability(model, X_test):

    probability = model.predict_proba(X_test)

    result = pd.DataFrame({
        "UpProbability": probability[:, 1],
        "DownProbability": probability[:, 0]
    })

    print()
    print("===== Prediction Probability =====")

    print(result.head(20))


from sklearn.metrics import precision_score, recall_score, f1_score

def evaluate_threshold(model, X_test, y_test):

    probability = model.predict_proba(X_test)[:, 1]

    print()
    print("===== Threshold Evaluation =====")

    print(
        f"{'Threshold':<10}"
        f"{'Buy':<10}"
        f"{'Precision':<12}"
        f"{'Recall':<12}"
        f"{'F1':<12}"
    )

    for threshold in [
        0.50,
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
        0.80,
        0.85,
        0.90
    ]:

        prediction = (
            probability >= threshold
        ).astype(int)

        buy_count = prediction.sum()

        precision = precision_score(
            y_test,
            prediction,
            zero_division=0
        )

        recall = recall_score(
            y_test,
            prediction,
            zero_division=0
        )

        f1 = f1_score(
            y_test,
            prediction,
            zero_division=0
        )

        print(
            f"{threshold:<10.2f}"
            f"{buy_count:<10}"
            f"{precision:<12.4f}"
            f"{recall:<12.4f}"
            f"{f1:<12.4f}"
        )

df = pd.read_csv("dataset.csv")

if USE_DEV_DATA:
    df = df.tail(1_000_000)

# X = df.drop(columns=["날짜", "Target"])
X = df.drop(
    columns=[
        "날짜",
        "ticker",
        "name",
        "Target"
    ]
)
y = df["Target"]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

# model = XGBClassifier(
#     random_state=42,
#     eval_metric="logloss",
#     scale_pos_weight=2.66,
#     min_child_weight=1,
#     gamma=0,
#     subsample=0.8
# )

model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8,
    n_estimators=150,
    learning_rate=0.15,
    max_depth=8,
    colsample_bytree=1.0
)

model.fit(X_train, y_train)

# param_grid = {
#     "n_estimators": [100, 150],
#     "learning_rate": [0.1, 0.15],
#     "max_depth": [7, 8],
#     "colsample_bytree": [0.6, 0.8, 1.0]
# }

# grid_search = GridSearchCV(
#     estimator=model,
#     param_grid=param_grid,
#     cv=5,
#     scoring="accuracy",
#     verbose=2,
#     n_jobs=-1,
#     refit=True
# )

# grid_search.fit(X_train, y_train)



# best_model = grid_search.best_estimator_

# print(f"Best Parameters: {grid_search.best_params_}")
# print(f"Best CV Accuracy: {grid_search.best_score_:.4f}")

print()

evaluate_model(
    model,
    X_test,
    y_test
)

show_feature_importance(
    model,
    X_train.columns
)

sample = X_train.sample(
    n=5000,
    random_state=42
)

show_shap_summary(
    model,
    sample
)

show_prediction_probability(
    model,
    X_test
)

evaluate_threshold(
    model,
    X_test,
    y_test
)