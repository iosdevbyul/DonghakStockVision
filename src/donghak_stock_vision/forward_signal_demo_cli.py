"""Executable synthetic capture-to-journal example using original provider parsing."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.storage.forward_capture import DB, run_capture
from donghak_stock_vision.storage.forward_signal import POLICY, generate_signal, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, append_signal, history


def prepare(root: Path, closes: tuple[int, ...]) -> FrozenJSON:
    require(not root.exists(), "demo_requires_new_capture_root")
    require(0 < len(closes) <= 4, "demo_close_budget")
    split = ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 4)),
        ResearchDateRange(date(2025, 7, 7), date(2025, 7, 7)),
        ResearchDateRange(date(2025, 7, 8), date(2025, 7, 8)),
    )
    sources = []
    for i, close in enumerate(closes):
        plan = ForwardCapturePlan(
            "KOSPI",
            "005930",
            date(2025, 7, i + 1),
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
            "start": "2025-07-01",
            "end": "2025-07-04",
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
