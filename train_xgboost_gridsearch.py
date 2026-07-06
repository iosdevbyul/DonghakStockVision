import pandas as pd

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    classification_report
)

from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import train_test_split

from xgboost import XGBClassifier

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

df = pd.read_csv("dataset.csv")

X = df.drop(columns=["날짜", "Target"])
y = df["Target"]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

model = XGBClassifier(
    random_state=42,
    eval_metric="logloss",
    scale_pos_weight=2.66,
    min_child_weight=1,
    gamma=0,
    subsample=0.8
)

param_grid = {
    "n_estimators": [100, 150],
    "learning_rate": [0.1, 0.15],
    "max_depth": [7, 8],
    "colsample_bytree": [0.6, 0.8, 1.0]
}

grid_search = GridSearchCV(
    estimator=model,
    param_grid=param_grid,
    cv=5,
    scoring="accuracy",
    verbose=2,
    n_jobs=-1,
    refit=True
)

grid_search.fit(X_train, y_train)



best_model = grid_search.best_estimator_

print(f"Best Parameters: {grid_search.best_params_}")
print(f"Best CV Accuracy: {grid_search.best_score_:.4f}")

print()

evaluate_model(
    best_model,
    X_test,
    y_test
)