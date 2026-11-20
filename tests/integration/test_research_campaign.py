"""Real source/plan/worker checks, deterministic transport-independent campaign tests."""

from __future__ import annotations

import copy
import hashlib
import json
import signal
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_gemma_research import healthy
from tests.integration.test_gemma_research import view as _legacy_view  # noqa: F401
from tests.integration.test_memory_guided_research import original

from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import memory_reasoning
from donghak_stock_vision.models import research_campaign as policy
from donghak_stock_vision.storage import autonomous_runner as runner
from donghak_stock_vision.storage import memory_guided_research as session
from donghak_stock_vision.storage import research_campaign as campaign
from donghak_stock_vision.storage.autonomous_research import DB, prepare, worker

agent_view = _legacy_view


def test_counterbalance_population_dependence_and_internal_gate() -> None:
    cells = [dict(cell_id=digest(i)) for i in range(6)]
    schedule = policy.schedule(cells, 8)
    assert len(schedule) == 144 and len({s["step_id"] for s in schedule}) == 144
    assert {
        tuple(s["arm"] for s in schedule if s["round"] == 1 and s["cell"] == c["cell_id"])
        for c in cells
    } == set(policy.ORDERS)
    fold: dict[str, Any] = dict(
        period=dict(start="2025-01-01", end="2025-06-30"),
        metrics=dict(pr_auc_ap=0.5, balanced_accuracy=0.51, brier_score=0.24, roc_auc=0.52),
        counts=dict(rows=100, dates=40, positive=50, negative=50, tickers=5),
        anchors=20,
    )
    rows: list[dict[str, Any]] = [
        dict(
            arm=a,
            experiment_id=digest([a, i]),
            family="linear",
            mask="v2_daily",
            mean_ap=0.5 + 0.01 * (a == "gemma_memory"),
            baseline_ap_gain=0.01,
            worst_ba=0.51,
            population="same",
            folds=[fold],
        )
        for a in memory_reasoning.ARMS
        for i in range(4)
    ]
    report = policy.analyze(rows, 8)
    assert report["comparisons"][1]["status"] == "INTERNAL_IMPROVEMENT_OBSERVED"
    assert report["comparisons"][1]["uncertaintyInterval"] is None
    changed = copy.deepcopy(rows)
    changed[-1]["population"] = "different"
    assert not policy.analyze(changed, 8)["comparisons"][1]["matched"]
    changed = copy.deepcopy(rows)
    changed[-1]["folds"][0]["anchors"] = 19
    assert policy.analyze(changed, 8)["comparisons"][1]["status"] == "INSUFFICIENT_EVIDENCE"


def test_counterfactual_requires_original_parent_and_bound_metrics(
    agent_view: dict[str, Any],
) -> None:
    from tests.integration.test_gemma_research import proposal

    v = copy.deepcopy(agent_view)
    v.update(cohort="fixture", counterfactual_required=True)
    h = dict(
        experiment_id=digest("old"),
        config=v["candidates"][1]["config"],
        state="completed",
        mean_ap=0.4,
        baseline_ap_gain=-0.1,
        overfit_gap=0.2,
        worst_balanced_accuracy=0.4,
        calibration_delta=0.02,
    )
    v["history"] = [h]
    p = (
        proposal(v)
        | memory_reasoning.FIXED
        | dict(
            horizon=1,
            cohort="fixture",
            problem="Baseline failure",
            failureCondition="No AP gain",
            evidence=[dict(experimentId=h["experiment_id"], metric="mean_ap")],
        )
    )
    with pytest.raises(ValueError, match="counterfactual_parent"):
        memory_reasoning.validate(p, v)
    p["parentExperimentId"] = h["experiment_id"]
    assert memory_reasoning.validate(p, v)
    assert len(memory_reasoning.failure_patterns(v)[0]["observations"]) == 4
    p["evidence"][0]["experimentId"] = "0"  # never an array position/foreign owner
    with pytest.raises(ValueError):
        memory_reasoning.validate(p, v)


