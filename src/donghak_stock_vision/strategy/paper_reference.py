"""Allowlisted synthetic price-band reference. Not a model, recommendation or optimizer."""

from decimal import Decimal
from hashlib import sha256
from importlib.resources import files

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, integer, money, require
from donghak_stock_vision.data.learning import digest


def code_reference() -> FrozenJSON:
    # Only this installed package resource; no caller-controlled path or executable loader.
    content = files("donghak_stock_vision.strategy").joinpath("paper_reference.py").read_bytes()
    return FrozenJSON.freeze(
        {
            "artifact_id": "synthetic-price-band-code",
            "version": "v1",
            "content_hash": sha256(content).hexdigest(),
        }
    )


def artifact() -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": "synthetic_price_band_strategy_v1",
            "code": code_reference().to_dict(),
            "parameters": {
                "entry": "positive_decimal",
                "exit": "positive_decimal_above_entry",
                "quantity": "integer_1_to_100",
            },
            "rules": ["price_lte_entry_buy", "price_gte_exit_sell", "otherwise_wait"],
            "data_origin": "synthetic",
            "usage_restriction": "research_only",
        }
    )


def strategy_reference() -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "artifact_id": "synthetic-price-band-strategy",
            "version": "v1",
            "content_hash": digest(artifact().to_dict()),
        }
    )


def parameters(value: FrozenJSON) -> tuple[Decimal, Decimal, int]:
    p = fields(value.to_dict(), "entry exit quantity")
    entry, exit_price = (Decimal(money(p[key], positive=True)) for key in ("entry", "exit"))
    require(entry < exit_price, "entry_must_precede_exit")
    quantity = integer(p["quantity"], positive=True)
    require(quantity <= 100, "reference_quantity_bound")
    return entry, exit_price, quantity


def decide(value: FrozenJSON, price: Decimal | None) -> str:
    entry, exit_price, _ = parameters(value)
    if price is None:
        return "WAIT"
    require(type(price) is Decimal and price.is_finite() and price > 0, "reference_price_invalid")
    return "BUY" if price <= entry else "SELL" if price >= exit_price else "WAIT"
