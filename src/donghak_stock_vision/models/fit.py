"""Optional learning dependencies are loaded only when a qualified population is fitted."""

import warnings
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, Sample
from donghak_stock_vision.signals.dataset import sample_weights


def fit_linear(samples: list[Sample], c: float) -> dict[str, Any]:
    try:
        import numpy as np
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from threadpoolctl import threadpool_limits
    except ImportError as error:
        raise AnalysisError(
            "learning_extra_required: install donghak-stock-vision[learning]"
        ) from error
    x = np.asarray([s.values for s in samples], dtype=np.float64)
    y = np.asarray([s.label for s in samples], dtype=np.int64)
    weights = np.asarray(sample_weights(samples), dtype=np.float64)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        scaler = StandardScaler()
        transformed = scaler.fit_transform(x, sample_weight=weights)
        estimator = LogisticRegression(
            C=c, solver="lbfgs", max_iter=2000, class_weight="balanced", random_state=42
        )
        try:
            estimator.fit(transformed, y, sample_weight=weights)
        except ConvergenceWarning as error:
            raise AnalysisError("convergence_failed") from error
    return {
        "features": list(FEATURES),
        "classes": estimator.classes_.tolist(),
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coefficient": estimator.coef_[0].tolist(),
        "intercept": float(estimator.intercept_[0]),
        "C": c,
        "n_train": len(samples),
    }
