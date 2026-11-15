"""Explicit bounded research commands; no collection or unattended activation."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

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
        "action", choices=("prepare", "run", "status", "worker", "reproduce", "render-agent")
    )
    p.add_argument("--root", type=Path, required=True)
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


def run_command(args: argparse.Namespace) -> int:
    if args.action == "worker":
        require(args.plan is not None and args.attempt is not None, "autonomous_worker_reference")
        worker_entry(args.root, args.plan, args.attempt, args.gate)
        return 0
    require(
        not args.gate and args.plan is None and args.attempt is None,
        "autonomous_worker_only_arguments",
    )
    if args.action == "prepare":
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
            result = run(args.root, pin, args.steps).to_dict()
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
                network_requests=0,
            )
        )
    )
    return 0
