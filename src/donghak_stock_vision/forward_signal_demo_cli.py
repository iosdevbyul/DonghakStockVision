"""Executable synthetic capture-to-journal example using original provider parsing."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.storage.forward_capture import DB, run_capture
from donghak_stock_vision.storage.forward_signal import POLICY, generate_signal, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, append_signal, history


def prepare(
    root: Path,
    closes: tuple[int, ...],
    *,
    research_split: ExplicitResearchSplit | None = None,
    observation_days: tuple[date, ...] | None = None,
) -> FrozenJSON:
    require(not root.exists(), "demo_requires_new_capture_root")
    require(0 < len(closes) <= 4, "demo_close_budget")
    require(
        (research_split is None) == (observation_days is None),
        "explicit_synthetic_days_and_cohort_required_together",
    )
    split = research_split or ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 4)),
        ResearchDateRange(date(2025, 7, 7), date(2025, 7, 7)),
        ResearchDateRange(date(2025, 7, 8), date(2025, 7, 8)),
    )
    days = observation_days or tuple(date(2025, 7, i + 1) for i in range(len(closes)))
    require(
        len(days) == len(closes) and list(days) == sorted(set(days)), "synthetic_observation_dates"
    )
    sources = []
    for i, close in enumerate(closes):
        plan = ForwardCapturePlan(
            "KOSPI",
            "005930",
            days[i],
            "training",
            split,
            split.identifier,
            1,
            65536,
            30,
            1,
            10,
            "offline_fixture",
            None,
            None,
        )

        def handler(
            request: httpx.Request, p: ForwardCapturePlan = plan, price: int = close
        ) -> httpx.Response:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "OutBlock_1": [
                            {
                                "ISU_CD": p.ticker,
                                "BAS_DD": p.day.strftime("%Y%m%d"),
                                "MKT_NM": p.market,
                                "TDD_OPNPRC": str(price),
                                "TDD_HGPRC": str(price),
                                "TDD_LWPRC": str(price),
                                "TDD_CLSPRC": str(price),
                                "ACC_TRDVOL": "10",
                                "ACC_TRDVAL": str(price * 10),
                            }
                        ]
                    }
                ).encode(),
            )

        with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
            owner = run_capture(root, plan, f"synthetic-{i}", client)
        sources.append(
            {
                "plan": plan.document.to_dict(),
                "plan_pin": plan.document.identifier,
                "attempt": f"synthetic-{i}",
                "capture_pin": owner.identifier,
            }
        )
    with (root / DB).open("rb") as stream:
        database_pin = hashlib.file_digest(stream, "sha256").hexdigest()
    request = FrozenJSON.freeze(
        {
            "version": "forward_signal_request_v1",
            "symbol": "005930",
            "market": "KOSPI",
            "currency": "KRW",
            "mode": "offline_fixture",
            "start": days[0].isoformat(),
            "end": days[-1].isoformat() if observation_days is not None else "2025-07-04",
            "calculated_at": datetime.now(UTC).isoformat(),
            "fast": 2,
            "slow": 3,
            "policy": POLICY,
            "max_latest_age_seconds": 3600,
            "database_pin": database_pin,
            "sources": sources,
        }
    )
    with (root / "signal-request.json").open("x") as output:
        output.write(request.payload_json)
    (root / "signal-request.json").chmod(0o600)
    return request


def prepare_prediction_fixture(root: Path) -> tuple[FrozenJSON, FrozenJSON]:
    """Retain original synthetic inputs using the existing collector and horizon."""
    from donghak_stock_vision.data.schema import SEOUL

    today = datetime.now(UTC).astimezone(SEOUL).date()
    day = today - timedelta(days=1)
    days: list[date] = []
    while len(days) < 4:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    target = today + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    # These weekday choices are synthetic fixture dates, not a KRX calendar claim.
    cohort = ExplicitResearchSplit(
        ResearchDateRange(days[0], target),
        ResearchDateRange(target + timedelta(days=1), target + timedelta(days=1)),
        ResearchDateRange(target + timedelta(days=2), target + timedelta(days=2)),
    )
    request = prepare(root, (100, 90, 80, 120), research_split=cohort, observation_days=tuple(days))
    due = datetime.combine(target + timedelta(days=1), datetime.min.time(), UTC)
    policy = FrozenJSON.freeze(
        {
            "version": "forward_prediction_policy_v1",
            "target_trading_date": target.isoformat(),
            "evaluate_not_before": due.isoformat(),
            "outcome_deadline": (due + timedelta(days=3)).isoformat(),
            "max_recording_delay_seconds": 30,
        }
    )
    policy_path = root / "prediction-policy.json"
    with policy_path.open("x") as output:
        output.write(policy.payload_json)
    policy_path.chmod(0o600)
    return request, policy


def demonstrate_prediction(root: Path, journal: Path) -> FrozenJSON:
    """Execute a new, explicitly synthetic forecast using the existing collector."""
    from donghak_stock_vision.storage.prospective_prediction import state

    require(not journal.exists(), "demo_requires_new_journal")
    request, policy = prepare_prediction_fixture(root)
    policy_path = root / "prediction-policy.json"
    appended = append_signal(
        journal,
        root,
        request,
        request.identifier,
        GENESIS,
        datetime.now(UTC),
        prediction_policy=policy,
        prediction_policy_pin=policy.identifier,
    ).to_dict()
    prediction = FrozenJSON.freeze(appended["entry"]["entry"]["prediction"])
    history(journal, appended["head_pin"])
    return FrozenJSON.freeze(
        {
            "version": "forward_prediction_demo_v1",
            "origin": "offline_fixture",
            "request_path": str(root / "signal-request.json"),
            "request_pin": request.identifier,
            "policy_path": str(policy_path),
            "policy_pin": policy.identifier,
            "signal_id": appended["entry"]["entry"]["decision"]["signal_id"],
            "journal_head_pin": appended["head_pin"],
            "prediction": prediction.to_dict(),
            "status": state(prediction, datetime.now(UTC)).to_dict(),
            "live_requests": 0,
        }
    )


def demonstrate(root: Path, journal: Path, outcome: str) -> FrozenJSON:
    require(not journal.exists(), "demo_requires_new_journal")
    closes = {"BUY": (100, 90, 80, 120), "SELL": (100, 110, 120, 80), "HOLD": (100, 100, 100, 100)}[
        outcome
    ]
    request = prepare(root, closes)
    decision = generate_signal(root, request, request.identifier)
    require(
        decision.to_dict()["calculation"]["signal"] == outcome, "synthetic_demo_signal_mismatch"
    )
    appended = append_signal(journal, root, request, request.identifier, GENESIS, datetime.now(UTC))
    head = appended.to_dict()["head_pin"]
    duplicate = append_signal(journal, root, request, request.identifier, head, datetime.now(UTC))
    require(duplicate.to_dict()["duplicate"], "synthetic_demo_idempotency_failure")
    records = history(journal, head)
    verify_signal(root, request, request.identifier, decision, decision.identifier)
    changed = decision.to_dict()
    changed["calculation"]["signal"] = "SELL" if outcome == "BUY" else "BUY"
    forged = FrozenJSON.freeze(changed)
    try:
        verify_signal(root, request, request.identifier, forged, forged.identifier)
    except ValueError as error:
        require(str(error) == "signal_semantic_mismatch", "unexpected_demo_failure")
    else:
        raise ValueError("synthetic_demo_tamper_not_rejected")
    return FrozenJSON.freeze(
        {
            "version": "forward_signal_demo_v1",
            "origin": "offline_fixture",
            "request_path": str(root / "signal-request.json"),
            "request_pin": request.identifier,
            "decision": decision.to_dict(),
            "decision_pin": decision.identifier,
            "journal_head_pin": head,
            "journal_entries": records.to_dict()["total"],
            "duplicate_detected": True,
            "semantic_replay_verified": True,
            "forged_decision_rejected": True,
            "live_requests": 0,
        }
    )
