"""Research-only inference of already verified, local, version-bound checkpoints."""

import io
import math
import pickle
import pickletools
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.features.research import FEATURES_V2
from donghak_stock_vision.models.service import provenance

# No general-purpose pickle API. Only the concrete classes used by the retained
# estimator_bytes protocol-5 owner; unknown constructors/extensions fail closed.
_ALLOWED = {
    "numpy": {"dtype"},
    "numpy._core.numeric": {"_frombuffer"},
    "numpy._core.multiarray": {"scalar", "_reconstruct"},
    "numpy.random._pickle": {"__generator_ctor", "__bit_generator_ctor"},
    "numpy.random._pcg64": {"PCG64"},
    "numpy.random.bit_generator": {"__pyx_unpickle_SeedSequence", "SeedSequence"},
    "sklearn.ensemble._forest": {"RandomForestClassifier"},
    "sklearn.tree._classes": {"DecisionTreeClassifier"},
    "sklearn.tree._tree": {"Tree"},
    "sklearn.ensemble._hist_gradient_boosting.gradient_boosting": {
        "HistGradientBoostingClassifier"
    },
    "sklearn.preprocessing._label": {"LabelEncoder"},
    "sklearn._loss.loss": {"HalfBinomialLoss"},
    "sklearn._loss._loss": {"CyHalfBinomialLoss"},
    "sklearn._loss.link": {"LogitLink", "Interval"},
    "sklearn.ensemble._hist_gradient_boosting.binning": {"_BinMapper"},
    "sklearn.ensemble._hist_gradient_boosting.predictor": {"TreePredictor"},
}


class _ResearchUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        require(name in _ALLOWED.get(module, set()), "research_checkpoint_constructor_forbidden")
        import numpy as np
        import numpy._core.multiarray as multiarray
        import numpy._core.numeric as numeric
        import numpy.random._pcg64 as pcg64
        import numpy.random._pickle as random_pickle
        import numpy.random.bit_generator as bit_generator
        import sklearn._loss._loss as cy_loss
        import sklearn._loss.link as link
        import sklearn._loss.loss as loss
        import sklearn.ensemble._forest as forest
        import sklearn.ensemble._hist_gradient_boosting.binning as binning
        import sklearn.ensemble._hist_gradient_boosting.gradient_boosting as hist
        import sklearn.ensemble._hist_gradient_boosting.predictor as predictor
        import sklearn.preprocessing._label as label
        import sklearn.tree._classes as tree_classes
        import sklearn.tree._tree as tree

        modules = {
            "numpy": np,
            "numpy._core.numeric": numeric,
            "numpy._core.multiarray": multiarray,
            "numpy.random._pickle": random_pickle,
            "numpy.random._pcg64": pcg64,
            "numpy.random.bit_generator": bit_generator,
            "sklearn.ensemble._forest": forest,
            "sklearn.tree._classes": tree_classes,
            "sklearn.tree._tree": tree,
            "sklearn.ensemble._hist_gradient_boosting.gradient_boosting": hist,
            "sklearn.preprocessing._label": label,
            "sklearn._loss.loss": loss,
            "sklearn._loss._loss": cy_loss,
            "sklearn._loss.link": link,
            "sklearn.ensemble._hist_gradient_boosting.binning": binning,
            "sklearn.ensemble._hist_gradient_boosting.predictor": predictor,
        }
        return getattr(modules[module], name)

    def persistent_load(self, pid: Any) -> Any:
        raise ValueError("research_checkpoint_external_reference_forbidden")


def verified_estimator(
    path: Path, expected_sha256: str, family: str, versions: dict[str, Any]
) -> Any:
    import hashlib

    require(
        family in ("linear", "random_forest", "hist_gradient_boosting"),
        "research_checkpoint_family",
    )
    require(
        path.is_absolute()
        and path.resolve() == path
        and not path.is_symlink()
        and path.is_file()
        and path.stat().st_nlink == 1
        and path.stat().st_size <= 16 * 1024**2,
        "research_checkpoint_file_bound",
    )
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == expected_sha256, "research_checkpoint_pin_mismatch")
    require(provenance()["versions"] == versions, "research_checkpoint_runtime_mismatch")
    ops = list(pickletools.genops(body))
    require(
        bool(ops)
        and ops[0][0].name == "PROTO"
        and ops[0][1] == 5
        and all(op.name not in {"EXT1", "EXT2", "EXT4", "PERSID", "BINPERSID"} for op, _, _ in ops),
        "research_checkpoint_protocol_forbidden",
    )
    stream = io.BytesIO(body)
    model = _ResearchUnpickler(stream).load()
    require(stream.tell() == len(body), "research_checkpoint_trailing_data")
    if family == "linear":
        require(
            type(model) is dict
            and model["features"] == list(FEATURES_V2)
            and model["classes"] == [0, 1],
            "research_checkpoint_linear_contract",
        )
        for key in ("mean", "scale", "coefficient"):
            require(
                len(model[key]) == len(FEATURES_V2) and all(math.isfinite(v) for v in model[key]),
                "research_checkpoint_linear_shape",
            )
        require(
            min(model["scale"]) > 0 and math.isfinite(model["intercept"]),
            "research_checkpoint_linear_finite",
        )
    else:
        expected = (
            ("sklearn.ensemble._forest", "RandomForestClassifier")
            if family == "random_forest"
            else (
                "sklearn.ensemble._hist_gradient_boosting.gradient_boosting",
                "HistGradientBoostingClassifier",
            )
        )
        require(
            (type(model).__module__, type(model).__name__) == expected
            and model.n_features_in_ == len(FEATURES_V2)
            and list(model.classes_) == [0, 1],
            "research_checkpoint_estimator_contract",
        )
        if family == "random_forest":
            require(model.n_jobs == 1, "research_checkpoint_thread_policy")
    return model
