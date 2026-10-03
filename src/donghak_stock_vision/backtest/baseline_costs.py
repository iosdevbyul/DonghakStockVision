"""Sourced KOSPI tax rates and explicit research commission; no broker execution."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_DOWN, Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.research_contract import OrderSide
from donghak_stock_vision.backtest.validation import integer, money, require, sha
from donghak_stock_vision.data.learning import digest

# Legal effective dates are settlement/transfer dates, NOT research session dates.
# Deliberately bounded: future legislation must not silently inherit the last rate.
TAX_PERIODS = (
    ("2022-01-01", "2022-12-31", "0.0008"),
    ("2023-01-01", "2023-12-31", "0.0005"),
    ("2024-01-01", "2024-12-31", "0.0003"),
    ("2025-01-01", "2025-12-31", "0"),
    ("2026-01-01", "2026-10-01", "0.0005"),
)
TAX_SOURCES = (
    "https://law.go.kr/LSW/lsRvsDocListP.do?chrClsCd=010102&lsId=005028",
    "https://www.law.go.kr/lsInfoP.do?chrClsCd=010102&lsiSeq=282431&viewCls=lsRvsDocInfoR",
    "https://www.law.go.kr/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1032879999",
    "https://securities.miraeasset.com/bbs/board/message/view.do?categoryId=66&messageId=2336448&selectedId=1000000202&selectedId=1000000203&selectedId=1000003101",
)


def tax_schedule() -> dict[str, Any]:
    return {
        "version": "kospi_ordinary_shares_transfer_rates_20261004_v1",
        "scope": "ordinary_taxable_KOSPI_share_sale_no_exemptions",
        "date_basis": "settlement_date",
        "sources": list(TAX_SOURCES),
        "periods": [
            dict(start=start, end=end, sell_tax_rate=rate, sell_market_levy_rate="0.0015")
            for start, end, rate in TAX_PERIODS
        ],
        "settlement_calendar": "external_verified_evidence_required_not_research_calendar",
    }


def tax_rates(settlement_date: date) -> tuple[Decimal, Decimal]:
    require(type(settlement_date) is date, "settlement_date_required")
    for start, end, rate in TAX_PERIODS:
        if start <= settlement_date.isoformat() <= end:
            return Decimal(rate), Decimal("0.0015")
    require(False, "BLOCKED_ON_COST_POLICY:tax_date_outside_verified_schedule")
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class BaselineCostPolicy:
    """Explicit approved assumptions. No implicit tax or price rounding choice."""

    price_quantum: Decimal
    tax_rounding: str

    def __post_init__(self) -> None:
        quantum = money(self.price_quantum, positive=True)
        require(quantum == "1", "whole_KRW_price_required")
        object.__setattr__(self, "price_quantum", Decimal(quantum))
        require(self.tax_rounding == "down_per_component_KRW", "tax_rounding_unapproved")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "baseline_costs_v1",
            "commission": {
                "version": "user_approved_research_commission_v1",
                "rate_per_side": "0.00015",
                "basis": "post_slippage_executed_notional_per_fill",
                "rounding": "ceiling_whole_KRW",
                "minimum": None,
                "maximum": None,
                "provenance": "explicit_user_assumption_not_historical_broker_fact",
            },
            "taxes": tax_schedule(),
            "tax_rounding": self.tax_rounding,
            "price_rounding": "adverse_whole_KRW_research_assumption",
            "price_quantum": money(self.price_quantum),
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def commission(self, executed_notional: Decimal) -> Decimal:
        value = Decimal(money(executed_notional))
        with localcontext(Context(prec=80)):
            return (value * Decimal("0.00015")).quantize(Decimal(1), rounding=ROUND_CEILING)

    def adverse_price(self, reference: Decimal, side: OrderSide) -> Decimal:
        value = Decimal(money(reference, positive=True))
        require(type(side) is OrderSide, "order_side_required")
        with localcontext(Context(prec=80)):
            result = value * (Decimal("1.001") if side == OrderSide.BUY else Decimal("0.999"))
            result = result.quantize(
                self.price_quantum, rounding=ROUND_CEILING if side == OrderSide.BUY else ROUND_DOWN
            )
            require(result > 0, "nonpositive_slipped_price")
            return result

    def charges(
        self,
        *,
        execution_price: Decimal,
        quantity: int,
        side: OrderSide,
        execution_date: date,
        settlement_date: date,
        settlement_evidence_id: str,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Commission, transaction tax, levy. Caller supplies verified settlement evidence.

        This arithmetic is not a fill or a settlement calendar implementation.
        Taxes are separate from commission and are charged on SELL only.
        """
        sha(settlement_evidence_id)
        require(type(execution_date) is date, "execution_date_required")
        rates = tax_rates(settlement_date)
        require(settlement_date >= execution_date, "settlement_before_execution")
        require(type(side) is OrderSide, "order_side_required")
        integer(quantity, positive=True)
        price = Decimal(money(execution_price, positive=True))
        with localcontext(Context(prec=80)):
            notional = price * quantity
            commission = self.commission(notional)
            if side == OrderSide.BUY:
                return commission, Decimal(0), Decimal(0)
            tax, levy = (
                (notional * rate).quantize(Decimal(1), rounding=ROUND_DOWN) for rate in rates
            )
            return commission, tax, levy
