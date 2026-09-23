"""Safe JSON inference; no sklearn, NumPy or pickle required at inference time."""

import math
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, AnalysisError


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def validate_parameters(parameters: dict[str, Any]) -> None:
    try:
        if parameters["features"] != list(FEATURES) or parameters["classes"] != [0, 1]:
            raise ValueError("feature/class order")
        for key in ("mean", "scale", "coefficient"):
            values = parameters[key]
            if len(values) != len(FEATURES) or any(
                type(v) not in {int, float} or not math.isfinite(v) for v in values
            ):
                raise ValueError("shape or nonfinite parameters")
        if min(parameters["scale"]) <= 0 or not math.isfinite(parameters["intercept"]):
            raise ValueError("scale/intercept")
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError("invalid_model") from error


def score(parameters: dict[str, Any], values: tuple[float, ...]) -> tuple[float, dict[str, Any]]:
    validate_parameters(parameters)
    if len(values) != len(FEATURES) or not all(math.isfinite(v) for v in values):
        raise AnalysisError("invalid_features")
    standardized = [
        (v - m) / s for v, m, s in zip(values, parameters["mean"], parameters["scale"], strict=True)
    ]
    terms = [v * c for v, c in zip(standardized, parameters["coefficient"], strict=True)]
    prediction = sigmoid(sum(terms) + parameters["intercept"])
    contributions = [
        {"feature": name, "value": v, "standardized": z, "contribution": term}
        for name, v, z, term in zip(FEATURES, values, standardized, terms, strict=True)
    ]
    return prediction, {
        "intercept": parameters["intercept"],
        "contributions": contributions,
        "top_positive": sorted(
            (c for c in contributions if c["contribution"] > 0), key=lambda c: -c["contribution"]
        )[:3],
        "top_negative": sorted(
            (c for c in contributions if c["contribution"] < 0), key=lambda c: c["contribution"]
        )[:3],
    }
