"""A finite manual supervisor for retained R3/R4 experiments."""

import json
import os
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require, sha, text
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models import autonomous_strategy as policy
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.models.stock_generalization import catalog
from donghak_stock_vision.storage import autonomous_strategy as r4
from donghak_stock_vision.storage import predictive_research as r3
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, LIMITS, clock, private_root
from donghak_stock_vision.storage.failure_feedback_source import outcome_panels, source_owner
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason
from donghak_stock_vision.storage.research_studies import _file_sha256

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


def _owners(
    request: dict[str, Any],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    previous = Path(request["r4_root"])
    pin = read_pin(Path(request["r4_plan_pin_file"]))
    plan = r4._plan(previous, pin)
    original, source, trials = r4._source(plan)
    # Memory reads development trials only.
    with closing(ReadOnlyAnalysisStore(previous / DB)) as store:
        result = store.connection.execute(
            "SELECT id,json_extract(payload,'$.plan_id'),json_extract(payload,'$.unique_trials') "
            "FROM analysis_artifacts WHERE kind='autonomous_strategy_result'"
        ).fetchall()
        require(len(result) == 1 and result[0][1] == pin, "supervised_r4_result_binding")
        prior = json.loads(result[0][2])
    return original, source, trials, prior, str(result[0][0])


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


def _inherit(root: Path, inherited: dict[str, Any], source_hash: str) -> dict[str, Any]:
    fields(inherited, "root plan_pin_file")
    parent = Path(inherited["root"])
    require(parent != root and parent.parent == root.parent, "supervised_parent_scope")
    pin = read_pin(Path(inherited["plan_pin_file"]))
    before = _file_sha256(parent / DB)
    prior = _plan(parent, pin)
    state = _ledger(parent)
    require(
        prior["r4_hash"] == source_hash
        and bool(state["completed"])
        and state["windows"].keys() == state["closures"].keys()
        and _file_sha256(parent / DB) == before,
        "supervised_parent_not_closed",
    )
    earlier = prior.get("inherited", {})
    trials = earlier.get("trials", []) + [r["trial"] for r in state["completed"].values()]
    seen = earlier.get("candidate_ids", []) + [
        d["candidate"]["experiment_id"] for _, d in state["decisions"]
    ]
    require(len(seen) == len(set(seen)) <= 40, "supervised_parent_history_bound")
    return dict(
        root=str(parent),
        plan_id=pin,
        database_hash=before,
        trials=trials,
        candidate_ids=seen,
    )


def register(request: dict[str, Any], root: Path, pins: Path) -> str:
    fields(
        {
            key: value
            for key, value in request.items()
            if key not in {"previous_run", "diagnostic_slots"}
        },
        "version r4_root r4_plan_pin_file r5_request r5_request_pin_file "
        "schedule max_seconds max_failures",
    )
    require(
        request["version"] == "supervised_research_request_v1"
        and _valid_limits(request)
        and root.parent == Path(request["r4_root"]).parent
        and all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not pins.is_relative_to(root)
        and not root.is_relative_to(pins),
        "supervised_plan_bounds",
    )
    slots = _diagnostic_slots(request)
    previous_db = Path(request["r4_root"]) / DB
    previous_hash = _file_sha256(previous_db)
    original, source, prior_r3, prior_r4, result_id = _owners(request)
    original_hash = _file_sha256(original / DB)
    require(
        previous_hash == _file_sha256(previous_db)
        and [source_owner(v) for v in source["sources"]] == source["sources"],
        "supervised_source_changed",
    )
    inherited = (
        _inherit(root, request["previous_run"], previous_hash) if "previous_run" in request else {}
    )
    studied = {digest(t["config"]) for t in prior_r3 + prior_r4} | set(
        inherited.get("candidate_ids", [])
    )
    pools = {
        str(h): [
            dict(config=c, experiment_id=digest(c))
            for cfg in catalog([h])
            if cfg["stock_policy"] == "heldout"
            and digest(c := cfg | dict(training_size=16)) not in studied
        ]
        for h in policy.HORIZONS
    }
    require(
        all(len(pools[str(h)]) >= request["schedule"].count(h) for h in policy.HORIZONS),
        "supervised_catalog_budget",
    )
    plan = dict(
        version="supervised_research_plan_v1",
        request=request,
        registered_at=clock(),
        r3_hash=original_hash,
        r4_hash=previous_hash,
        r4_result_id=result_id,
        inherited=inherited,
        r2_feedback_id=source["feedback_id"],
        r1_origin_ids=r4._plan(
            Path(request["r4_root"]), read_pin(Path(request["r4_plan_pin_file"]))
        )["r1_origin_ids"],
        max_worker_seconds=60,
        code=provenance(),
        pools=pools,
        bounds=LIMITS,
        windows=source["windows"],
        source_owners=source["sources"],
        **r3.FLAGS,
    )
    if slots:
        _bind_diagnostics(plan, slots, original, source, prior_r3, prior_r4)
    root.mkdir(mode=0o700)
    (root / "models").mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        pin = store.put("supervised_plan", plan)
    write_new(pins / "plan.pin", pin)
    return pin


def _bind_diagnostics(
    plan: dict[str, Any],
    slots: list[dict[str, Any]],
    source_root: Path,
    source: dict[str, Any],
    prior_r3: list[dict[str, Any]],
    prior_r4: list[dict[str, Any]],
) -> None:
    prior = prior_r3 + prior_r4 + plan["inherited"].get("trials", [])
    ranking_plan = plan | {"development_tickers": source["development_tickers"]}
    pinned = []
    for index, slot in enumerate(slots):
        h = slot["horizon"]
        evidence = {
            trial["experiment_id"]
            for trial in prior
            if trial["state"] == "completed" and trial["config"]["goal"]["horizon"] == h
        }
        require(
            set(slot["evidence_experiment_ids"]) <= evidence,
            "supervised_diagnostic_source_binding",
        )
        original = _select(
            ranking_plan,
            digest(plan),
            h,
            index,
            dict(decisions=[], completed={}),
            source_root,
            prior_r3,
            prior_r4,
        )["candidate"]["experiment_id"]
        require(original == slot["original_candidate_id"], "supervised_diagnostic_original_choice")
        candidate = next(
            (c for c in plan["pools"][str(h)] if c["experiment_id"] == slot["candidate_id"]),
            None,
        )
        require(candidate is not None, "supervised_diagnostic_candidate_not_available")
        if matched := slot.get("matched_experiment_id"):
            reference = next(
                (
                    t
                    for t in plan["inherited"].get("trials", [])
                    if t["experiment_id"] == matched and t["state"] == "completed"
                ),
                None,
            )
            assert candidate is not None
            require(
                reference is not None
                and candidate["config"]["mask"] != reference["config"]["mask"]
                and candidate["config"]
                == reference["config"] | {"mask": candidate["config"]["mask"]},
                "supervised_matched_mask_contract",
            )
        pinned.append(slot | dict(policy=DIAGNOSTIC_POLICY, selected_at=plan["registered_at"]))
    plan["diagnostics"] = pinned


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


def _store(root: Path, kind: str, row: dict[str, Any]) -> str:
    with closing(AnalysisStore(root / DB)) as writer:
        return writer.put("supervised_" + kind, row)


def _select(
    plan: dict[str, Any],
    pin: str,
    h: int,
    index: int,
    ledger: dict[str, Any],
    origin: Path,
    prior_r3: list[dict[str, Any]],
    prior_r4: list[dict[str, Any]],
) -> dict[str, Any]:
    names = sorted(plan["development_tickers"])
    groups = [names[::2], names[1::2]]
    memory = r4._memory(origin, dict(groups=groups), prior_r3, h)
    new_history = prior_r4 + plan.get("inherited", {}).get("trials", [])
    memory.extend(t for t in new_history if t["config"]["goal"]["horizon"] == h)
    control = r3._control(memory, h)
    own = [control]
    for key, d in ledger["decisions"]:
        if d["horizon"] == h and key in ledger["completed"]:
            t = ledger["completed"][key]["trial"]
            own.append(t)
    used = {d["candidate"]["experiment_id"] for _, d in ledger["decisions"]}
    available = [p for p in plan["pools"][str(h)] if p["experiment_id"] not in used]
    choice, reason = policy.choose(available, own, memory, control, h)
    without_latest, _ = policy.choose(
        available, own[:-1] if len(own) > 1 else own, memory, control, h
    )
    diagnostics = plan.get("diagnostics", [])
    prescribed = diagnostics[index] if index < len(diagnostics) else None
    if prescribed is not None:
        require(
            prescribed["horizon"] == h
            and choice["experiment_id"] == prescribed["original_candidate_id"],
            "supervised_diagnostic_original_choice",
        )
        available_by_id = {c["experiment_id"]: c for c in available}
        require(
            prescribed["candidate_id"] in available_by_id,
            "supervised_diagnostic_candidate_not_available",
        )
        choice = available_by_id[prescribed["candidate_id"]]
        reason = reason | {"diagnostic": prescribed}
    return dict(
        plan_id=pin,
        index=index,
        horizon=h,
        candidate=choice,
        parent=own[-1]["experiment_id"],
        previous_result=own[-1]["experiment_id"] if len(own) > 1 else None,
        without_latest=without_latest["experiment_id"],
        latest_changed_choice=prescribed is None
        and choice["experiment_id"] != without_latest["experiment_id"],
        rationale=reason,
        control=control,
        groups=groups,
        r2_feedback_id=plan["r2_feedback_id"],
        r1_origin_ids=plan["r1_origin_ids"],
        r3_result_id=r4.R3_ID,
        r4_result_id=plan["r4_result_id"],
        selected_at=clock(),
    )


def _checkpoint(
    root: Path, decision: str, result: dict[str, Any], evaluation_population: str
) -> None:
    _store(
        root,
        "checkpoint",
        {
            k: result[k]
            for k in (
                "experiment_id",
                "config",
                "training_population",
                "checkpoint",
                "model_id",
                "cache_source",
                "fit_seconds",
            )
        }
        | dict(decision=decision, evaluation_population=evaluation_population),
    )


def worker(root: Path, pin: str, attempt_id: str) -> None:
    cpu = time.process_time()
    plan = _plan(root, pin)
    request = plan["request"]
    require(plan["code"] == provenance(), "supervised_code_changed")
    state = _ledger(root)
    attempt = state["attempts"][attempt_id]
    require(attempt["plan_id"] == pin, "supervised_attempt_binding")
    origin, source, prior_r3, prior_r4, result_id = _owners(request)
    require(
        result_id == plan["r4_result_id"]
        and _file_sha256(origin / DB) == plan["r3_hash"]
        and _file_sha256(Path(request["r4_root"]) / DB) == plan["r4_hash"]
        and [source_owner(v) for v in source["sources"]] == source["sources"],
        "supervised_source_binding",
    )
    inherited = plan.get("inherited", {})
    if inherited:
        require(
            _file_sha256(Path(inherited["root"]) / DB) == inherited["database_hash"],
            "supervised_parent_changed",
        )
    index = attempt["index"]
    diagnostics = plan.get("diagnostics", [])
    require(not diagnostics or index < len(diagnostics), "supervised_diagnostic_approval_bound")
    h = request["schedule"][index]
    existing = state["decisions"]
    if index < len(existing):
        key, choice = existing[index]
        require(key not in state["completed"], "supervised_step_already_complete")
    else:
        require(index == len(existing), "supervised_step_order")
        choice = _select(
            plan | dict(development_tickers=source["development_tickers"]),
            pin,
            h,
            index,
            state,
            origin,
            prior_r3,
            prior_r4,
        )
        key = _store(root, "decision", choice)
    train = r3._train(source, h)[16]
    dev = outcome_panels(source["sources"][1], "calibration", source["development_tickers"], (h,))[
        h
    ]
    train_pin = digest([v.to_dict() for v in train])
    evaluation_pin = digest([v.to_dict() for v in dev[0]])
    control = choice["control"]
    config = choice["candidate"]["config"]
    experiment = digest(dict(plan_id=pin, config=config, train=train_pin))
    require(
        choice["plan_id"] == pin
        and choice["horizon"] == h
        and choice["index"] == index
        and train_pin == control["training_population"]
        and evaluation_pin == control["evaluation_population"],
        "supervised_population_binding",
    )
    if diagnostics and (matched := diagnostics[index].get("matched_experiment_id")):
        reference = next(t for t in inherited["trials"] if t["experiment_id"] == matched)
        require(
            train_pin == reference["training_population"]
            and evaluation_pin == reference["evaluation_population"],
            "supervised_matched_population",
        )
    prepared = state["checkpoints"].get(key)
    trial = state["trials"].get(experiment)
    if trial is None:
        require(
            prepared is not None or not (root / f"models/{experiment}.checkpoint").exists(),
            "supervised_unsigned_checkpoint",
        )

        def checkpoint_ready(result: dict[str, Any]) -> bool:
            if prepared is None:
                _checkpoint(root, key, result, evaluation_pin)
            return bool(attempt["checkpoint_only"])

        trial_plan = source | dict(code=plan["code"])
        cache = r3._r2_cache(trial_plan) if diagnostics and h == 5 and prepared is None else {}
        timing: dict[str, float] = {}
        trial = r3._trial(
            root,
            trial_plan,
            pin,
            config,
            train,
            dev,
            cache,
            choice["parent"],
            "supervised_research_trial",
            timings=timing,
            prepared=prepared,
            checkpoint_ready=checkpoint_ready,
        )
        if trial["state"] == "checkpointed":
            _store(
                root,
                "yield",
                dict(
                    attempt=attempt_id,
                    decision=key,
                    reason="checkpoint_pause",
                    cpu_seconds=time.process_time() - cpu,
                ),
            )
            return
    else:
        timing = {}
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        trial = r4._blocks(reader, trial, choice["groups"])
    stability = policy.stable(trial, control, h) if trial["state"] == "completed" else None
    _store(
        root,
        "completed",
        dict(
            decision=key,
            trial=trial,
            attempt=attempt_id,
            exact_checkpoint_reuse=prepared is not None,
            retained_trial_reuse=experiment in state["trials"],
            stability=stability,
            recorded_at=clock(),
            cpu_seconds=time.process_time() - cpu,
            timings=timing,
        ),
    )


def _usage(state: dict[str, Any]) -> tuple[float, int]:
    attempts: dict[str, float] = {}
    for key, record in state["audited"].items():
        window = state["attempts"][key]["window"]
        attempts[window] = attempts.get(window, 0.0) + record["wall_seconds"]
    seconds = sum(
        max(attempts.get(key, 0.0), state["closures"].get(key, {}).get("elapsed_seconds", 0.0))
        for key in state["windows"]
    )
    failures = sum(
        a["returncode"] != 0 or a["reason"] is not None for a in state["audited"].values()
    )
    failures += sum(v["trial"]["state"] == "failed" for v in state["completed"].values())
    return seconds, failures


def _diagnostic_stop(plan: dict[str, Any], state: dict[str, Any], index: int) -> str | None:
    if not plan.get("diagnostics"):
        return None
    if index >= len(plan["diagnostics"]):
        return "diagnostic_approval_exhausted"
    if any(item["trial"]["state"] == "failed" for item in state["completed"].values()):
        return "diagnostic_slot_failed"
    prior = [
        (key, attempt) for key, attempt in state["attempts"].items() if attempt["index"] == index
    ]
    if not prior:
        return None
    # Only a signed checkpoint-only attempt proves the prior worker did not evaluate.
    if len(prior) == 1 and prior[0][1]["checkpoint_only"]:
        key = prior[0][0]
        audit = state["audited"].get(key)
        decision = next((k for k, d in state["decisions"] if d["index"] == index), None)
        if (
            audit is not None
            and audit["returncode"] == 0
            and audit["reason"] is None
            and decision in state["checkpoints"]
        ):
            return None
    return "diagnostic_attempt_requires_review"


def run(root: Path, pin: str, steps: int, *, checkpoint_only: bool = False) -> dict[str, Any]:
    from donghak_stock_vision.storage.autonomous_runner import launch

    plan = _plan(root, pin)
    spec = plan["request"]
    require(
        1 <= steps <= len(plan.get("diagnostics", spec["schedule"]))
        and (not checkpoint_only or steps == 1),
        "supervised_step_bound",
    )
    require(plan["code"] == provenance(), "supervised_code_changed")
    with lock(root, ".runner.lock"), lock(root.parent, ".autonomous-heavy-work.lock") as fd:
        with lock(root, ".worker.lock"):
            pass
        state = _ledger(root)
        for key, attempt in state["attempts"].items():
            if key not in state["audited"]:
                _store(
                    root,
                    "audit",
                    dict(
                        attempt=key,
                        returncode=-1,
                        reason="supervisor_interrupted",
                        wall_seconds=attempt["allocated_seconds"],
                        maximum_rss_kb=None,
                    ),
                )
        for old in state["windows"]:
            if old not in state["closures"]:
                _store(
                    root,
                    "close",
                    dict(window=old, reason="supervisor_interrupted", ended_at=clock()),
                )
        began = time.monotonic()
        initial, _ = _usage(_ledger(root))
        window = _store(
            root, "window", dict(plan_id=pin, nonce=uuid.uuid4().hex, started_at=clock())
        )
        reason: str | None = "requested_steps_complete"
        for _ in range(steps):
            state = _ledger(root)
            charged, failures = _usage(state)
            remaining = spec["max_seconds"] - max(charged, initial + time.monotonic() - began)
            index = len(state["completed"])
            diagnostic_stop = _diagnostic_stop(plan, state, index)
            if window in state["stops"]:
                reason = "operator_stop"
            elif remaining <= 0:
                reason = "time_budget_exhausted"
            elif diagnostic_stop is not None:
                reason = diagnostic_stop
            elif index >= len(spec["schedule"]):
                reason = "candidate_budget_exhausted"
            elif failures >= spec["max_failures"]:
                reason = "failure_budget_exhausted"
            else:
                reason = resource_reason(root, probe())
                if reason is None:
                    allocation = min(remaining, plan["max_worker_seconds"])
                    attempt = _store(
                        root,
                        "attempt",
                        dict(
                            plan_id=pin,
                            window=window,
                            index=index,
                            nonce=uuid.uuid4().hex,
                            allocated_seconds=allocation,
                            checkpoint_only=checkpoint_only,
                            started_at=clock(),
                        ),
                    )
                    audit = launch(
                        root,
                        pin,
                        attempt,
                        supervised_research=True,
                        reservation_fd=fd,
                        timeout_seconds=allocation,
                    )
                    _store(root, "audit", audit | dict(attempt=attempt))
                    if audit["reason"] is not None or audit["returncode"]:
                        reason = audit["reason"] or "worker_failed"
                    elif window in _ledger(root)["stops"]:
                        reason = "operator_stop"
                    elif plan.get("diagnostics") and any(
                        item["trial"]["state"] == "failed"
                        for item in _ledger(root)["completed"].values()
                    ):
                        reason = "diagnostic_slot_failed"
                    elif checkpoint_only and len(_ledger(root)["completed"]) == index:
                        reason = "checkpoint_pause"
                    else:
                        reason = "requested_steps_complete"
                        continue
            break
        _store(
            root,
            "close",
            dict(
                window=window,
                reason=reason,
                ended_at=clock(),
                elapsed_seconds=time.monotonic() - began,
            ),
        )
    return dict(window=window, reason=reason)


def stop(root: Path, pin: str, window: str) -> str:
    _plan(root, pin)
    state = _ledger(root)
    require(
        window in state["windows"] and window not in state["closures"],
        "supervised_active_window_required",
    )
    if window not in state["stops"]:
        _store(root, "stop", dict(window=window))
    return window


def status(root: Path, pin: str, *, forecasts: bool = True) -> dict[str, Any]:
    plan = _plan(root, pin)
    state = _ledger(root)
    seconds, failures = _usage(state)
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        rows = {
            t["experiment_id"]: reader.get("predictive_research_rows", t["rows_id"])
            for t in state["trials"].values()
            if "rows_id" in t
        }
        yields = _rows(reader, "supervised_yield")
    active = [w for w in state["windows"] if w not in state["closures"]]
    spec = plan["request"]
    upcoming = len(state["completed"])
    pending = {key: value for key, value in state["decisions"] if key not in state["completed"]}
    prospective: dict[str, Any] = dict(status="NOT_CHECKED")
    if forecasts:
        from donghak_stock_vision.storage.direction_shadow import preflight
        from donghak_stock_vision.storage.operational_market import OperationalMarketStore

        try:
            request = document(spec["r5_request"])
            require(
                digest(request) == read_pin(Path(spec["r5_request_pin_file"])), "supervised_r5_pin"
            )
            prospective = preflight(request)
            with closing(OperationalMarketStore(Path(request["operational"]["root"]))) as store:
                latest = store.status().to_dict()["latest_trading_date"]
            prospective["latest_operational_trading_date"] = latest
            if latest != request["input_trading_date"]:
                prospective.update(status="BLOCKED", reason="supervised_r5_new_request_required")
        except (ValueError, OSError, KeyError) as error:
            prospective = dict(status="BLOCKED", reason=str(error))
    return dict(
        plan_id=pin,
        plan=plan,
        historical=dict(
            active_window=active[0] if active else None,
            completed=len(state["completed"]),
            fits=sum(c["cache_source"] is None for c in state["checkpoints"].values()),
            evaluated=sum(r["trial"]["state"] == "completed" for r in state["completed"].values()),
            **(
                dict(
                    approved_slots=len(plan["diagnostics"]),
                    exact_source_reuse=sum(
                        c["cache_source"] is not None for c in state["checkpoints"].values()
                    ),
                )
                if plan.get("diagnostics")
                else {}
            ),
            exact_checkpoint_reuse=sum(
                r["exact_checkpoint_reuse"] for r in state["completed"].values()
            ),
            failures=failures,
            remaining_seconds=max(0, spec["max_seconds"] - seconds),
            next_horizon=spec["schedule"][upcoming] if upcoming < len(spec["schedule"]) else None,
            next_committed_candidate=pending,
            decisions=dict(state["decisions"]),
            results=state["completed"],
            checkpoints=state["checkpoints"],
            attempts=state["attempts"],
            audits=state["audited"],
            windows=state["windows"],
            closures=state["closures"],
            checkpoint_pauses=yields,
            predictions=rows,
            retained_legacy=sum(
                not (r["stability"] or {}).get("passed", False) for r in state["completed"].values()
            ),
            size_bytes=sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        ),
        prospective=prospective,
        **r3.FLAGS,
    )
