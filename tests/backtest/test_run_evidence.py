"""Synthetic evidence and temporary databases only; no real model or simulation."""

import ast
import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.run_evidence import (
    ACTION_KINDS,
    CATEGORIES,
    BaselineRunEvidence,
    audit_database,
    audit_rows,
    calendar_check,
    check,
    corporate_check,
    dataset_check,
    file_sha,
    identity_check,
    settlement_check,
)
from donghak_stock_vision.backtest.validation import ContractError
from tests.backtest.test_research_contract import bar

START, END = date(2025, 1, 3), date(2025, 1, 7)
UNIVERSE = ("005930",)
H = "a" * 64


def row(day: date = START) -> dict[str, Any]:
    return replace(bar(day), provider="krx").to_dict()


def document(**changes: Any) -> FrozenJSON:
    value = dict(
        source_urls=["https://example.test/official-fixture"],
        source_content_hashes=[H],
        review_reference="synthetic_fixture_review_not_real_evidence",
        days={
            "2025-01-03": "session",
            "2025-01-04": "weekend",
            "2025-01-05": "weekend",
            "2025-01-06": "session",
            "2025-01-07": "session",
        },
        effective_start="2025-01-01",
        effective_end="2025-01-31",
        rule="kospi_cash_equity_T_plus_2_settlement_sessions",
    )
    value.update(changes)
    return FrozenJSON.freeze(value)


@pytest.mark.parametrize(
    "defect",
    ["duplicate", "nonincreasing", "malformed", "missing", "outside", "ticker", "metadata"],
)
def test_bad_sessions_blocked(defect: str) -> None:
    rows = [row(), row(END)]
    if defect == "duplicate":
        rows.append(row())
    if defect == "nonincreasing":
        rows.reverse()
    if defect == "malformed":
        rows[0]["open"] = -1
    if defect == "missing":
        del rows[0]["volume"]
    if defect == "outside":
        rows.append(row(date(2025, 1, 8)))
    if defect == "ticker":
        rows[0]["ticker"] = "000001"
    if defect == "metadata":
        rows[0]["market"] = "KOSDAQ"
    assert audit_rows(rows, UNIVERSE, START, END, ()).status == "BLOCKED_ON_SESSION_ORDERING"


def test_temporary_database_read_only_and_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE bars(ticker TEXT,trading_date TEXT,payload TEXT)")
        for value in (row(), row(END)):
            db.execute(
                "INSERT INTO bars VALUES(?,?,?)",
                (value["ticker"], value["trading_date"], json.dumps(value)),
            )
    before = file_sha(path)
    first, info = audit_database(path, UNIVERSE, START, END, ())
    second, _ = audit_database(path, UNIVERSE, START, END, ())
    assert first == second and first.status == "VERIFIED"
    assert before == info["sha256_before"] == info["sha256_after"] == file_sha(path)
    assert not Path(str(path) + "-wal").exists()
    Path(str(path) + "-wal").write_bytes(b"active")
    with pytest.raises(ContractError, match="active_sqlite_sidecar"):
        audit_database(path, UNIVERSE, START, END, ())


@pytest.mark.parametrize(
    "field", ["db_sha256", "rows_sha256", "universe_sha256", "dataset_id", "rows"]
)
def test_dataset_identity_mismatch(field: str) -> None:
    expected = dict(db_sha256=H, rows_sha256=H, universe_sha256=H, dataset_id="fixture", rows=2)
    actual = dict(expected)
    actual[field] = "changed"
    assert dataset_check(actual, expected).status == "BLOCKED_ON_DATASET_IDENTITY"
    assert dataset_check(expected, expected).status == "VERIFIED"


def test_calendar_and_research_exclusion_not_holiday() -> None:
    d = document()
    observed = {"005930": ["2025-01-03", "2025-01-07"]}
    excluded = (date(2025, 1, 6),)
    assert calendar_check(observed, START, END, excluded, d, d.identifier).status == "VERIFIED"
    assert (
        calendar_check(observed, START, END, (), d, d.identifier).status
        == "BLOCKED_ON_CALENDAR_EVIDENCE"
    )
    value = d.to_dict()
    value["days"]["2025-01-06"] = "holiday"
    wrong = FrozenJSON.freeze(value)
    assert (
        "research_exclusion_is_not_exchange_holiday"
        in calendar_check(observed, START, END, excluded, wrong, wrong.identifier).reasons
    )
    assert calendar_check(observed, START, END, excluded, d, "b" * 64).status != "VERIFIED"


