"""Command line interface. JSON goes to stdout; UTC logs go to stderr."""

import argparse
import json
import logging
import sqlite3
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv

from donghak_stock_vision.config.settings import Settings
from donghak_stock_vision.data.schema import validate_range, validate_ticker
from donghak_stock_vision.ingestion.market import KRXMarketPipeline
from donghak_stock_vision.ingestion.pipeline import CollectionResult, Pipeline
from donghak_stock_vision.providers.base import MarketDataProvider
from donghak_stock_vision.providers.fake import FakeProvider
from donghak_stock_vision.providers.http import RequestGate, RetryingHTTP
from donghak_stock_vision.providers.krx import KRXProvider
from donghak_stock_vision.storage.sqlite import SQLiteStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="DonghakStockVision 2.0 — market data and signal analysis"
    )
    root.add_argument("--db", type=Path, help="SQLite path; overrides DSV_DB_PATH")
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("collect", "update", "query"):
        command = sub.add_parser(name)
        if name == "collect":
            universe = command.add_mutually_exclusive_group(required=True)
            universe.add_argument("--tickers", nargs="+")
            universe.add_argument(
                "--all", action="store_true", help="collect every KRX market row per date"
            )
        else:
            command.add_argument("--tickers", nargs="+", required=True)
        command.add_argument("--start", type=date.fromisoformat, required=True)
        command.add_argument("--end", type=date.fromisoformat, required=True)
        if name != "query":
            command.add_argument("--provider", choices=("krx", "fake"), default="krx")
            command.add_argument("--market", choices=("KOSPI", "KOSDAQ", "KONEX"))
        if name == "update":
            command.add_argument("--overlap-days", type=int, default=7)
    sub.add_parser("validate")
    from donghak_stock_vision.learning_cli import add_commands

    add_commands(sub)
    from donghak_stock_vision.decision_cli import add_commands as add_decisions

    add_decisions(sub)
    from donghak_stock_vision.backtest_cli import add_commands as add_backtest

    add_backtest(sub)
    from donghak_stock_vision.historical_cli import add_commands as add_historical

    add_historical(sub)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    load_dotenv(Path.cwd() / ".env", override=False)
    store = None
    try:
        if args.command == "backtest-input":
            from donghak_stock_vision.historical_cli import run as run_historical

            return run_historical(args)
        if args.command == "backtest":
            from donghak_stock_vision.backtest_cli import run as run_backtest

            return run_backtest(args)
        all_market = args.command == "collect" and args.all
        if all_market and args.provider != "krx":
            raise ValueError("--all is only supported with --provider krx")
        settings = Settings.from_env()
        path = args.db or settings.db_path
        if args.command in {"decide", "decisions"}:
            from donghak_stock_vision.decision_cli import run as run_decision

            return run_decision(args, path)
        if args.command in {"train", "evaluate", "infer", "signals"}:
            from donghak_stock_vision.learning_cli import run

            return run(args, path)
        if args.command != "validate":
            validate_range(args.start, args.end)
            for ticker in args.tickers or []:
                validate_ticker(ticker)
        if args.command in {"query", "validate"} and not path.is_file():
            raise ValueError("database does not exist; collect data first")
        if args.command in {"collect", "update"} and args.provider == "krx":
            if not settings.api_key.strip():
                raise ValueError(
                    "KRX_API_KEY and KRX service approval required; use --provider fake"
                )
        store = SQLiteStore(path)
        if args.command == "validate":
            issues = store.check_integrity()
            print(json.dumps({"valid": not issues, "issues": issues}))
            return 1 if issues else 0
        if args.command == "query":
            bars = [
                bar.to_dict()
                for ticker in dict.fromkeys(args.tickers)
                for bar in store.read(ticker, args.start, args.end)
            ]
            print(json.dumps(bars, ensure_ascii=False))
            return 0
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            provider: MarketDataProvider
            if args.provider == "fake":
                if args.market and args.market != "KOSPI":
                    raise ValueError("fake fixture only supports KOSPI")
                provider = FakeProvider()
            else:
                gate = RequestGate(path, settings.request_interval, settings.daily_request_limit)
                provider = KRXProvider(
                    settings.api_key, RetryingHTTP(client, gate), args.market or settings.market
                )
            result: CollectionResult
            if all_market:
                assert isinstance(provider, KRXProvider)
                result = KRXMarketPipeline(provider, store).collect(args.start, args.end)
            else:
                result = Pipeline(provider, store).collect(
                    args.tickers,
                    args.start,
                    args.end,
                    incremental=args.command == "update",
                    overlap_days=getattr(args, "overlap_days", 7),
                )
        print(json.dumps(asdict(result)))
        return 1 if result.failed else 0
    except ValueError as error:
        logging.getLogger(__name__).error("configuration_or_validation_error: %s", error)
        return 2
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).error("storage_or_filesystem_error")
        return 2
    finally:
        if store is not None:
            store.close()
