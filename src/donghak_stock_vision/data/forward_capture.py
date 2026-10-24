"""Bounded collection authorization metadata; no PIT or execution authority."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require, sha
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import validate_ticker


@dataclass(frozen=True)
class ForwardCapturePlan:
    market: str
    ticker: str
    day: date
    partition: str
    split: ExplicitResearchSplit
    split_pin: str
    max_requests: int
    max_response_bytes: int
    max_seconds: int
    interval_seconds: int
    daily_limit: int
    mode: str
    authorization_reference: str | None
    terms_reference: str | None

    def __post_init__(self) -> None:
        require(self.market in ("KOSPI", "KOSDAQ", "KONEX"), "unsupported_market")
        validate_ticker(self.ticker)
        require(type(self.day) is date and self.day.weekday() < 5, "invalid_session_date")
        require(type(self.split) is ExplicitResearchSplit, "split_owner_required")
        self.split.__post_init__()
        require(self.split.identifier == sha(self.split_pin), "split_pin_mismatch")
        require(self.partition in ("training", "tuning"), "forbidden_partition")
        span = self.split.training if self.partition == "training" else self.split.validation
        require(span.start <= self.day <= span.end, "outside_authorized_partition")
        for value, upper in (
            (self.max_requests, 3),
            (self.max_response_bytes, 2 * 1024 * 1024),
            (self.max_seconds, 120),
            (self.interval_seconds, 60),
            (self.daily_limit, 9500),
        ):
            require(type(value) is int and 1 <= value <= upper, "invalid_capture_budget")
        require(self.max_requests <= self.daily_limit, "daily_budget_too_small")
        require(self.mode in ("offline_fixture", "authorized_provider"), "invalid_capture_mode")
        for ref in (self.authorization_reference, self.terms_reference):
            require(ref is None or (type(ref) is str and 0 < len(ref) <= 256), "invalid_reference")

    @property
    def document(self) -> FrozenJSON:
        return FrozenJSON.freeze(
            {
                "version": "forward_capture_plan_v1",
                "provider": "krx",
                "currency": "KRW",
                "market": self.market,
                "ticker": self.ticker,
                "day": self.day.isoformat(),
                "partition": self.partition,
                "split": self.split.to_dict(),
                "split_pin": self.split_pin,
                "max_requests": self.max_requests,
                "max_response_bytes": self.max_response_bytes,
                "max_seconds": self.max_seconds,
                "interval_seconds": self.interval_seconds,
                "daily_limit": self.daily_limit,
                "mode": self.mode,
                "authorization_reference": self.authorization_reference,
                "terms_reference": self.terms_reference,
                "concurrency": 1,
            }
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ForwardCapturePlan":
        require(type(value) is dict, "plan_object_required")
        fields = set(cls.__dataclass_fields__)
        require(
            set(value) == fields | {"version", "provider", "currency", "concurrency"}, "plan_fields"
        )
        require(
            value["version"] == "forward_capture_plan_v1"
            and value["provider"] == "krx"
            and value["currency"] == "KRW"
            and type(value["concurrency"]) is int
            and value["concurrency"] == 1,
            "plan_contract",
        )
        args = {name: value[name] for name in fields}
        args["split"] = ExplicitResearchSplit.from_dict(value["split"])
        args["day"] = date.fromisoformat(value["day"])
        return cls(**args)

    def blockers(self, credential_present: bool) -> list[str]:
        if self.mode == "offline_fixture":
            return []
        return [
            reason
            for ok, reason in (
                (credential_present, "missing_krx_api_key"),
                (bool(self.authorization_reference), "service_authorization_required"),
                (bool(self.terms_reference), "terms_and_frequency_review_required"),
            )
            if not ok
        ]
