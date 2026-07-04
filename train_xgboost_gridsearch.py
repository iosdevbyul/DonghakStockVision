import pandas as pd

from sklearn.metrics import accuracy_score
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import train_test_split

from xgboost import XGBClassifier

df = pd.read_csv("dataset.csv")
print(df.columns)

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
    eval_metric="logloss"
)

param_grid = {
    "n_estimators": [50, 100, 150],
    "learning_rate": [0.05, 0.1, 0.15],
    "max_depth": [6, 7, 8]
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

#accuracy = best_model.score(X_test, y_test)
prediction = best_model.predict(X_test)

accuracy = accuracy_score(y_test, prediction)

print(f"Best Parameters: {grid_search.best_params_}")
print(f"Best CV Accuracy: {grid_search.best_score_:.4f}")
print(f"Test Accuracy: {accuracy:.4f}")