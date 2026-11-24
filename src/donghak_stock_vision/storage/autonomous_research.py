"""Immutable experiment memory over AnalysisStore; no market DB or registry writes."""

import hashlib
import os
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import Sample, digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.models.autonomous import champion_check, execute
from donghak_stock_vision.models.directional import (
    build,
    catalog,
    contract,
    folds,
    readiness,
)
from donghak_stock_vision.models.experiment import select_candidate
from donghak_stock_vision.models.service import provenance
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.krx_backfill import write_new
from donghak_stock_vision.storage.real_market_dataset import _protocol
from donghak_stock_vision.storage.real_market_training import (
    RealMarketTrainingRequest,
    prepare_real_market_training,
)

DB = "experiments.sqlite3"
REQUEST = "autonomous_direction_source_v1"
LIMITS = dict(
    cpu_threads=1,
    memory_mb=1024,
    timeout_seconds=300,
    minimum_free_mb=1024,
    maximum_study_mb=512,
    minimum_memory_free_percent=20,
    backoff_seconds=60,
    maximum_attempts=2,
    interval_seconds=300,
    model_maximum_mb=16,
)


def clock() -> str:
    return datetime.now(UTC).isoformat()


def prepare(d: dict[str, Any], root: Path, pins: Path) -> FrozenJSON:
    historical = d.get("version") == "autonomous_historical_source_v1"
    multi = historical or d.get("version") == "autonomous_multi_stock_source_v1"
    fields(
        d,
        "version dataset_version research protocol tickers cutoff horizons"
        + (" multi_stock" if multi else "")
        + (" historical" if historical else ""),
    )
    require(
        d["version"]
        in (
            REQUEST,
            "autonomous_direction_source_v2",
            "autonomous_multi_stock_source_v1",
            "autonomous_historical_source_v1",
        )
        and type(d["dataset_version"]) is str
        and 0 < len(d["dataset_version"]) <= 100,
        "autonomous_request_version",
    )
    require(
        d["horizons"] in ([1], [5], [20], [1, 5], [1, 20], [5, 20], [1, 5, 20]),
        "autonomous_horizons",
    )
    protocol, access = _protocol(d)
    research = fields(d["research"], "database pin source_contract")
    source = RealMarketTrainingRequest(
        Path(research["database"]),
        research["pin"],
        access,
        access.identifier,
        None,
        None,
        tuple(d["tickers"]),
        timestamp(d["cutoff"]),
        ResearchCalendarPolicy.from_dict(protocol["research_calendar"]),
        research["source_contract"],
    )
    source.validate()
    if multi:
        from donghak_stock_vision.models import stock_generalization
        from donghak_stock_vision.storage.real_market_dataset import document

        spec = fields(d["multi_stock"], "heldout_tickers budget policy inventory cohort")
        stock_generalization.validate_scope(
            spec | dict(policy="adaptive" if historical else spec["policy"]), d["tickers"]
        )
        if historical:
            require(
                spec["policy"] in ("adaptive", "predeclared", "robust"),
                "historical_selection_policy",
            )
        inv = fields(spec["inventory"], "path pin")
        inventory = document(inv["path"], maximum=4 * 1024**2)
        require(
            digest(inventory) == sha(inv["pin"])
            and inventory["database_pin"] == source.database_pin
            and inventory["split_pin"] == source.access_split_pin
            and all(inventory["inventory"][t]["metadata_eligible"] for t in source.tickers),
            "multi_stock_inventory_binding",
        )
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (root, pins))
        and not root.is_relative_to(pins)
        and not pins.is_relative_to(root)
        and not root.is_relative_to(source.database.parent),
        "autonomous_new_separate_output",
    )
    cached_goals: dict[str, Any] | None = None
    if historical:
        from donghak_stock_vision.storage import historical_research_source as historical_source

        if "source_parent" in d["historical"]:
            snapshot, original, cached_goals = _historical_reused_source(source, d["historical"])
            require(
                set(cached_goals) == {str(h) for h in d["horizons"]}, "historical_cached_horizons"
            )
        else:
            snapshot, original = historical_source.snapshot(source, d["historical"])
    else:
        snapshot, original = prepare_real_market_training(source)
    value = snapshot.to_dict()
    expanded = d["version"] == "autonomous_direction_source_v2" or multi
    if expanded:
        from donghak_stock_vision.models import research_v2
    windows = folds(value, access)
    feature_cache = (
        None
        if cached_goals is not None
        else historical_source.features(value)
        if historical
        else research_v2.prepare_features(value)
        if multi
        else None
    )
    cached_availability = (
        {(t, e["date"]): e["available_at"] for t, entries in feature_cache.items() for e in entries}
        if feature_cache is not None
        else {}
    )
    goals = {}
    for horizon in d["horizons"]:
        by_ticker: dict[str, dict[str, int]] = {}
        if cached_goals is not None:
            cached = cached_goals[str(horizon)]
            require(
                cached["contract"] == research_v2.contract(horizon),
                "historical_cached_feature_contract",
            )
            samples = [Sample.from_dict(s) for s in cached["samples"]]
            changes, excluded = cached["returns"], cached["excluded"]
            by_ticker = cached["exclusions_by_ticker"]
        else:
            samples, changes, excluded = (
                research_v2.build(
                    value, horizon, feature_cache=feature_cache, exclusions_by_ticker=by_ticker
                )
                if expanded
                else build(value, horizon)
            )
        if historical:
            from donghak_stock_vision.models import historical_research as historical_model

            goal_windows = historical_model.folds(value, d["historical"], horizon)
        else:
            goal_windows = research_v2.folds(value, access, horizon) if expanded else windows
        # Label generation is retrospective; neither the generator nor search workers
        # may use the separate confirmation population to select another candidate.
        train = [
            s
            for s in samples
            if s.label_end is not None
            and s.label_end
            <= (
                date.fromisoformat(d["historical"]["search_end"])
                if historical
                else access.training.end
            )
        ]
        held = [
            s
            for s in samples
            if s.trading_date
            >= (
                date.fromisoformat(d["historical"]["diagnostic"]["start"])
                if historical
                else access.validation.start
            )
            and s.label_end is not None
            and s.label_end
            <= (
                date.fromisoformat(d["historical"]["diagnostic"]["end"])
                if historical
                else access.validation.end
            )
        ]
        goals[str(horizon)] = dict(
            contract=research_v2.contract(horizon) if expanded else contract(horizon),
            samples=[s.to_dict() for s in train + held],
            returns=changes,
            sample_pin=digest([s.to_dict() for s in train]),
            confirmation_pin=digest([s.to_dict() for s in held]),
            readiness=readiness(samples, goal_windows),
            excluded=excluded,
            training_rows=len(train),
            confirmation_rows=len(held),
        )
        if multi:
            goals[str(horizon)]["exclusions_by_ticker"] = by_ticker
            goals[str(horizon)]["samples_by_ticker"] = {
                t: sum(s.ticker == t for s in train + held) for t in source.tickers
            }
        if expanded:
            goals[str(horizon)]["folds"] = [f.to_dict() for f in goal_windows]
            goals[str(horizon)]["input_availability"] = {
                digest(s.to_dict()): cached_goals[str(horizon)]["input_availability"][
                    digest(s.to_dict())
                ]
                if cached_goals is not None
                else cached_availability[(s.ticker, s.trading_date.isoformat())]
                if multi
                else max(
                    timestamp(r["collected_at"])
                    for r in value["rows"][s.ticker]
                    if r["trading_date"] <= s.trading_date.isoformat()
                ).isoformat()
                for s in train + held
            }
    data = dict(
        version="direction_dataset_v4"
        if historical
        else "direction_dataset_v3"
        if multi
        else "direction_dataset_v2"
        if expanded
        else "direction_dataset_v1",
        dataset_version=d["dataset_version"],
        source_pin=digest(value["real_market_source"]),
        snapshot_pin=snapshot.identifier,
        split=access.to_dict(),
        folds=[f.to_dict() for f in windows],
        goals=goals,
        observation_count=sum(len(r) for r in value["rows"].values()),
        tickers=sorted(value["rows"]),
        trading_dates=len({r["trading_date"] for rows in value["rows"].values() for r in rows}),
        first_date=value["start"],
        last_date=value["end"],
        protected_final_test_read=False,
        historical_publication_available_at_anchor=False,
        prior_cross_study_exposure=True,
        timeframes=dict(
            daily="originals_verified",
            weekly="incomplete_session_coverage_not_qualified",
            monthly="incomplete_session_coverage_not_qualified",
            intraday="no_retained_intraday_originals",
            index_and_sector="not_present_in_selected_source",
        ),
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    if expanded:
        data["timeframes"] = dict(
            policy="closed_observed_periods_no_partial_first_period",
            session_authentication=False,
            intraday="unavailable",
            availability="actual_receipt_time_not_nominal_historical_anchor",
        )
    if multi:
        data["generalization"] = {k: v for k, v in spec.items() if k != "policy"}
        data["generalization"].update(
            heldout_metrics_used_for_selection=False,
            liblinear_quarantined=True,
            original_source_universe_cap=25,
        )
    if historical:
        data["historical"] = d["historical"]
    code = provenance()
    configurations = []
    for config in (
        stock_generalization.catalog(d["horizons"])
        if multi
        else research_v2.catalog(d["horizons"])
        if expanded
        else catalog(d["horizons"])
    ):
        identity = config | dict(
            training_sample_pin=goals[str(config["goal"]["horizon"])]["sample_pin"],
            fold_pin=digest(
                goals[str(config["goal"]["horizon"])]["folds"] if expanded else data["folds"]
            ),
            implementation_hash=code["implementation_hash"],
            versions=code["versions"],
        )
        configurations.append(dict(experiment_id=digest(identity), config=config))
    plan = dict(
        version="autonomous_plan_v6"
        if historical
        else "autonomous_plan_v5"
        if multi
        else "autonomous_plan_v4"
        if expanded
        else "autonomous_plan_v1",
        dataset_id=digest(data),
        snapshot_id=snapshot.identifier,
        request_pin=digest(d),
        source_pin=data["source_pin"],
        code=code,
        limits=LIMITS,
        configurations=configurations,
        source_classification="local_archived_provider_claim_not_independent_authentication",
        promotion_policy="locked_once_confirmation_after_finite_nested_search_v1",
        new_market_data_required=False,
        operational_registration_available=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
    if expanded:
        plan.update(
            search_space="adaptive_direction_v2",
            agent_policy="server_validated_managed_v1",
            experiment_budget=24,
        )
    if multi:
        plan.update(
            search_space=stock_generalization.SPACE,
            experiment_budget=spec["budget"],
            selection_policy=spec["policy"],
            generalization_pin=digest(data["generalization"]),
        )
    if historical:
        plan.update(
            search_space=historical_model.SPACE,
            historical_pin=digest(data["historical"]),
            robust_priorities=historical_model.PRIORITIES,
            transfer_hypotheses=_historical_hypotheses(d["historical"]),
        )
    root.mkdir(mode=0o700, parents=True)
    pins.mkdir(mode=0o700, parents=True)
    fd = os.open(root / DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(root / DB)) as store:
        store.put_many(
            [
                ("autonomous_snapshot", value),
                ("autonomous_source_verification", original.to_dict()),
                ("autonomous_dataset", data),
                ("autonomous_plan", plan),
            ]
        )
    for name, pin in (
        ("plan", digest(plan)),
        ("dataset", digest(data)),
        ("source", data["source_pin"]),
        ("snapshot", snapshot.identifier),
    ):
        write_new(pins / (name + ".pin"), pin + "\n")
    write_new(root / "request.json", FrozenJSON.freeze(d).payload_json)
    return FrozenJSON.freeze(
        dict(
            dataset=data, plan_id=digest(plan), experiments=len(configurations), network_requests=0
        )
    )


def private_root(root: Path) -> None:
    """Shared filesystem boundary; callers must separately verify content owners."""
    require(
        root.is_absolute()
        and root.resolve() == root
        and not root.is_symlink()
        and (root / DB).is_file()
        and (root / DB).resolve() == root / DB
        and (root / DB).stat().st_nlink == 1
        and (root / DB).stat().st_mode & 0o077 == 0
        and root.stat().st_mode & 0o077 == 0,
        "autonomous_private_root",
    )


def context(
    root: Path, pin: str, *, code_check: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    private_root(root)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("autonomous_plan", pin)
        data = store.get("autonomous_dataset", plan["dataset_id"])
    require(
        plan["version"]
        in (
            "autonomous_plan_v1",
            "autonomous_plan_v2",
            "autonomous_plan_v3",
            "autonomous_plan_v4",
            "autonomous_plan_v5",
            "autonomous_plan_v6",
        )
        and plan["limits"] == LIMITS
        and not data["protected_final_test_read"]
        and data["source_pin"] == plan["source_pin"]
        and all(
            plan[k] is False
            for k in ("pit_qualified", "performance_approved", "execution_authorized")
        ),
        "autonomous_plan_binding",
    )
    protected = ExplicitResearchSplit.from_dict(data["split"]).final_test
    historical = plan["version"] == "autonomous_plan_v6"
    if historical:
        from donghak_stock_vision.storage.historical_research_source import validate

        require(digest(data["historical"]) == plan["historical_pin"], "historical_scope_pin")
        allowed = validate(data["historical"], ExplicitResearchSplit.from_dict(data["split"]))
        from donghak_stock_vision.models import historical_research as historical_model

        require(
            plan["selection_policy"] in historical_model.POLICIES
            and plan["robust_priorities"] == historical_model.PRIORITIES,
            "historical_policy_binding",
        )
    else:
        require(date.fromisoformat(data["last_date"]) < protected.start, "autonomous_sealed_window")
    require(
        data["version"]
        == (
            "direction_dataset_v4"
            if historical
            else "direction_dataset_v3"
            if plan["version"] == "autonomous_plan_v5"
            else "direction_dataset_v2"
            if plan["version"] == "autonomous_plan_v4"
            else "direction_dataset_v1"
        ),
        "autonomous_dataset_version",
    )
    split = ExplicitResearchSplit.from_dict(data["split"])
    for goal in data["goals"].values():
        training, held = [], []
        for sample in goal["samples"]:
            start, end = (
                date.fromisoformat(sample["trading_date"]),
                date.fromisoformat(sample["label_end"]),
            )
            if historical:
                require(
                    any(w.start <= start <= end <= w.end for w in allowed),
                    "historical_sample_source_window",
                )
            else:
                require(start <= end < protected.start, "autonomous_sample_sealed_window")
            if (
                start <= end <= date.fromisoformat(data["historical"]["search_end"])
                if historical
                else split.training.start <= start <= end <= split.training.end
            ):
                training.append(sample)
            else:
                require(
                    (
                        data["historical"]["diagnostic"]["start"]
                        <= start.isoformat()
                        <= end.isoformat()
                        <= data["historical"]["diagnostic"]["end"]
                    )
                    if historical
                    else split.validation.start <= start <= end <= split.validation.end,
                    "autonomous_sample_split",
                )
                held.append(sample)
            require(digest(sample) in goal["returns"], "autonomous_sample_return")
        require(
            digest(training) == goal["sample_pin"] and digest(held) == goal["confirmation_pin"],
            "autonomous_population_pin",
        )
    space = "initial_v1" if plan["version"] == "autonomous_plan_v1" else plan["search_space"]
    if plan["version"] == "autonomous_plan_v2":
        from donghak_stock_vision.backtest.validation import sha

        sha(plan["agent_model_digest"])
        require(space == "agent_masks_v1", "gemma_search_version")
    if plan["version"] == "autonomous_plan_v3":
        require(
            space == "management_masks_v1"
            and plan["agent_policy"] == "server_validated_managed_v1"
            and "agent_model_digest" not in plan,
            "llm_managed_plan_policy",
        )
    if plan["version"] in ("autonomous_plan_v5", "autonomous_plan_v6"):
        from donghak_stock_vision.models import stock_generalization

        stock_generalization.validate_scope(
            data["generalization"]
            | dict(policy="adaptive" if historical else plan["selection_policy"]),
            data["tickers"],
        )
        require(
            space == ("historical_cross_period_v1" if historical else stock_generalization.SPACE)
            and plan["experiment_budget"] == 12
            and digest(data["generalization"]) == plan["generalization_pin"]
            and data["generalization"]["liblinear_quarantined"]
            and not data["generalization"]["heldout_metrics_used_for_selection"],
            "multi_stock_plan_binding",
        )
        if "policy_parent" in plan:
            owner = plan["policy_parent"]
            with closing(ReadOnlyAnalysisStore(Path(owner["root"]) / DB)) as parent_store:
                parent = parent_store.get("autonomous_plan", owner["pin"])
            require(
                "policy_parent" not in parent
                and parent["version"] == plan["version"]
                and parent["selection_policy"] == "adaptive"
                and plan["selection_policy"]
                in (("predeclared", "robust") if historical else ("predeclared",))
                and {k: v for k, v in parent.items() if k != "selection_policy"}
                == {
                    k: v for k, v in plan.items() if k not in ("selection_policy", "policy_parent")
                },
                "multi_stock_policy_fork_binding",
            )
        expected = stock_generalization.catalog(sorted(int(h) for h in data["goals"]))
    elif plan["version"] == "autonomous_plan_v4":
        from donghak_stock_vision.models import research_v2

        require(
            space == research_v2.SPACE
            and data["version"] == "direction_dataset_v2"
            and plan["agent_policy"] == "server_validated_managed_v1"
            and plan["experiment_budget"] == 24,
            "research_v2_plan_contract",
        )
        expected = research_v2.catalog(sorted(int(h) for h in data["goals"]))
    else:
        expected = catalog(sorted(int(h) for h in data["goals"]), space)
    require([c["config"] for c in plan["configurations"]] == expected, "autonomous_search_space")
    for item in plan["configurations"]:
        identity = item["config"] | dict(
            training_sample_pin=data["goals"][str(item["config"]["goal"]["horizon"])]["sample_pin"],
            fold_pin=digest(
                data["goals"][str(item["config"]["goal"]["horizon"])]["folds"]
                if plan["version"]
                in ("autonomous_plan_v4", "autonomous_plan_v5", "autonomous_plan_v6")
                else data["folds"]
            ),
            implementation_hash=plan["code"]["implementation_hash"],
            versions=plan["code"]["versions"],
        )
        require(digest(identity) == item["experiment_id"], "autonomous_experiment_identity")
    if code_check:
        current = provenance()
        require(
            current["implementation_hash"] == plan["code"]["implementation_hash"]
            and current["versions"] == plan["code"]["versions"],
            "autonomous_code_or_runtime_changed",
        )
    return plan, data


def terminals(store: AnalysisStore) -> dict[str, dict[str, Any]]:
    require(
        store.connection.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0] <= 5000,
        "autonomous_memory_bound",
    )
    result = {}
    for _, value in store.all("autonomous_terminal"):
        key = value["experiment_id"]
        require(key not in result, "autonomous_conflicting_terminal")
        require(value["state"] in ("completed", "failed"), "autonomous_terminal_state")
        if value["state"] == "completed":
            original = store.get("autonomous_result", value["result_id"])
            base = Path(store.connection.execute("PRAGMA database_list").fetchone()[2]).parent
            path = Path(value["checkpoint"])
            require(
                path == base / "models" / (key + ".checkpoint")
                and path.resolve() == path
                and path.is_file()
                and path.stat().st_size <= LIMITS["model_maximum_mb"] * 1024**2,
                "autonomous_checkpoint_reference",
            )
            require(
                hashlib.sha256(path.read_bytes()).hexdigest()
                == value["model_sha256"]
                == original["model_sha256"]
                and original["experiment_id"] == key
                and original["config"] == value["config"],
                "autonomous_checkpoint_integrity",
            )
            require(
                all(
                    original[k] == value[k]
                    for k in ("stability_ap", "mean_ap", "largest_train_gap")
                ),
                "autonomous_result_summary",
            )
        if value["state"] == "completed" and "robust_summary" in original:
            value = value | dict(robust_summary=original["robust_summary"])
        result[key] = value
    return result


def next_experiment(
    plan: dict[str, Any], data: dict[str, Any], done: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    if plan["version"] == "autonomous_plan_v6":
        from donghak_stock_vision.models.historical_research import selection

        return selection(plan, done)
    if plan["version"] == "autonomous_plan_v5":
        from donghak_stock_vision.models.stock_generalization import selection

        return selection(plan, done)
    if plan["version"] == "autonomous_plan_v4":
        from donghak_stock_vision.models.research_v2 import selection

        return selection(plan, done)
    remaining: list[dict[str, Any]] = [
        c for c in plan["configurations"] if c["experiment_id"] not in done
    ]
    if not remaining:
        return None
    target = min(
        sorted({c["config"]["goal"]["horizon"] for c in remaining}),
        key=lambda h: sum(r["config"]["goal"]["horizon"] == h for r in done.values()),
    )
    remaining = [c for c in remaining if c["config"]["goal"]["horizon"] == target]
    completed = [
        r
        for r in done.values()
        if r["state"] == "completed"
        and r["config"]["goal"]["horizon"] == target
        and r["experiment_id"] in {c["experiment_id"] for c in plan["configurations"]}
    ]
    seen = {r["config"]["candidate"]["family"] for r in completed}
    unseen = [c for c in remaining if c["config"]["candidate"]["family"] not in seen]
    if unseen:
        return unseen[0] | dict(
            selection_reason="predeclared_family_diversity", parent_experiment=None
        )
    eligible = [
        r
        for r in completed
        if not data["goals"][str(r["config"]["goal"]["horizon"])]["readiness"]["failures"]
    ]
    if not eligible:
        return remaining[0] | dict(
            selection_reason="next_predeclared_configuration", parent_experiment=None
        )
    # Only training-internal fold metrics enter selection. Confirmation is never read here.
    lead = select_candidate(
        [
            dict(candidate=r["experiment_id"], stability_ap=r["stability_ap"], mean_ap=r["mean_ap"])
            for r in eligible
        ]
    )
    leader = done[lead]
    family = leader["config"]["candidate"]["family"]
    ranked = sorted(
        enumerate(remaining),
        key=lambda pair: (
            pair[1]["config"]["goal"]["horizon"] != leader["config"]["goal"]["horizon"],
            pair[1]["config"]["candidate"]["family"] != family,
            pair[1]["config"]["mask"] == leader["config"]["mask"],
            pair[1]["config"]["candidate"]["value"]
            if leader["largest_train_gap"] > 0.15
            else pair[0],
        ),
    )
    return ranked[0][1] | dict(
        selection_reason="lower_complexity_after_overfit"
        if leader["largest_train_gap"] > 0.15
        else "training_internal_leader_neighbour",
        parent_experiment=lead,
    )


def status(root: Path, pin: str) -> FrozenJSON:
    plan, data = context(root, pin, code_check=False)
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        done = terminals(store)
        attempts = [v for _, v in store.all("autonomous_attempt")]
        confirmations = [v for _, v in store.all("autonomous_confirmation")]
        leaders = [v for _, v in store.all("autonomous_champion")]
        interruptions = store.all("autonomous_interruption")
        consumed = store.all("autonomous_gate_consumed")
        proposals = [v for _, v in store.all("gemma_proposal")]
        llm_proposals = [v for _, v in store.all("llm_proposal")]
        selections = [v for _, v in store.all("autonomous_selection")]
        confirmation_failures = store.all("autonomous_confirmation_failure")
    selected = {c["experiment_id"] for c in plan["configurations"]}
    finished = [v for k, v in done.items() if k in selected]
    return FrozenJSON.freeze(
        dict(
            version="autonomous_status_v1",
            dataset_version=data["dataset_version"],
            experiment_count=len(selected),
            completed=sum(v["state"] == "completed" for v in finished),
            failed=sum(v["state"] == "failed" for v in finished),
            remaining=len(selected) - len(finished),
            attempts=len(attempts),
            interrupted_attempts=len(interruptions),
            unresolved_attempts=sum(a["experiment_id"] not in done for a in attempts),
            gates_consumed=len(consumed),
            confirmation_failures=len(confirmation_failures),
            state="BUDGET_EXHAUSTED"
            if plan["version"] in ("autonomous_plan_v4", "autonomous_plan_v5", "autonomous_plan_v6")
            and len(finished) >= plan["experiment_budget"]
            else "SEARCH_EXHAUSTED"
            if selected <= done.keys()
            else "QUEUED",
            confirmations=len(confirmations),
            research_champions=leaders,
            source_pin_verified=True,
            validation_search_reuses=sum(v["state"] == "completed" for v in finished),
            prior_cross_study_exposure=True,
            independent_future_performance_established=False,
            new_market_data_required=False,
            network_requests=sum(v.get("local_http_requests", 0) for v in proposals)
            + sum(
                v.get("metadata", {}).get("local_http_requests", 0)
                + v.get("metadata", {}).get("remote_http_requests", 0)
                for v in llm_proposals
            ),
            local_ollama_inference_requests=sum(v.get("inference_calls", 0) for v in proposals),
            gemma_accepted=sum(v["state"] == "accepted" for v in proposals),
            gemma_rejected=sum(v["state"] != "accepted" for v in proposals),
            rule_fallbacks=sum(v["state"] == "fallback" for v in selections),
            krx_requests=0,
            external_network_requests=sum(
                v.get("metadata", {}).get("remote_http_requests", 0) for v in llm_proposals
            ),
            llm_accepted=sum(v["state"] == "accepted" for v in llm_proposals),
            llm_rejected=sum(v["state"] != "accepted" for v in llm_proposals),
            operating_model_registered=False,
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
        )
    )


def worker(root: Path, pin: str, attempt_id: str) -> None:
    plan, data = context(root, pin)
    with closing(AnalysisStore(root / DB)) as store:
        attempt = store.get("autonomous_attempt", attempt_id)
        key = attempt["experiment_id"]
        require(attempt["plan_id"] == pin, "autonomous_attempt_owner")
        if key in terminals(store):
            return
        choice = next(c for c in plan["configurations"] if c["experiment_id"] == key)
        phase = dict(attempt_id=attempt_id, experiment_id=key, at=clock())
        store.put("autonomous_phase", phase | dict(state="Training"))
        try:
            require(
                not data["goals"][str(choice["config"]["goal"]["horizon"])]["readiness"][
                    "failures"
                ],
                "autonomous_insufficient_population",
            )
            result, body = execute(data, choice["config"])
            require(len(body) <= LIMITS["model_maximum_mb"] * 1024**2, "autonomous_model_size")
            directory = root / "models"
            directory.mkdir(mode=0o700, exist_ok=True)
            require(
                directory.resolve() == directory and directory.stat().st_mode & 0o077 == 0,
                "autonomous_model_directory",
            )
            path = directory / (key + ".checkpoint")
            if path.exists():
                require(
                    hashlib.sha256(path.read_bytes()).hexdigest() == result["model_sha256"],
                    "autonomous_checkpoint_conflict",
                )
            else:
                temp = directory / (attempt_id + ".tmp")
                fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temp, path)
                temp.unlink()
            store.put("autonomous_phase", phase | dict(state="Evaluating", at=clock()))
            result.update(experiment_id=key, dataset_id=plan["dataset_id"], config=choice["config"])
            result_id = digest(result)
            terminal = dict(
                experiment_id=key,
                config=choice["config"],
                state="completed",
                result_id=result_id,
                model_sha256=result["model_sha256"],
                checkpoint=str(path),
                started_at=attempt["started_at"],
                finished_at=clock(),
                stability_ap=result["stability_ap"],
                mean_ap=result["mean_ap"],
                largest_train_gap=result["largest_train_gap"],
                selection_reason=attempt["selection_reason"],
                parent_experiment=attempt["parent_experiment"],
                attempt_id=attempt_id,
            )
            store.put_many([("autonomous_result", result), ("autonomous_terminal", terminal)])
        except Exception as error:
            reason = str(error)
            import re

            reason = (
                reason
                if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", reason)
                else "autonomous_training_failed"
            )
            store.put(
                "autonomous_terminal",
                dict(
                    experiment_id=key,
                    config=choice["config"],
                    state="failed",
                    reason=reason,
                    started_at=attempt["started_at"],
                    finished_at=clock(),
                    attempt_id=attempt_id,
                ),
            )


def confirm(root: Path, pin: str) -> None:
    plan, data = context(root, pin)
    require(
        plan["version"] not in ("autonomous_plan_v4", "autonomous_plan_v5", "autonomous_plan_v6"),
        "research_v2_confirmation_sealed",
    )
    with closing(AnalysisStore(root / DB)) as store:
        done = terminals(store)
        require(
            all(c["experiment_id"] in done for c in plan["configurations"]),
            "autonomous_search_not_finished",
        )
        for horizon in sorted(data["goals"]):
            group = [
                r
                for r in done.values()
                if r["state"] == "completed" and str(r["config"]["goal"]["horizon"]) == horizon
            ]
            gate_id = digest(
                dict(
                    dataset=plan["dataset_id"],
                    horizon=horizon,
                    gate=data["goals"][horizon]["confirmation_pin"],
                )
            )
            if any(v["gate_id"] == gate_id for _, v in store.all("autonomous_gate_consumed")):
                continue
            if not group:
                store.put(
                    "autonomous_gate_consumed",
                    dict(
                        gate_id=gate_id,
                        winner=None,
                        at=clock(),
                        selection_locked=True,
                        reason="no_successful_candidate",
                    ),
                )
                continue
            best = select_candidate(
                [
                    dict(
                        candidate=r["experiment_id"],
                        stability_ap=r["stability_ap"],
                        mean_ap=r["mean_ap"],
                    )
                    for r in group
                ]
            )
            chosen = done[best]
            # Consume before loading/evaluating confirmation; a crash cannot reopen it.
            store.put(
                "autonomous_gate_consumed",
                dict(gate_id=gate_id, winner=best, at=clock(), selection_locked=True),
            )
            try:
                search = store.get("autonomous_result", chosen["result_id"])
                gate, _ = execute(data, chosen["config"], gate=True)
                require(
                    gate["model_sha256"] == chosen["model_sha256"],
                    "autonomous_frozen_model_reproduction",
                )
                decision = champion_check(search, gate)
            except Exception:
                store.put(
                    "autonomous_confirmation_failure",
                    dict(
                        gate_id=gate_id,
                        experiment_id=best,
                        at=clock(),
                        reason="confirmation_failed_consumed_not_reopened",
                    ),
                )
                continue
            value = dict(
                gate_id=gate_id,
                experiment_id=best,
                goal=chosen["config"]["goal"],
                confirmation=gate,
                decision=decision,
                prior_cross_study_exposure=True,
                validation_looks=1,
                selection_trials=len(group),
                final_test_read=False,
                pit_qualified=False,
                performance_approved=False,
                execution_authorized=False,
            )
            records = [("autonomous_confirmation", value)]
            if decision["promote"]:
                records.append(
                    (
                        "autonomous_champion",
                        dict(
                            experiment_id=best,
                            model_sha256=chosen["model_sha256"],
                            checkpoint=chosen["checkpoint"],
                            evaluation_contract=gate_id,
                            state="Research Champion",
                            confirmation_id=digest(value),
                            usage_restriction="research_only",
                            operational_status="unregistered",
                            performance_approved=False,
                            pit_qualified=False,
                            execution_authorized=False,
                        ),
                    )
                )
            store.put_many(records)


def _historical_hypotheses(spec: dict[str, Any]) -> list[dict[str, Any]]:
    from pathlib import Path

    from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore

    result = []
    for reference in spec["references"]:
        fields(reference, "root pin")
        root = Path(reference["root"])
        plan, _ = context(root, reference["pin"], code_check=False)
        require(plan["version"] == "autonomous_plan_v5", "historical_reference_owner")
        with closing(ReadOnlyAnalysisStore(root / DB)) as store:
            done = terminals(store)
        require(len(done) == plan["experiment_budget"], "historical_reference_locked")
        for h in (1, 5, 20):
            candidates = [
                v
                for v in done.values()
                if v["state"] == "completed" and v["config"]["goal"]["horizon"] == h
            ]
            if candidates:
                best = max(candidates, key=lambda v: v["stability_ap"] - v["largest_train_gap"])
                result.append(
                    dict(
                        config=best["config"],
                        reference=reference,
                        reason="revalidate_prior_stability_in_new_population_and_period",
                        source_scores_are_current_evidence=False,
                    )
                )
    return result


def _historical_reused_source(
    request: RealMarketTrainingRequest, spec: dict[str, Any]
) -> tuple[FrozenJSON, FrozenJSON, dict[str, Any]]:
    """Only a separately retained v6 original snapshot owner; no fabricated source proof."""
    from pathlib import Path

    from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
    from donghak_stock_vision.storage.real_market_training import MAX_ROWS, _quiescent

    parent = fields(spec["source_parent"], "root pin")
    root = Path(parent["root"])
    plan, data = context(root, parent["pin"], code_check=False)
    require(
        plan["version"] == "autonomous_plan_v6" and "source_parent" not in data["historical"],
        "historical_original_snapshot_parent",
    )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        value = store.get("autonomous_snapshot", plan["snapshot_id"])
        proofs = store.all("autonomous_source_verification")
        require(
            not store.all("autonomous_attempt") and not store.all("autonomous_terminal"),
            "historical_pretraining_source_revision_only",
        )
    source = value["real_market_source"]
    require(
        len(proofs) == 1
        and proofs[0][1] == source
        and source["version"] == "historical_disjoint_source_v1"
        and digest(source) == plan["source_pin"]
        and source["local_originals_verified"] is True
        and source["request"] == request.document.to_dict()
        and value["historical_windows"] == spec["windows"]
        and data["historical"]["search_end"] == spec["search_end"]
        and data["historical"]["diagnostic"] == spec["diagnostic"]
        and source["selected_rows_pin"] == digest(value["rows"])
        and sum(len(r) for r in value["rows"].values()) <= MAX_ROWS,
        "historical_original_snapshot_binding",
    )
    _quiescent(request.database, request.database_pin)
    original_pin = digest(source)
    source = source | dict(
        version="historical_disjoint_reused_source_v1",
        original_source_pin=original_pin,
        source_parent=parent,
        original_snapshot_pin=plan["snapshot_id"],
        prepared_dataset_pin=plan["dataset_id"],
    )
    value = value | dict(real_market_source=source)
    return FrozenJSON.freeze(value), FrozenJSON.freeze(source), data["goals"]
