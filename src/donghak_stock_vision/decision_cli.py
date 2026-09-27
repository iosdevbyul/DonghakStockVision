"""Research decision CLI. Phase 1 and 2 commands remain untouched."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import timestamp
from donghak_stock_vision.storage.decision import DecisionStore, ensure_separate
from donghak_stock_vision.strategy.adapter import assemble
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from donghak_stock_vision.strategy.engine import decide


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = sub.add_parser("decide")
    run.add_argument("--decision-db", type=Path, required=True)
    run.add_argument("--analysis-db", type=Path, required=True)
    run.add_argument("--scope", choices=("research", "operational"), required=True)
    run.add_argument("--ticker", required=True)
    run.add_argument("--as-of", type=timestamp, required=True)
    run.add_argument("--analysis-id", required=True)
    for name in ("policy", "account", "orders", "market-context"):
        run.add_argument(f"--{name}", type=Path)
    query = sub.add_parser("decisions")
    query.add_argument("--decision-db", type=Path, required=True)
    query.add_argument("--scope", choices=("research", "operational"), default="operational")
    query.add_argument("--ticker")
    query.add_argument("--as-of")
    query.add_argument("--limit", type=int, default=100)
    query.add_argument("--decision-id")
    query.add_argument("--replay", action="store_true")


def read_object(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value


def run(args: argparse.Namespace, market_path: Path) -> int:
    protected = [market_path]
    if args.command == "decide":
        protected += [args.analysis_db]
        protected += [p for p in (args.policy, args.account, args.orders, args.market_context) if p]
    ensure_separate(args.decision_db, protected)
    if args.command == "decisions" and not args.decision_db.is_file():
        raise ValueError("missing_decision_database")
    store = None
    try:
        if args.command == "decide":
            request = {
                "scope": args.scope,
                "ticker": args.ticker,
                "decision_as_of": args.as_of.isoformat(),
                "analysis_id": args.analysis_id,
            }
            # Operational requests cannot gain meaning through local policy/approval files.
            if args.scope == "operational":
                policy, account, orders, market = DecisionPolicy.from_dict({}), None, None, None
            else:
                policy = DecisionPolicy.from_dict(read_object(args.policy) or {})
                account, orders, market = (
                    read_object(p) for p in (args.account, args.orders, args.market_context)
                )
            inputs = assemble(request, args.analysis_db, account, orders, market)
            result = decide(inputs, policy)
            store = DecisionStore(args.decision_db, protected)
            output: Any = store.save(inputs, policy, result)
            code = 1 if output["status"] == "blocked" else 0
        else:
            store = DecisionStore(args.decision_db, protected)
            if args.replay and not args.decision_id:
                raise ValueError("decision_id_required_for_replay")
            if args.decision_id:
                output = (
                    store.replay(args.decision_id) if args.replay else store.get(args.decision_id)
                )
                if output["decision_scope"] != args.scope:
                    raise ValueError("decision_scope_mismatch")
            else:
                output = store.query(args.scope, args.ticker, args.as_of, args.limit)
            code = 0
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return code
    finally:
        if store:
            store.close()
