"""Validated read-only R6 plan and ledger projections."""

from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require, sha, text
from donghak_stock_vision.models import autonomous_strategy as policy
from donghak_stock_vision.storage import predictive_research as r3
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, LIMITS, private_root

DIAGNOSTIC_POLICY = "failure_guided_diagnostic_v1"


def _diagnostic_slots(request: dict[str, Any]) -> list[dict[str, Any]]:
    if "diagnostic_slots" not in request:
        return []
    raw = request["diagnostic_slots"]
    require(type(raw) is list, "supervised_diagnostic_schedule")
    slots: list[dict[str, Any]] = raw
    single = request["schedule"] == [1]
    require(
        (single and len(slots) == 1)
        or (
            len(slots) == 2
            and request["schedule"][:2] == [1, 5]
            and len(request["schedule"]) <= 14
            and all(request["schedule"].count(h) <= 7 for h in policy.HORIZONS)
        ),
        "supervised_diagnostic_schedule",
    )
    for horizon, item in zip(request["schedule"][: len(slots)], slots, strict=True):
        fields(
            item,
            "horizon original_candidate_id candidate_id evidence_experiment_ids reason hypothesis"
            + (" matched_experiment_id" if single else ""),
        )
        evidence = item["evidence_experiment_ids"]
        require(
            type(item["horizon"]) is int
            and item["horizon"] == horizon
            and sha(item["original_candidate_id"]) != sha(item["candidate_id"])
            and type(evidence) is list
            and 1 <= len(evidence) <= 12
            and len({sha(key) for key in evidence}) == len(evidence)
            and len(text(item["reason"])) <= 512
            and len(text(item["hypothesis"])) <= 512
            and (not single or sha(item["matched_experiment_id"]) in evidence),
            "supervised_diagnostic_slot_binding",
        )
    return slots


def _valid_limits(request: dict[str, Any]) -> bool:
    return bool(
        type(request["schedule"]) is list
        and (
            2 <= len(request["schedule"]) <= 20
            or (
                request["schedule"] == [1]
                and "diagnostic_slots" in request
                and type(request["max_seconds"]) is int
                and request["max_seconds"] <= 90
                and request["max_failures"] == 1
            )
        )
        and all(type(h) is int and h in policy.HORIZONS for h in request["schedule"])
        and type(request["max_seconds"]) is int
        and 1 <= request["max_seconds"] <= LIMITS["timeout_seconds"]
        and type(request["max_failures"]) is int
        and 1 <= request["max_failures"] <= LIMITS["maximum_attempts"]
    )


def _plan(root: Path, pin: str) -> dict[str, Any]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        plan = reader.get("supervised_plan", pin)
    require(
        plan["version"] == "supervised_research_plan_v1"
        and plan["bounds"] == LIMITS
        and plan["max_worker_seconds"] == 60
        and _valid_limits(plan["request"])
        and all(plan[k] is False for k in r3.FLAGS),
        "supervised_plan_binding",
    )
    slots = _diagnostic_slots(plan["request"])
    if slots or "diagnostics" in plan:
        require(
            bool(slots)
            and plan.get("diagnostics")
            == [
                slot | dict(policy=DIAGNOSTIC_POLICY, selected_at=plan["registered_at"])
                for slot in slots
            ],
            "supervised_diagnostic_plan_binding",
        )
    return plan


def _rows(store: AnalysisStore, kind: str) -> dict[str, dict[str, Any]]:
    return {key: value for key, value in store.all(kind)}


def _ledger(root: Path) -> dict[str, Any]:
    with closing(ReadOnlyAnalysisStore(root / DB)) as s:
        s.connection.execute("BEGIN")
        return dict(
            windows=_rows(s, "supervised_window"),
            closures={v["window"]: v for _, v in s.all("supervised_close")},
            stops={v["window"] for _, v in s.all("supervised_stop")},
            attempts=_rows(s, "supervised_attempt"),
            audited={v["attempt"]: v for _, v in s.all("supervised_audit")},
            decisions=sorted(
                _rows(s, "supervised_decision").items(), key=lambda pair: pair[1]["index"]
            ),
            completed={v["decision"]: v for _, v in s.all("supervised_completed")},
            checkpoints={v["decision"]: v for _, v in s.all("supervised_checkpoint")},
            trials={v["experiment_id"]: v for _, v in s.all("predictive_research_trial")},
        )
