"""Explicit bounded research commands; no collection or unattended activation."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.autonomous import execute
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, prepare, status, terminals
from donghak_stock_vision.storage.autonomous_runner import render_agent, run, worker_entry
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("autonomous-research")
    p.add_argument(
        "action",
        choices=(
            "prepare",
            "run",
            "status",
            "worker",
            "reproduce",
            "render-agent",
            "derive",
            "report",
            "fork-policy",
            "selection-register",
            "selection-assess",
            "selection-prepare",
            "selection-worker",
            "evidence-audit",
            "prediction-audit",
            "failure-feedback-register",
            "failure-feedback-run",
            "failure-feedback-report",
            "failure-feedback-worker",
            "predictive-research-register",
            "predictive-research-run",
            "predictive-research-report",
            "predictive-research-worker",
            "autonomous-strategy-register",
            "autonomous-strategy-run",
            "autonomous-strategy-report",
            "autonomous-strategy-worker",
            "supervised-register",
            "supervised-run",
            "supervised-stop",
            "supervised-status",
            "supervised-worker",
        ),
    )
    p.add_argument("--selection-policy", choices=("predeclared", "robust"), default="predeclared")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--source-root", type=Path)
    p.add_argument("--source-plan-pin-file", type=Path)
    p.add_argument("--managed-agents", action="store_true")
    p.add_argument("--management-root", type=Path)
    p.add_argument("--model-config", type=Path)
    p.add_argument("--model-config-sha256")
    p.add_argument("--model-digest-file", type=Path)
    p.add_argument("--reuse-root", type=Path)
    p.add_argument("--reuse-plan-pin-file", type=Path)
    p.add_argument("--agent", choices=("rule", "gemma4", "managed"), default="rule")
    p.add_argument("--request", type=Path)
    p.add_argument("--request-pin-file", type=Path)
    p.add_argument("--pins-root", type=Path)
    p.add_argument("--plan-pin-file", type=Path)
    p.add_argument("--plan")
    p.add_argument("--attempt")
    p.add_argument("--experiment-id")
    p.add_argument("--gate", action="store_true")
    p.add_argument("--steps", type=int, default=1)
    p.add_argument("--report", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--checkpoint-only", action="store_true")
    p.add_argument("--window")


def run_command(args: argparse.Namespace) -> int:
    if args.action.startswith("supervised-"):
        from donghak_stock_vision.storage import supervised_research

        if args.action == "supervised-worker":
            require(
                args.plan is not None and args.attempt is not None and not args.gate,
                "supervised_worker_reference",
            )
            worker_entry(args.root, args.plan, args.attempt, consumer=supervised_research.worker)
            return 0
        require(
            args.plan is None and args.attempt is None and not args.gate,
            "supervised_worker_only_arguments",
        )
        if args.action == "supervised-register":
            require(
                args.request is not None
                and args.request_pin_file is not None
                and args.pins_root is not None,
                "supervised_registration_references",
            )
            spec = document(str(args.request))
            require(digest(spec) == read_pin(args.request_pin_file), "supervised_request_pin")
            supervised_research.register(spec, args.root, args.pins_root)
            supervised_state = dict(state="REGISTERED")
        else:
            require(args.plan_pin_file is not None, "supervised_plan_pin_required")
            pin = read_pin(args.plan_pin_file)
            if args.action == "supervised-run":
                supervised_state = supervised_research.run(
                    args.root, pin, args.steps, checkpoint_only=args.checkpoint_only
                )
            elif args.action == "supervised-stop":
                require(args.window is not None, "supervised_window_required")
                supervised_state = dict(
                    window=supervised_research.stop(args.root, pin, args.window)
                )
            else:
                supervised_view = supervised_research.status(args.root, pin)
                if args.report is not None:
                    require(args.report.parent == args.root, "supervised_report_destination")
                    write_new(args.report, FrozenJSON.freeze(supervised_view).payload_json)
                current = supervised_view["historical"]
                supervised_state = {
                    k: current[k]
                    for k in (
                        "active_window",
                        "completed",
                        "fits",
                        "evaluated",
                        "exact_checkpoint_reuse",
                        "failures",
                        "remaining_seconds",
                        "next_horizon",
                    )
                } | dict(prospective=supervised_view["prospective"]["status"])
                if "approved_slots" in current:
                    supervised_state.update(
                        approved_slots=current["approved_slots"],
                        exact_source_reuse=current["exact_source_reuse"],
                    )
        print(json.dumps(supervised_state))
        return int(
            args.action == "supervised-run"
            and supervised_state["reason"]
            not in (
                "requested_steps_complete",
                "operator_stop",
                "checkpoint_pause",
                "candidate_budget_exhausted",
                "time_budget_exhausted",
            )
        )
    if args.action == "autonomous-strategy-worker":
        from donghak_stock_vision.storage.autonomous_strategy import worker as strategy_worker

        require(
            args.plan is not None and args.attempt is not None and not args.gate,
            "strategy_worker_reference",
        )
        worker_entry(args.root, args.plan, args.attempt, consumer=strategy_worker)
        return 0
    if args.action == "predictive-research-worker":
        from donghak_stock_vision.storage.predictive_research import worker as research_worker

        require(
            args.plan is not None and args.attempt is not None and not args.gate,
            "predictive_research_worker_reference",
        )
        worker_entry(args.root, args.plan, args.attempt, consumer=research_worker)
        return 0
    if args.action == "failure-feedback-worker":
        from donghak_stock_vision.storage.failure_feedback import worker

        require(
            args.plan is not None and args.attempt is not None and not args.gate,
            "feedback_worker_reference",
        )
        worker_entry(args.root, args.plan, args.attempt, consumer=worker)
        return 0
    if args.action == "selection-worker":
        from donghak_stock_vision.storage.selection_review import assess_worker

        require(
            args.plan is not None and args.attempt is not None and not args.gate,
            "selection_worker_reference",
        )
        worker_entry(args.root, args.plan, args.attempt, consumer=assess_worker)
        return 0
    if args.action == "worker":
        require(args.plan is not None and args.attempt is not None, "autonomous_worker_reference")
        worker_entry(args.root, args.plan, args.attempt, args.gate)
        return 0
    require(
        not args.gate and args.plan is None and args.attempt is None,
        "autonomous_worker_only_arguments",
    )
    if args.action.startswith("autonomous-strategy-"):
        from donghak_stock_vision.storage import autonomous_strategy

        if args.action == "autonomous-strategy-register":
            require(
                args.request is not None
                and args.request_pin_file is not None
                and args.pins_root is not None,
                "strategy_registration_references",
            )
            value = document(str(args.request))
            require(digest(value) == read_pin(args.request_pin_file), "strategy_request_pin")
            autonomous_strategy.register(value, args.root, args.pins_root)
            strategy_state = dict(state="REGISTERED")
        else:
            require(args.plan_pin_file is not None, "strategy_plan_pin_required")
            strategy_pin = read_pin(args.plan_pin_file)
            if args.action == "autonomous-strategy-run":
                strategy_state = autonomous_strategy.run(args.root, strategy_pin)
            else:
                require(
                    args.report is not None and args.report.parent == args.root,
                    "strategy_report_destination",
                )
                strategy = autonomous_strategy.report(args.root, strategy_pin)
                write_new(args.report, strategy.payload_json)
                strategy_state = dict(state="RETAINED", result_id=strategy.to_dict()["result_id"])
        print(json.dumps(strategy_state))
        return 1 if strategy_state["state"] == "BLOCKED" else 0
    if args.action.startswith("predictive-research-"):
        from donghak_stock_vision.storage import predictive_research

        if args.action == "predictive-research-register":
            require(
                args.request is not None
                and args.request_pin_file is not None
                and args.pins_root is not None,
                "predictive_research_registration_references",
            )
            request = document(str(args.request))
            require(
                digest(request) == read_pin(args.request_pin_file),
                "predictive_research_request_pin",
            )
            predictive_research.register(request, args.root, args.pins_root)
            research_state = dict(state="REGISTERED")
        else:
            require(args.plan_pin_file is not None, "predictive_research_plan_pin_required")
            research_pin = read_pin(args.plan_pin_file)
            if args.action == "predictive-research-run":
                research_state = predictive_research.run(args.root, research_pin)
            else:
                require(
                    args.report is not None and args.report.parent == args.root,
                    "predictive_research_report_destination",
                )
                view = predictive_research.report(args.root, research_pin)
                write_new(args.report, view.payload_json)
                research_state = dict(state="RETAINED", result_id=view.to_dict()["result_id"])
        print(json.dumps(research_state))
        return 1 if research_state["state"] == "BLOCKED" else 0
    if args.action.startswith("failure-feedback-"):
        from donghak_stock_vision.storage import failure_feedback

        if args.action == "failure-feedback-register":
            require(
                args.request is not None
                and args.request_pin_file is not None
                and args.pins_root is not None,
                "feedback_registration_references",
            )
            request = document(str(args.request))
            require(digest(request) == read_pin(args.request_pin_file), "feedback_request_pin")
            failure_feedback.register(request, args.root, args.pins_root)
            feedback_state = dict(state="REGISTERED")
        else:
            require(args.plan_pin_file is not None, "feedback_plan_pin_required")
            pin = read_pin(args.plan_pin_file)
            if args.action == "failure-feedback-run":
                feedback_state = failure_feedback.run(args.root, pin)
            else:
                require(
                    args.report is not None and args.report.parent == args.root,
                    "feedback_report_destination",
                )
                view = failure_feedback.report(args.root, pin)
                write_new(args.report, view.payload_json)
                feedback_state = dict(state="RETAINED", feedback_id=view.to_dict()["feedback_id"])
        print(json.dumps(feedback_state))
        return 1 if feedback_state["state"] == "BLOCKED" else 0
    if args.action == "prediction-audit":
        from donghak_stock_vision.storage.prediction_audit import audit

        require(
            args.plan_pin_file is not None
            and args.report is not None
            and args.pins_root is not None,
            "prediction_audit_references",
        )
        pin_file = args.pins_root / "report.pin"
        view = audit(args.root, read_pin(args.plan_pin_file), outputs=(args.report, pin_file))
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.pins_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(
            all(p.stat().st_mode & 0o077 == 0 for p in (args.report.parent, args.pins_root)),
            "prediction_audit_private_outputs",
        )
        write_new(args.report, view.payload_json)
        write_new(pin_file, view.identifier)
        result = {
            k: v
            for k, v in view.to_dict().items()
            if k not in ("development", "readout", "readout_id", "retained_selection_comparisons")
        }
        print(json.dumps(result))
        return 0
    if args.action == "evidence-audit":
        from donghak_stock_vision.storage.evaluation_evidence import availability_audit

        require(
            args.plan_pin_file is not None
            and args.report is not None
            and args.pins_root is not None
            and not args.report.exists(),
            "evidence_audit_references",
        )
        require(
            args.report.parent.resolve() != args.pins_root.resolve()
            and not args.pins_root.is_relative_to(args.root)
            and not args.report.is_relative_to(args.root),
            "evidence_separate_outputs",
        )
        audit_view = availability_audit(
            args.root,
            read_pin(args.plan_pin_file),
            outputs=(args.report, args.pins_root / "report.pin"),
        )
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.pins_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_new(args.report, audit_view.payload_json)
        write_new(args.pins_root / "report.pin", audit_view.identifier)
        print(
            json.dumps(
                dict(
                    status="INSUFFICIENT_EVIDENCE",
                    new_training=0,
                    network_requests=0,
                    pit_qualified=False,
                    performance_approved=False,
                    execution_authorized=False,
                )
            )
        )
        return 0
    elif args.action == "selection-register":
        from donghak_stock_vision.storage.selection_review import register

        require(
            args.request is not None
            and args.request_pin_file is not None
            and args.pins_root is not None,
            "selection_registration_reference",
        )
        d = document(str(args.request))
        require(digest(d) == read_pin(args.request_pin_file), "selection_request_pin")
        register(d, args.root, args.pins_root)
        result = dict(registered=True, old_experiments_reexecuted=0, new_training_budget=0)
    elif args.action == "selection-prepare":
        from donghak_stock_vision.storage.selection_review import prepare_readout

        require(args.plan_pin_file is not None, "selection_plan_pin_required")
        result = prepare_readout(args.root, read_pin(args.plan_pin_file))
    elif args.action == "selection-assess":
        from donghak_stock_vision.storage.selection_review import run_assessment

        require(args.plan_pin_file is not None, "selection_plan_pin_required")
        result = run_assessment(args.root, read_pin(args.plan_pin_file))
    elif args.action == "fork-policy":
        from donghak_stock_vision.storage.stock_research import fork_policy

        require(
            args.source_root is not None
            and args.source_plan_pin_file is not None
            and args.pins_root is not None,
            "multi_stock_policy_fork_arguments",
        )
        fork_policy(
            args.source_root,
            read_pin(args.source_plan_pin_file),
            args.root,
            args.pins_root,
            policy=args.selection_policy,
        )
        result = dict(
            prepared=True, source_replayed=False, policy=args.selection_policy, network_requests=0
        )
    elif args.action == "derive":
        from donghak_stock_vision.storage.gemma_memory import derive

        require(
            args.source_root is not None
            and args.source_plan_pin_file is not None
            and args.pins_root is not None
            and args.model_digest_file is not None,
            "gemma_derivation_references",
        )
        derive(
            args.source_root,
            read_pin(args.source_plan_pin_file),
            args.root,
            args.pins_root,
            read_pin(args.model_digest_file),
            managed=args.managed_agents,
        )
        result = dict(dataset_prepared=True, network_requests=0)
    elif args.action == "prepare":
        require(
            args.request is not None
            and args.request_pin_file is not None
            and args.pins_root is not None,
            "autonomous_source_reference",
        )
        d = document(str(args.request))
        require(digest(d) == read_pin(args.request_pin_file), "autonomous_request_pin")
        value = prepare(d, args.root, args.pins_root).to_dict()
        result = dict(
            dataset_prepared=True,
            observations=value["dataset"]["observation_count"],
            experiments=value["experiments"],
            network_requests=0,
        )
    else:
        require(args.plan_pin_file is not None, "autonomous_plan_pin_required")
        pin = read_pin(args.plan_pin_file)
        if args.action == "run":
            require(
                (args.reuse_root is None) == (args.reuse_plan_pin_file is None), "gemma_cache_pair"
            )
            reuse = (
                None
                if args.reuse_root is None
                else (args.reuse_root, read_pin(args.reuse_plan_pin_file))
            )
            if args.agent == "managed":
                from donghak_stock_vision.storage.ai_management_config import load_management

                require(
                    args.management_root is not None
                    and args.model_config is not None
                    and args.model_config_sha256 is not None
                    and args.management_root.parent == args.root.parent,
                    "ai_managed_run_configuration",
                )
                service, _ = load_management(
                    args.management_root, args.model_config, args.model_config_sha256
                )
                with service.lease() as config:
                    result = run(
                        args.root,
                        pin,
                        args.steps,
                        agent="managed",
                        reuse=reuse,
                        agent_factory=lambda root, plan: service.agent_for(root, config),
                    ).to_dict()
                    service.record_run(args.root, pin, config, result, args.steps)
            else:
                result = run(args.root, pin, args.steps, agent=args.agent, reuse=reuse).to_dict()
        elif args.action == "report":
            from donghak_stock_vision.storage.adaptive_research_report import report

            plan, _ = context(args.root, pin, code_check=False)
            if plan["version"] in ("autonomous_plan_v5", "autonomous_plan_v6"):
                from donghak_stock_vision.storage.stock_research import report as stock_report

                result = stock_report(args.root, pin)
            else:
                result = report(args.root, pin)
        elif args.action == "status":
            result = status(args.root, pin).to_dict()
        elif args.action == "render-agent":
            require(args.output is not None, "autonomous_agent_output_required")
            render_agent(args.root, args.plan_pin_file, args.output)
            result = dict(rendered=True, installed=False, activated=False)
        else:
            require(args.experiment_id is not None, "autonomous_reproduction_id")
            plan, data = context(args.root, pin)
            with closing(ReadOnlyAnalysisStore(args.root / DB)) as store:
                done = terminals(store)
                original = store.get("autonomous_result", done[args.experiment_id]["result_id"])
            rebuilt, _ = execute(data, original["config"])
            for key in ("folds", "mean_ap", "stability_ap", "model_sha256", "parameters"):
                require(original[key] == rebuilt[key], "autonomous_reproduction_failed")
            result = dict(reproduced=True, network_requests=0)
    if args.report is not None:
        write_new(args.report, json.dumps(result))
    # Full source/PIN/model metadata stays in private memory/reports.
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k
                in (
                    "state",
                    "observations",
                    "experiments",
                    "completed",
                    "trained_this_run",
                    "cache_hits_this_run",
                    "agent",
                    "failed",
                    "remaining",
                    "executed_this_run",
                    "pause_reason",
                    "dataset_prepared",
                    "reproduced",
                    "rendered",
                    "installed",
                    "activated",
                )
            }
            | dict(
                pit_qualified=False,
                performance_approved=False,
                execution_authorized=False,
                network_requests=result.get("network_requests", 0),
                krx_requests=0,
                external_network_requests=result.get("external_network_requests", 0),
            )
        )
    )
    return 0