def test_unknown_calendar_day_not_inferred() -> None:
    d = document(days={"2025-01-03": "session", "2025-01-07": "session"})
    result = calendar_check(
        {"005930": ["2025-01-03", "2025-01-07"]}, START, END, (), d, d.identifier
    )
    assert "unclassified_calendar_date" in result.reasons


@pytest.mark.parametrize("condition", ["missing", "unresolved", "incomplete_types", "valid"])
def test_corporate_action_review(condition: str) -> None:
    d: FrozenJSON | None = document(
        dataset_id="fixture",
        universe_sha256=H,
        start=START.isoformat(),
        end=END.isoformat(),
        reviewed_action_kinds=list(ACTION_KINDS),
        unresolved_conditions=[],
        quality_manifest_sha256=H,
    )
    if condition == "missing":
        d = None
    elif condition != "valid":
        assert d is not None
        value = d.to_dict()
        value["unresolved_conditions" if condition == "unresolved" else "reviewed_action_kinds"] = [
            "unknown"
        ]
        d = FrozenJSON.freeze(value)
    result = corporate_check("fixture", H, START, END, d, d.identifier if d else None)
    assert (result.status == "VERIFIED") == (condition == "valid")


def test_settlement_counts_sessions_not_calendar_days_or_research_exclusions() -> None:
    d = document()
    result = settlement_check((START,), d, d.identifier)
    assert result.status == "VERIFIED"
    assert result.details.to_dict()["mapping"] == {"2025-01-03": "2025-01-07"}
    assert settlement_check((END,), d, d.identifier).status == "BLOCKED_ON_SETTLEMENT_EVIDENCE"
    wrong = document(rule="calendar_plus_two")
    assert settlement_check((START,), wrong, wrong.identifier).status != "VERIFIED"
    assert settlement_check((START,), None, None).status != "VERIFIED"


def test_settlement_unsupported_cost_date_and_holiday() -> None:
    d = document(
        days={
            "2026-10-01": "session",
            "2026-10-02": "session",
            "2026-10-03": "weekend",
            "2026-10-04": "weekend",
            "2026-10-05": "holiday",
            "2026-10-06": "session",
        },
        effective_start="2026-01-01",
        effective_end="2026-12-31",
    )
    assert settlement_check((date(2026, 10, 1),), d, d.identifier).status != "VERIFIED"


@pytest.mark.parametrize("field", ["up_sha", "up_fingerprint", "baseline_policy", "quality_policy"])
def test_model_policy_identity_mismatch(field: str) -> None:
    keys = (
        "up_sha down_sha up_fingerprint down_fingerprint lock_id qualification_id "
        "features feature_version label_version signal_policy quality_policy "
        "baseline_policy cost_policy runner_version"
    ).split()
    expected = dict.fromkeys(keys, H)
    actual = dict(expected, **{field: "b" * 64})
    assert identity_check(actual, expected).status == "BLOCKED_ON_ARTIFACT_IDENTITY"
    assert identity_check(expected, expected).status == "VERIFIED"
    expected.pop("up_fingerprint")
    assert "missing_expected:up_fingerprint" in identity_check(actual, expected).reasons


def test_evidence_identity_immutable_and_audit_time_external() -> None:
    checks = tuple(check(c, ["missing"], reference=H) for c in CATEGORIES)
    e = BaselineRunEvidence(checks, FrozenJSON.freeze({"dataset": H}))
    assert e.fingerprint == replace(e).fingerprint
    assert (
        e.fingerprint != replace(e, identities=FrozenJSON.freeze({"dataset": "b" * 64})).fingerprint
    )
    with pytest.raises(FrozenInstanceError):
        e.version = "changed"  # type: ignore[misc]
    with pytest.raises(ContractError):
        replace(e, checks=checks[:-1])
    assert len([c for c in e.checks if c.status != "VERIFIED"]) == len(CATEGORIES)


