"""Pinned R5 ensemble registration, custody, score and read-only publication."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision import direction_shadow_cli
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.direction_evaluation import publication
from donghak_stock_vision.models.research_v2 import contract
from donghak_stock_vision.storage import direction_ensemble, direction_shadow
from donghak_stock_vision.storage.krx_backfill import write_new

AT = datetime(2026, 11, 29, tzinfo=UTC)


def base(key: str, h: int = 5) -> dict[str, Any]:
    return dict(
        experiment_id=digest(key),
        sha256=digest("model " + key),
        fit_end="2023-06-30",
        created_at="2026-11-27T00:00:00+00:00",
        config=dict(goal=contract(h)),
        protected=dict(start="2024-07-01", end="2025-06-30"),
        baseline=0.45,
        lineage=dict(
            training_population=digest("train"),
            evaluation_population=digest("evaluation"),
            source_owners=[dict(dataset_id=digest("dataset"))],
            exposed_through="2026-06-30",
        ),
    )


def spec(tmp_path: Path) -> dict[str, Any]:
    return dict(
        version=direction_ensemble.VERSION,
        horizon=5,
        members=[
            dict(
                reference=dict(
                    root=str(tmp_path / "model-source"),
                    plan_pin_file=str(tmp_path / "source-pins" / "plan.pin"),
                    experiment_id=key,
                ),
                weight_bps=weight,
            )
            for key, weight in (("rf", 7000), ("lr", 3000))
        ],
    )


def test_registered_contract_is_order_stable_and_reverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(direction_ensemble, "_member", lambda ref, *_: base(ref["experiment_id"]))
    monkeypatch.setattr(
        direction_shadow,
        "model_reference",
        lambda ref, *args, **kwargs: (
            base(str(ref["horizon"]), ref["horizon"]) | dict(reference=ref)
        ),
    )
    ensemble = spec(tmp_path)
    req = dict(
        version="direction_shadow_registration_request_v4",
        source_kind="offline_fixture",
        tickers=["005930"],
        models=[dict(horizon=1), ensemble, dict(horizon=20)],
    )
    reg = direction_shadow.register(req, at=AT)
    meta = reg.to_dict()
    models = meta["models"]
    m = models["5"]
    rev = req | {
        "models": [
            req["models"][0],
            ensemble | {"members": ensemble["members"][::-1]},
            req["models"][2],
        ]
    }
    assert direction_shadow.register(rev, at=AT) == reg
    later = direction_shadow.register(req, at=AT + timedelta(days=1)).to_dict()
    assert m["ensemble_id"] == later["models"]["5"]["ensemble_id"]
    assert not meta["execution_authorized"] and not meta["performance_approved"]
    pin_name = direction_shadow.ensemble_pin_name(models)
    report, pin = tmp_path / "report.json", tmp_path / pin_name
    write_new(report, reg.payload_json)
    write_new(pin, reg.identifier)
    assert direction_shadow.registration(report, pin) == reg

    frozen_view = publication(meta, [])
    assert frozen_view["predictions"] == []
    assert {v["modelId"] for v in frozen_view["models"] if v["horizon"] == 5} == {m["ensemble_id"]}
    assert {v["modelSha256"] for v in frozen_view["models"] if v["horizon"] == 5} == {m["sha256"]}
    monkeypatch.setattr(direction_shadow, "verify", lambda *_: (reg, []))
    audit = direction_shadow.audit({}, tmp_path / "unused.sqlite3", digest("empty"))
    assert audit["models"]["5"] == m and not audit["forecasts"]
    assert audit["independent_memory_eligible"] == 0

    altered = tmp_path / "altered"
    altered.mkdir(mode=0o700)
    m["config"]["members"][0]["weight_bps"] += 1
    changed = FrozenJSON.freeze(meta)
    write_new(altered / "report.json", changed.payload_json)
    write_new(altered / pin_name, changed.identifier)
    with pytest.raises(ValueError, match="direction_registered_model_changed"):
        direction_shadow.registration(altered / "report.json", altered / pin_name)


@pytest.mark.parametrize(
    ("problem", "reason"),
    [
        ("weights", "weight_sum"),
        ("bool", "weight"),
        ("missing", "member_count"),
        ("duplicate", "duplicate_base"),
        ("horizon", "horizon_mismatch"),
        ("population", "population_mismatch"),
    ],
)
def test_rejects_unbound_ensemble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str, reason: str
) -> None:
    def lookup(ref: dict[str, Any], *_: Any) -> dict[str, Any]:
        k = ref["experiment_id"]
        model = base(k, 1 if problem == "horizon" and k == "lr" else 5)
        if problem == "population" and k == "lr":
            model["lineage"]["evaluation_population"] = digest("different")
        return model

    monkeypatch.setattr(direction_ensemble, "_member", lookup)
    req = spec(tmp_path)
    a, b = req["members"]
    if problem == "weights":
        a["weight_bps"] = 6000
    elif problem == "bool":
        a["weight_bps"] = True
    elif problem == "missing":
        req["members"].pop()
    elif problem == "duplicate":
        b["reference"] = a["reference"]
    with pytest.raises(ValueError, match=f"direction_ensemble_{reason}"):
        direction_ensemble.resolve(req, AT, load_checkpoint=False)


def test_score_and_private_source_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def lookup(ref: dict[str, Any], *_: Any) -> dict[str, Any]:
        model = base(ref["experiment_id"])
        model["checkpoint"] = ref["experiment_id"]
        model["versions"] = {}
        model["config"].update(
            candidate=dict(family="random_forest", name="test", value=3, parameters=None),
            mask="v2_legacy",
        )
        return model

    monkeypatch.setattr(direction_ensemble, "_member", lookup)
    monkeypatch.setattr(direction_shadow, "verified_estimator", lambda path, *_: path.name)
    monkeypatch.setattr(
        direction_shadow,
        "predict_candidate",
        lambda _, estimator, __: [0.4 if estimator == "rf" else 0.8],
    )
    req = spec(tmp_path)
    resolved = direction_ensemble.resolve(req, AT, load_checkpoint=False)
    assert direction_shadow._score(
        resolved, "005930", "2026-11-28", (0.0,) * 46, AT
    ) == pytest.approx(0.52)
    assert tmp_path / "source-pins" in direction_ensemble.source_paths(resolved["reference"])
    with pytest.raises(ValueError, match="direction_ensemble_invalid_score"):
        direction_ensemble.average([7000, 3000], [0.4, float("nan")])


def test_registration_cli_rejects_duplicate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(direction_ensemble, "_member", lambda ref, *_: base(ref["experiment_id"]))
    ensemble = spec(tmp_path)
    m = direction_ensemble.resolve(ensemble, AT, load_checkpoint=False)
    reg = FrozenJSON.freeze(dict(version="direction_shadow_registration_v4", models={"5": m}))
    monkeypatch.setattr(direction_shadow, "register", lambda *_: reg)
    req = FrozenJSON.freeze(
        dict(version="direction_shadow_registration_request_v4", models=[ensemble])
    )
    request, request_pin = tmp_path / "request.json", tmp_path / "request.pin"
    write_new(request, req.payload_json)
    write_new(request_pin, req.identifier)
    report = tmp_path / "reports" / "first.json"
    pin = tmp_path / "pins" / direction_shadow.ensemble_pin_name({"5": m})
    args = argparse.Namespace(
        action="register",
        request=request,
        request_pin_file=request_pin,
        report=report,
        report_pin_file=pin,
        journal=None,
    )
    assert direction_shadow_cli.run(args) == 0
    args.report = report.with_name("duplicate.json")
    assert direction_shadow_cli.run(args) == 2
    assert not args.report.exists()
    assert "direction_ensemble_already_registered" in capsys.readouterr().out


def test_ensemble_rejects_exposed_input_and_missing_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="direction_ensemble_base_missing"):
        direction_ensemble._member(
            spec(tmp_path)["members"][0]["reference"]
            | dict(owner="r3", run_id=digest("run"), database_sha256=digest("store")),
            AT,
            False,
        )
    monkeypatch.setattr(direction_ensemble, "_member", lambda ref, *_: base(ref["experiment_id"]))
    m = direction_ensemble.resolve(spec(tmp_path), AT, load_checkpoint=False)
    protected = SimpleNamespace(
        start=date(2024, 7, 1), end=date(2025, 6, 30), to_dict=lambda: m["protected"]
    )
    monkeypatch.setattr(
        direction_shadow, "_protocol", lambda _: (None, SimpleNamespace(final_test=protected))
    )
    request = dict(
        version="direction_shadow_request_v3",
        mode="historical_replay",
        registration="not needed",
        registration_pin_file="not needed",
        research={},
        protocol={},
        operational={},
        calendars={},
        context_window=dict(start="2026-06-01", end="2026-06-29"),
        input_trading_date="2026-06-30",
    )
    with pytest.raises(ValueError, match="direction_ensemble_pre_exposure"):
        direction_shadow._request(request, {"models": {"5": m}})
