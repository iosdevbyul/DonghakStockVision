"""Executable synthetic capture/signal/risk/PAPER round trip; no live transport."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, utc
from donghak_stock_vision.forward_signal_demo_cli import prepare
from donghak_stock_vision.storage.forward_paper_journal import GENESIS, execute, inspect
from donghak_stock_vision.storage.forward_signal_journal import GENESIS as SIGNAL_GENESIS
from donghak_stock_vision.storage.forward_signal_journal import append_signal


def configuration(at: datetime) -> FrozenJSON:
    """Explicit illustrative limits, never a permissive production default."""
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_account_v1",
            "mode": "PAPER_SYNTHETIC",
            "account_id": "synthetic-005930",
            "initial_cash": "10000",
            "initial_at": at.isoformat(),
            "risk": {
                "policy_id": "synthetic-risk-only",
                "max_order_notional": "5000",
                "max_position_fraction": "0.5",
                "max_exposure_fraction": "0.8",
                "max_daily_loss": "1000",
                "max_price_age_seconds": 60,
                "max_fx_age_seconds": 60,
                "max_account_age_seconds": 60,
                "max_valuation_age_seconds": 60,
                "risk_timezone": "Asia/Seoul",
            },
            "mechanics": {
                "policy_id": "synthetic-fill-only",
                "price_quantum": "0.01",
                "cost_quantum": "0.01",
                "buy_fee_rate": "0.001",
                "sell_fee_rate": "0.001",
                "sell_tax_rate": "0.002",
                "slippage_rate": "0.01",
                "max_session_age_seconds": 60,
                "max_commands": 128,
            },
        }
    )


def action(
    root: Path, request: FrozenJSON, journal: Path, pin: str, signal_id: str, at: datetime
) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_action_v1",
            "mode": "PAPER_SYNTHETIC",
            "capture_root": str(root),
            "request": request.to_dict(),
            "request_pin": request.identifier,
            "signal_journal": str(journal),
            "signal_journal_pin": pin,
            "signal_id": signal_id,
            "quantity": 2,
            "at": at.isoformat(),
            "fill_at": (at + timedelta(seconds=1)).isoformat(),
            "session_open": True,
            "trading_enabled": True,
            "daily_pnl": "0",
        }
    )


def demonstrate(workspace: Path) -> FrozenJSON:
    require(not workspace.exists(), "paper_demo_requires_new_directory")
    workspace.mkdir(mode=0o700)
    root, journal = workspace / "capture", workspace / "signals.sqlite3"
    captured = prepare(root, (100, 80, 120, 80)).to_dict()
    now = datetime.now(UTC)
    buy = FrozenJSON.freeze(
        captured
        | {
            "fast": 1,
            "slow": 2,
            "sources": captured["sources"][:3],
            "calculated_at": now.isoformat(),
        }
    )
    sell = FrozenJSON.freeze(
        captured | {"fast": 1, "slow": 2, "calculated_at": (now + timedelta(seconds=3)).isoformat()}
    )
    a = append_signal(journal, root, buy, buy.identifier, SIGNAL_GENESIS, now)
    b = append_signal(
        journal,
        root,
        sell,
        sell.identifier,
        a.to_dict()["head_pin"],
        utc(sell.to_dict()["calculated_at"]),
    )
    pin = b.to_dict()["head_pin"]
    config = configuration(now)
    jobs = [
        action(
            root,
            r,
            journal,
            pin,
            e.to_dict()["entry"]["entry"]["decision"]["signal_id"],
            utc(r.to_dict()["calculated_at"]),
        )
        for r, e in ((buy, a), (sell, b))
    ]
    history_path = workspace / "paper.sqlite3"
    dry = execute(history_path, config, config.identifier, GENESIS, jobs[0], dry_run=True)
    first = execute(history_path, config, config.identifier, GENESIS, jobs[0])
    duplicate = execute(
        history_path, config, config.identifier, first.to_dict()["head_pin"], jobs[0]
    )
    second = execute(history_path, config, config.identifier, first.to_dict()["head_pin"], jobs[1])
    hold_root, hold_journal = workspace / "hold-capture", workspace / "hold-signals.sqlite3"
    hold_request = prepare(hold_root, (100, 100, 100, 100))
    hold_at = max(datetime.now(UTC), utc(sell.to_dict()["calculated_at"]) + timedelta(seconds=2))
    hold_request = FrozenJSON.freeze(
        hold_request.to_dict() | {"calculated_at": hold_at.isoformat()}
    )
    hold_record = append_signal(
        hold_journal, hold_root, hold_request, hold_request.identifier, SIGNAL_GENESIS, hold_at
    ).to_dict()
    hold_job = action(
        hold_root,
        hold_request,
        hold_journal,
        hold_record["head_pin"],
        hold_record["entry"]["entry"]["decision"]["signal_id"],
        hold_at,
    )
    hold = execute(history_path, config, config.identifier, second.to_dict()["head_pin"], hold_job)
    verified = inspect(history_path, config, config.identifier, hold.to_dict()["head_pin"])
    files = {
        "account.json": config,
        "buy-action.json": jobs[0],
        "sell-action.json": jobs[1],
        "hold-action.json": hold_job,
    }
    for name, owner in files.items():
        path = workspace / name
        path.write_text(owner.payload_json)
        path.chmod(0o600)
    outputs: dict[str, Any] = {
        "dry_run": dry.to_dict(),
        "buy": first.to_dict(),
        "sell": second.to_dict(),
        "hold": hold.to_dict(),
        "duplicate": duplicate.to_dict(),
        "verified": verified.to_dict(),
    }
    return FrozenJSON.freeze(
        {
            "version": "forward_paper_demo_v1",
            "origin": "offline_fixture",
            "account_pin": config.identifier,
            "journal_head_pin": hold.to_dict()["head_pin"],
            "live_requests": 0,
            "outputs": outputs,
        }
    )
