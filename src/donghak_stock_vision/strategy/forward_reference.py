"""Observed-bar SMA crossover reference; signals are not orders or investment advice."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from importlib.resources import files

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require


def parameters(fast: int, slow: int) -> None:
    require(
        type(fast) is int and type(slow) is int and 1 <= fast < slow <= 30,
        "sma_parameters_require_1_le_fast_lt_slow_le_30",
    )


def reference() -> dict[str, str]:
    return {
        "strategy_id": "forward-observed-sma-crossover",
        "strategy_version": "1",
        "code_sha256": sha256(
            files("donghak_stock_vision.strategy").joinpath("forward_reference.py").read_bytes()
        ).hexdigest(),
    }


def calculate(closes: tuple[int, ...], fast: int, slow: int) -> FrozenJSON:
    parameters(fast, slow)
    require(type(closes) is tuple and len(closes) <= 32, "bounded_close_window_required")
    require(all(type(c) is int and 0 < c < 2**63 for c in closes), "invalid_close")
    if len(closes) < slow + 1:
        return FrozenJSON.freeze(
            {
                "signal": "INSUFFICIENT_DATA",
                "reason": "slow_plus_one_observations_required",
                "indicators": None,
                "required_observations": slow + 1,
            }
        )
    previous = closes[-slow - 1 : -1]
    current = closes[-slow:]
    previous_fast, previous_slow = sum(previous[-fast:]), sum(previous)
    current_fast, current_slow = sum(current[-fast:]), sum(current)
    before = previous_fast * slow - previous_slow * fast
    after = current_fast * slow - current_slow * fast
    signal, reason = (
        ("BUY", "fast_crossed_above_slow")
        if before <= 0 < after
        else ("SELL", "fast_crossed_below_slow")
        if before >= 0 > after
        else ("HOLD", "no_crossing_in_latest_observed_pair")
    )
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        indicators = {
            key: {
                "sum": total,
                "count": count,
                "mean": format(
                    (Decimal(total) / Decimal(count)).quantize(Decimal("0.000000000000000001")), "f"
                ),
            }
            for key, total, count in (
                ("previous_fast", previous_fast, fast),
                ("previous_slow", previous_slow, slow),
                ("current_fast", current_fast, fast),
                ("current_slow", current_slow, slow),
            )
        }
    return FrozenJSON.freeze(
        {
            "signal": signal,
            "reason": reason,
            "indicators": indicators,
            "required_observations": slow + 1,
        }
    )
