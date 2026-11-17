"""Offline evidence bookkeeping using real journal, model and receipt validators."""

import copy
import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_shadow_prediction import (
    evaluation_request as evaluation_request,
)
from tests.integration.test_shadow_prediction import offline as offline
from tests.integration.test_shadow_prediction import request_data as request_data
from tests.integration.test_shadow_prediction import shadow_fixture as shadow_fixture

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.evaluation_evidence import calendar_evidence, shadow_rows
from donghak_stock_vision.storage.evaluation_evidence import (
    _inventory,
    freeze_shadow,
    shadow_readiness,
)
from donghak_stock_vision.storage.forward_signal_journal import GENESIS
from donghak_stock_vision.storage.shadow_prediction import events, record, resolve


def test_calendar_union_purge_class_counts_and_no_ticker_replication() -> None:
    rows = [
        dict(ticker=f"{t:06}", day=f"2026-01-{d:02}", end=f"2026-01-{d + 5:02}", label=t % 2)
        for d in range(1, 21)
        for t in range(20)
    ]
    report = calendar_evidence(rows + rows)
    assert report["valid_rows"] == 400 and report["duplicate_rows_collapsed"] == 400
    assert report["nonoverlapping_calendar_anchors"] == 4
    assert report["missing_calendar_anchors"] == 16
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert sum(v["global_anchors"] for v in report["periods"].values()) == 4
    altered = copy.deepcopy(rows[0])
    altered["label"] = 1
    with pytest.raises(ValueError, match="conflicting"):
        calendar_evidence(rows + [altered])
    assert calendar_evidence([])["missing_calendar_anchors"] == 20


def test_ready_counts_do_not_authorize_performance_and_single_class_blocks() -> None:
    rows = [
        dict(ticker="000001", day=f"2026-01-{d:02}", end=f"2026-01-{d:02}", label=d % 2)
        for d in range(1, 31)
    ]
    report = calendar_evidence(rows)
    assert report["status"] == "READY_FOR_EXISTING_EVALUATOR"
    assert not report["performance_approved"] and not report["pit_qualified"]
    for r in rows:
        r["label"] = 1
    assert calendar_evidence(rows)["status"] == "INSUFFICIENT_EVIDENCE"
    with pytest.raises(ValueError):
        calendar_evidence([dict(ticker="000001", day="2026-02-30", end="2026-03-01", label=1)])