def test_inspector_has_no_execution_or_pickle_calls() -> None:
    for file in ("run_evidence.py", "inspect_evidence.py"):
        tree = ast.parse((Path("src/donghak_stock_vision/backtest") / file).read_text())
        calls = {
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert not calls & {
            "fit",
            "predict",
            "predict_proba",
            "load",
            "loads_pickle",
            "run",
            "research",
        }


def test_complete_read_only_inspection_without_model_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donghak_stock_vision.backtest.inspect_evidence import inspect
    from donghak_stock_vision.data.learning import digest
    from tests.learning.test_signal_integration import bundle

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("model execution is forbidden in evidence verification")

    monkeypatch.setattr("donghak_stock_vision.models.artifact_resolver.VerifiedHGB.load", forbidden)
    monkeypatch.setattr("donghak_stock_vision.models.inference.HGBInference.score", forbidden)
    resolver = bundle(tmp_path / "models")
    market, analysis = tmp_path / "market.sqlite3", tmp_path / "analysis.sqlite3"
    analysis.write_bytes(b"protected analysis fixture")
    source = [row(), row(END)]
    with sqlite3.connect(market) as db:
        db.execute("CREATE TABLE bars(ticker TEXT,trading_date TEXT,payload TEXT)")
        for value in source:
            db.execute(
                "INSERT INTO bars VALUES(?,?,?)",
                (value["ticker"], value["trading_date"], json.dumps(value)),
            )
    audit = audit_rows(source, UNIVERSE, START, END, ())
    universe = dict(tickers=list(UNIVERSE), count=1)
    policy = dict(
        start=START.isoformat(), end=END.isoformat(), research_calendar=dict(incomplete_dates=[])
    )
    provenance = dict(source_db_sha256=file_sha(market), source_metadata="synthetic fixture")
    identity = dict(
        start=START.isoformat(),
        end=END.isoformat(),
        rows=2,
        universe_count=1,
        universe_sha256=digest(universe),
        policy_sha256=digest(policy),
        availability_sha256=digest({}),
        rows_sha256=audit.details.to_dict()["rows_sha256"],
    )
    manifest: dict[str, Any] = dict(
        dataset_id="oos-" + digest(identity),
        dataset_fingerprint=digest(identity),
        identity=identity,
        provenance_sha256=digest(provenance),
    )
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    for name, metadata in dict(
        manifest=manifest, universe=universe, policy=policy, provenance=provenance, availability={}
    ).items():
        (frozen / f"{name}.json").write_text(json.dumps(metadata))
    up, down = resolver.artifacts
    contract = dict(
        dataset_id=manifest["dataset_id"],
        model_hashes=dict(up=up.checkpoint_sha256, down=down.checkpoint_sha256),
        experiment_lock_id=up.experiment_lock_id,
        qualification_id=up.qualification_id,
        model_parameters=dict(
            up=json.loads(up.parameters_json), down=json.loads(down.parameters_json)
        ),
        versions=dict(up.runtime_versions),
        features=list(up.features),
        feature_version=up.feature_version,
        label_version=up.label_version,
    )
    evaluation = tmp_path / "evaluation-lock.json"
    evaluation.write_text(json.dumps(dict(contract=contract, lock_id=digest(contract))))
    first = inspect(
        market,
        analysis,
        frozen,
        resolver.root,
        evaluation,
        manifest["dataset_id"],
        file_sha(market),
    )
    second = inspect(
        market,
        analysis,
        frozen,
        resolver.root,
        evaluation,
        manifest["dataset_id"],
        file_sha(market),
    )
    assert first.fingerprint == second.fingerprint
    assert first.checks[1].status == first.checks[2].status == "VERIFIED"
    assert first.checks[-1].reasons == (
        "missing_expected:down_fingerprint",
        "missing_expected:up_fingerprint",
    )
    (resolver.root / "up-research-only.pkl").write_bytes(b"tampered synthetic checkpoint")
    failed = inspect(
        market,
        analysis,
        frozen,
        resolver.root,
        evaluation,
        manifest["dataset_id"],
        file_sha(market),
    )
    assert any(r.startswith("artifact_verification_failed") for r in failed.checks[-1].reasons)