def test_campaign_real_worker_recovery_expiry_and_legacy_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req, parent, db = original(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    d = json.loads((parent / "request.json").read_text())
    d["dataset_version"] = "offline-second"
    d["multi_stock"]["cohort"] = "fixture-second"
    second_root = tmp_path / "source-second"
    second_pins = tmp_path / "source-second-pins"
    prepare(d, second_root, second_pins)
    sources = [
        req["source"],
        dict(root=str(second_root), plan_pin_file=str(second_pins / "plan.pin")),
    ]
    protected = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (db, parent / DB, second_root / DB)
    }
    # Root siblings share the real existing reservation parent.
    root = tmp_path / "campaign"
    pins = tmp_path / "campaign-pins"
    request = dict(
        version=campaign.VERSION,
        studies=[dict(source=r, memory=[r]) for r in sources],
        horizons=[1, 5, 20],
        candidate_budget=8,
        llm_budget=12,
        wall_seconds=7200,
        step_seconds=360,
        calibration_steps=3,
        disk_mb=4096,
        model=req["model"],
    )
    pin = campaign.prepare(request, root, pins)
    frozen = campaign.load(root, pin)
    assert len(frozen["schedule"]) == 144 and len(frozen["cells"]) == 6
    assert frozen["cohort_overlap"]["independent"] is False
    assert (
        sum(c["status"] == "READY" for c in frozen["cells"]) == 2
    )  # source genuinely only owns H1
    assert (
        session.load(Path(frozen["cells"][0]["root"]), frozen["cells"][0]["pin"])[
            "effective_budget"
        ]
        == 8
    )

    def forbidden(*a: Any, **kw: Any) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(
        "donghak_stock_vision.storage.gemma_research.process_resources",
        lambda: dict(verified=True, rss_kb=0),
    )
    real_runner = runner.run
    monkeypatch.setattr(
        session, "run_worker", lambda *a, **kw: real_runner(*a, **kw, resource_probe=healthy)
    )

    def local_worker(root: Path, pin: str, attempt: str, **kw: Any) -> dict[str, Any]:
        worker(root, pin, attempt)
        return dict(returncode=0, reason=None, maximum_rss_kb=100, wall_seconds=0.1)

    monkeypatch.setattr(runner, "launch", local_worker)
    calls = []

    def lost_ack(root: Path, pin: str, attempt: str, fd: int, timeout: float) -> dict[str, Any]:
        calls.append(attempt)
        old = signal.getsignal(signal.SIGTERM)
        try:
            campaign.worker(root, pin, attempt)
        finally:
            signal.signal(signal.SIGTERM, old)
        raise OSError("lost controller ack")

    monkeypatch.setattr(campaign, "_spawn", lost_ack)
    monkeypatch.setattr(campaign, "native_ready", lambda p: None)
    with pytest.raises(OSError):
        campaign.run(root, pin)
    recovered = campaign.recover(root, pin)
    assert recovered["attempts"] == 1 and recovered["cells"][0]["arms"]["rule"]["done"] == 1
    campaign.recover(root, pin)
    assert len(calls) == 1

    class Future(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Future:
            return cls.fromtimestamp((datetime.now(UTC) + timedelta(days=1)).timestamp(), tz)

    monkeypatch.setattr(campaign, "datetime", Future)
    ended = campaign.run(root, pin)
    assert ended["lastPause"] == "campaign_wall_budget_exhausted" and len(calls) == 1
    monkeypatch.setattr(campaign, "datetime", datetime)
    report = campaign.report(root, pin)
    pub = campaign.publication(report)
    assert "/private/" not in json.dumps(pub) and "/tmp/" not in json.dumps(pub)
    bad = copy.deepcopy(pub)
    bad["cells"][0]["analysis"]["arms"]["rule"]["ap"]["mean"] = 0.99
    with pytest.raises(ValueError, match="reconstruction"):
        campaign.validate_publication(bad)
    output = tmp_path / "pub"
    output.mkdir(mode=0o700)
    p = output / "campaign.json"
    p.write_text(json.dumps(pub))
    p.chmod(0o600)
    index = output / "index.json"
    index.write_text(
        json.dumps(
            dict(
                version="research_lab_publication_index_v3",
                entries=[],
                campaignReports=[
                    dict(report="campaign.json", sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                ],
            )
        )
    )
    index.chmod(0o600)
    from donghak_stock_vision.storage.research_lab_publication import ResearchLabPublication

    reader = ResearchLabPublication(output, hashlib.sha256(index.read_bytes()).hexdigest())
    assert reader.report_catalog()["total"] == 1 and reader.report(pin) == pub
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in protected.items())


def test_campaign_cli_postponed_annotations() -> None:
    from donghak_stock_vision.cli import parser

    args = parser().parse_args(
        [
            "memory-research",
            "campaign-worker",
            "--root",
            "/tmp/fixture",
            "--campaign-pin-file",
            "/tmp/pin",
            "--attempt-id",
            "abc",
        ]
    )
    assert args.action == "campaign-worker" and args.report is None