def test_inventory_never_selects_sealed_rows_and_requires_source_pin(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript(
            "CREATE TABLE bars(ticker TEXT,trading_date TEXT,payload TEXT); "
            'INSERT INTO bars VALUES ("000001","2024-01-02","INVALID_PROTECTED"),'
            '("000001","2026-01-02","not_loaded");'
        )
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    report = _inventory(path, "bars", pin, dict(start="2024-01-01", end="2024-12-31"), ["000001"])
    assert report["rows_excluding_final_test"] == 1 and report["dates"] == ["2026-01-02"]
    assert not report["final_test_rows_queried"]
    with pytest.raises(ValueError, match="pin|database"):
        _inventory(path, "bars", "0" * 64, dict(start="2024-01-01", end="2024-12-31"), ["000001"])


def test_existing_shadow_pending_outcome_idempotence_and_frozen_model(
    shadow_fixture: tuple[dict[str, Any], datetime, datetime], tmp_path: Path
) -> None:
    d, at, mature = shadow_fixture
    journal = tmp_path / "journal.sqlite3"
    registration = freeze_shadow(d, journal, GENESIS, at - timedelta(seconds=1))
    first = record(
        d, journal, GENESIS, tmp_path / "intents", tmp_path / "pins", clock=lambda: at
    ).to_dict()
    head = first["head_pin"]
    before = journal.read_bytes()
    pending = shadow_readiness(registration, d, journal, head, at=at).to_dict()
    assert pending["retained_stream_pending"] == 5 and pending["new_eligible_evaluation_rows"] == 0
    assert pending["stream_counts"]["fixture_excluded"] == 5
    assert journal.read_bytes() == before
    completed = resolve(
        d, journal, head, tmp_path / "intents", tmp_path / "pins", clock=lambda: mature
    ).to_dict()
    head = completed["head_pin"]
    result = shadow_readiness(registration, d, journal, head, at=mature).to_dict()
    assert result["new_eligible_evaluation_rows"] == 0  # Fixture never becomes real evidence.
    again = resolve(
        d, journal, head, tmp_path / "intents", tmp_path / "pins", clock=lambda: mature
    ).to_dict()
    assert again["head_pin"] == head
    retained = freeze_shadow(d, journal, head, mature)
    assert (
        shadow_readiness(retained, d, journal, head, at=mature).to_dict()["stream_counts"][
            "pre_registration_predictions"
        ]
        == 5
    )
    changed = copy.deepcopy(registration.to_dict())
    changed["conditions"]["threshold"] = 0.6
    with pytest.raises(ValueError, match="frozen_conditions"):
        shadow_readiness(FrozenJSON.freeze(changed), d, journal, head, at=mature)
    changed = copy.deepcopy(registration.to_dict())
    changed["baseline_events_pin"] = "0" * 64
    with pytest.raises(ValueError, match="prefix"):
        shadow_readiness(FrozenJSON.freeze(changed), d, journal, head, at=mature)
    assert len([e for e in events(journal, head) if e["kind"] == "prediction"]) == 5


def test_shadow_counter_keeps_replay_prior_and_real_streams_separate() -> None:
    at = "2026-01-01T00:00:00+00:00"
    p = dict(
        prediction_id=digest("p"),
        ticker="000001",
        generated_at=at,
        mode="research_forward_candidate",
        source_classification="local_provider_capture",
        target_trading_date="2026-01-02",
        outcome_sessions=["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"],
    )
    entries: list[dict[str, Any]] = [dict(kind="prediction", payload=p)]
    rows, counts = shadow_rows(entries, at, 0)
    assert not rows and counts["pending"] == 1
    entries.append(
        dict(
            kind="outcome",
            payload=dict(prediction_id=p["prediction_id"], state="evaluated", actual_label=1),
        )
    )
    rows, counts = shadow_rows(entries, at, 0)
    assert len(rows) == 1 and counts["evaluated"] == 1
    assert not shadow_rows(entries, at, 1)[0]
    entries[0]["payload"]["mode"] = "historical_replay"
    assert shadow_rows(entries, at, 0)[1]["historical_replay_excluded"] == 1


def test_prior_metric_partitions_are_explicitly_already_exposed() -> None:
    from donghak_stock_vision.storage.evaluation_evidence import _prior_metrics

    case = dict(
        horizon=1,
        cohort="c",
        policy="robust",
        stock_policy="heldout",
        selection=dict(state="ABSTAIN"),
        rows=[
            dict(
                ticker="000001", day="2026-01-02", label_end="2026-01-05", label=1, old=0.6, new=0.5
            )
        ],
    )
    result = _prior_metrics([case], 1)
    partition = result[0]["partitions"]["ticker:000001"]
    assert partition["classification"] == "previously_exposed_descriptive_partition"
    assert partition["old"]["pr_auc_ap"] is None
    assert partition["evidence"]["missing_calendar_anchors"] == 19


def test_output_paths_reject_protected_existing_symlink_and_nonindependent_pins(
    tmp_path: Path,
) -> None:
    from donghak_stock_vision.storage.evaluation_evidence import output_paths

    protected = tmp_path / "protected"
    protected.mkdir()
    report = tmp_path / "out" / "report.json"
    pin = tmp_path / "pins" / "report.pin"
    output_paths(report, pin, [protected])
    for bad_report, bad_pin in ((protected / "new.json", pin), (report, report.parent / "pin")):
        with pytest.raises(ValueError, match="separate_private"):
            output_paths(bad_report, bad_pin, [protected])
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="separate_private"):
        output_paths(alias / "new.json", pin, [])
    assert not list(protected.iterdir())


def test_frozen_conditions_include_unchanged_evaluation_criteria() -> None:
    from donghak_stock_vision.models.evaluation_evidence import evidence_policy
    from donghak_stock_vision.storage.evaluation_evidence import _conditions

    d = dict(
        version="research_shadow_request_v1",
        mode="research_forward_candidate",
        source_kind="local_provider_capture",
        tickers=["000001"],
    )
    conditions = _conditions(d, dict(identifier="fixed"))
    assert conditions["evidence_policy"] == evidence_policy()
    assert conditions["evidence_policy"]["minimum_calendar_anchors"] == 20
    assert conditions["evidence_policy"]["minimum_class_rows"] == 10


def test_cli_retains_full_report_once_instead_of_overwriting_with_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test the CLI write envelope only. Real evidence owners are exercised by the
    # receipt/journal tests above and the separate actual KRX availability run.
    from donghak_stock_vision.cli import main

    source = tmp_path / "original"
    source.mkdir()
    pin = tmp_path / "original.pin"
    pin.write_text("0" * 64)
    pin.chmod(0o600)
    value = FrozenJSON.freeze(
        dict(version="offline_report_envelope_fixture", horizons={"1": {"count": 9}})
    )
    monkeypatch.setattr(
        "donghak_stock_vision.storage.evaluation_evidence.availability_audit",
        lambda *a, **kw: value,
    )
    report = tmp_path / "new" / "report.json"
    pins = tmp_path / "new-pins"
    assert (
        main(
            [
                "autonomous-research",
                "evidence-audit",
                "--root",
                str(source),
                "--plan-pin-file",
                str(pin),
                "--report",
                str(report),
                "--pins-root",
                str(pins),
            ]
        )
        == 0
    )
    assert FrozenJSON(report.read_text()) == value
    from donghak_stock_vision.storage.krx_backfill import read_pin

    assert read_pin(pins / "report.pin") == value.identifier
