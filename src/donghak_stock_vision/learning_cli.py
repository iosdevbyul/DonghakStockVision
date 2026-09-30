"""Phase 2 CLI, isolated from the Phase 1 command path and optional dependencies."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError, mode_value, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.models.service import EvaluationService, TrainingService
from donghak_stock_vision.signals.service import SignalQueryService, SignalService
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.sqlite import SQLiteStore


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name in ("train", "evaluate", "infer", "signals"):
        command = sub.add_parser(name)
        command.add_argument("--analysis-db", type=Path, required=True)
        command.add_argument(
            "--mode", choices=("historical-research", "point-in-time"), required=name != "signals"
        )
        if name in {"train", "infer"}:
            command.add_argument("--tickers", nargs="+", required=True)
            command.add_argument("--quality-manifest", type=Path)
        if name == "train":
            command.add_argument("--research-calendar", type=Path)
            command.add_argument("--start", type=date.fromisoformat, required=True)
            command.add_argument("--end", type=date.fromisoformat, required=True)
            command.add_argument("--snapshot-as-of", type=timestamp)
            command.add_argument("--acknowledge-research-limitations", action="store_true")
            command.add_argument(
                "--synthetic-test",
                action="store_true",
                help="only for Fake data; permanently synthetic_test_only",
            )
        if name != "evaluate":
            command.add_argument("--as-of", type=timestamp)
        if name != "train":
            command.add_argument("--model-version", required=name in {"infer", "evaluate"})
        if name == "infer":
            command.add_argument("--anchor-date", type=date.fromisoformat)
        if name in {"infer", "signals"}:
            command.add_argument("--snapshot-id")
        if name == "signals":
            command.add_argument(
                "--scope", choices=("research", "analysis", "operational"), default="operational"
            )
            command.add_argument("--direction", choices=("up", "down", "all"), default="all")
            command.add_argument("--ticker")
            command.add_argument("--limit", type=int, default=100)


def _required(value: Any, name: str) -> Any:
    if value is None:
        raise AnalysisError(f"{name}_required")
    return value


def run(args: argparse.Namespace, market_path: Path) -> int:
    analysis = None
    market = None
    try:
        mode = mode_value(args.mode) if args.mode else None
        needs_market = args.command == "train" or (
            args.command == "infer" and mode == "point_in_time"
        )
        if market_path.resolve() == args.analysis_db.resolve():
            raise AnalysisError("analysis_db_must_be_separate")
        if needs_market:
            if not market_path.is_file():
                raise AnalysisError("missing_market_database")
            market = SQLiteStore(market_path)
        if args.command in {"evaluate", "signals"} and not args.analysis_db.is_file():
            raise AnalysisError("missing_analysis_database")
        analysis = AnalysisStore(args.analysis_db)
        quality = None
        if getattr(args, "quality_manifest", None):
            quality = json.loads(args.quality_manifest.read_text())
        result: Any
        if args.command == "train":
            assert mode is not None and market is not None
            cutoff = (
                _required(args.snapshot_as_of, "snapshot_as_of")
                if mode == "historical_research"
                else _required(args.as_of, "as_of")
            )
            if mode == "historical_research" and args.as_of is not None:
                raise AnalysisError("use_snapshot_as_of_for_research")
            if mode == "point_in_time" and (
                args.snapshot_as_of or args.acknowledge_research_limitations
            ):
                raise AnalysisError("research_options_forbidden_in_pit")
            snapshot = capture(
                market,
                args.tickers,
                args.start,
                args.end,
                cutoff,
                mode,
                quality=quality,
                acknowledge=args.acknowledge_research_limitations,
                synthetic=args.synthetic_test,
                research_calendar=(
                    ResearchCalendarPolicy.from_dict(json.loads(args.research_calendar.read_text()))
                    if args.research_calendar
                    else None
                ),
            )
            snapshot_id = analysis.put("snapshot", snapshot)
            result = TrainingService(analysis).train(snapshot_id)
            exit_code = 0 if result["model_version"] else 1
        elif args.command == "evaluate":
            assert mode is not None
            result = EvaluationService(analysis).evaluate(args.model_version, mode)
            exit_code = 0
        elif args.command == "infer":
            service = SignalService(analysis)
            if mode == "historical_research":
                if args.as_of or args.quality_manifest:
                    raise AnalysisError("research_inference_requires_frozen_snapshot_only")
                result = service.research(
                    args.model_version,
                    _required(args.snapshot_id, "snapshot_id"),
                    args.tickers,
                    _required(args.anchor_date, "anchor_date"),
                )
            else:
                assert market is not None
                if args.snapshot_id or args.anchor_date:
                    raise AnalysisError("research_options_forbidden_in_pit")
                result = service.point_in_time(
                    market,
                    args.model_version,
                    args.tickers,
                    _required(args.as_of, "as_of"),
                    quality,
                )
            exit_code = (
                1
                if any(
                    r["input_status"] != "ok" or r["signal_state"] == "insufficient_model_data"
                    for r in result
                )
                else 0
            )
        else:
            query = SignalQueryService(analysis)
            if args.scope == "research":
                if mode not in {None, "historical_research"} or args.as_of:
                    raise AnalysisError("mode_mismatch")
                result = query.get_research_analyses(
                    _required(args.model_version, "model_version"),
                    _required(args.snapshot_id, "snapshot_id"),
                    args.direction,
                    args.ticker,
                    args.limit,
                )
            elif args.scope == "analysis":
                if mode != "point_in_time" or args.snapshot_id:
                    raise AnalysisError("mode_mismatch")
                result = query.get_point_in_time_analyses(
                    _required(args.model_version, "model_version"),
                    _required(args.as_of, "as_of"),
                    args.ticker,
                )
            else:
                if mode not in {None, "point_in_time"} or args.snapshot_id:
                    raise AnalysisError("mode_mismatch")
                directions = ("up", "down") if args.direction == "all" else (args.direction,)
                result = {
                    d: query.operational(
                        d,
                        _required(args.as_of, "as_of"),
                        args.model_version,
                        args.ticker,
                        args.limit,
                    )
                    for d in directions
                }
            exit_code = 0
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return exit_code
    except AnalysisError as error:
        print(json.dumps({"status": str(error), "up_score": None, "down_score": None}))
        return 1
    finally:
        if market:
            market.close()
        if analysis:
            analysis.close()
