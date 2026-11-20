"""Explicit finite research sessions; no credentials, collection or scheduling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("memory-research")
    p.add_argument(
        "action",
        choices=(
            "prepare",
            "run",
            "report",
            "publish",
            "campaign-prepare",
            "campaign-run",
            "campaign-report",
            "campaign-recover",
            "campaign-publish",
            "campaign-worker",
        ),
    )
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--session-pin-file", type=Path)
    p.add_argument("--request", type=Path)
    p.add_argument("--request-pin-file", type=Path)
    p.add_argument("--pins-root", type=Path)
    p.add_argument("--arm", choices=("rule", "gemma_blind", "gemma_memory"))
    p.add_argument("--report", type=Path)
    p.add_argument("--campaign-pin-file", type=Path)
    p.add_argument("--attempt-id")
    p.add_argument("--steps", type=int, default=1)


def run(args: argparse.Namespace) -> int:
    from donghak_stock_vision.storage import memory_guided_research as owner

    if args.action == "campaign-worker":
        from donghak_stock_vision.storage import research_campaign as campaign

        require(
            args.campaign_pin_file is not None and args.attempt_id is not None,
            "campaign_worker_arguments",
        )
        campaign.worker(args.root, read_pin(args.campaign_pin_file), args.attempt_id)
        return 0
    require(args.report is not None, "memory_report_required")
    require(
        args.report.is_absolute()
        and args.report.resolve() == args.report
        and not args.report.exists(),
        "memory_new_report",
    )
    require(
        args.report.parent == args.root or args.report.parent == args.root.parent,
        "memory_report_destination",
    )
    if args.action.startswith("campaign-"):
        from donghak_stock_vision.storage import research_campaign as campaign

        if args.action == "campaign-prepare":
            require(
                args.request is not None
                and args.request_pin_file is not None
                and args.pins_root is not None,
                "campaign_prepare_arguments",
            )
            request = document(str(args.request))
            require(digest(request) == read_pin(args.request_pin_file), "campaign_request_pin")
            result = dict(campaign_id=campaign.prepare(request, args.root, args.pins_root))
        else:
            require(args.campaign_pin_file is not None, "campaign_pin_required")
            pin = read_pin(args.campaign_pin_file)
            result = (
                campaign.run(args.root, pin, args.steps)
                if args.action == "campaign-run"
                else campaign.report(args.root, pin)
            )
            if args.action == "campaign-recover":
                result = campaign.recover(args.root, pin)
            if args.action == "campaign-publish":
                result = campaign.publication(result)
        write_new(args.report, json.dumps(result))
        print(
            json.dumps(
                dict(
                    status="RECORDED",
                    performance_approved=False,
                    independent_generalization="NOT_ESTABLISHED",
                )
            )
        )
        return 0
    if args.action == "prepare":
        require(
            args.request is not None
            and args.request_pin_file is not None
            and args.pins_root is not None,
            "memory_prepare_references",
        )
        request = document(str(args.request))
        require(digest(request) == read_pin(args.request_pin_file), "memory_request_pin")
        result = dict(session_id=owner.prepare(request, args.root, args.pins_root))
    else:
        require(args.session_pin_file is not None, "memory_session_reference")
        pin = read_pin(args.session_pin_file)
        if args.action == "run":
            require(args.arm is not None, "memory_arm_required")
            result = owner.run(args.root, pin, args.arm)
        elif args.action == "publish":
            result = owner.publication(owner.report(args.root, pin))
        else:
            result = owner.report(args.root, pin)
    write_new(args.report, json.dumps(result))
    print(
        json.dumps(
            dict(
                status="RECORDED",
                performance_approved=False,
                independent_generalization="NOT_ESTABLISHED",
            )
        )
    )
    return 0
