"""Bounded collection authorization metadata; no PIT or execution authority."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import SEOUL, validate_ticker


@dataclass(frozen=True)
class ForwardObservationScope:
    """Short, pinned collection window; no research membership or approval authority."""

    valid_from: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        for instant in (self.valid_from, self.valid_until):
            require(
                type(instant) is datetime and instant.utcoffset() == timedelta(0),
                "forward_scope_utc_required",
            )
        require(
            self.valid_from < self.valid_until
            and self.valid_until - self.valid_from <= timedelta(hours=24),
            "forward_scope_window_invalid",
        )

    def validate_day(self, day: date) -> None:
        self.__post_init__()
        require(
            1 <= (self.valid_from.astimezone(SEOUL).date() - day).days <= 7,
            "forward_scope_requires_recent_completed_date",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "version": "prospective_read_only_observation_v1",
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ForwardObservationScope":
        d = fields(value, "version valid_from valid_until")
        require(d["version"] == "prospective_read_only_observation_v1", "forward_scope_version")
        owner = cls(utc(d["valid_from"]), utc(d["valid_until"]))
        require(owner.to_dict() == d, "forward_scope_canonical_utc_required")
        return owner


@dataclass(frozen=True)
class ForwardCapturePlan:
    market: str
    ticker: str
    day: date
    partition: str
    split: ExplicitResearchSplit | None
    split_pin: str | None
    max_requests: int
    max_response_bytes: int
    max_seconds: int
    interval_seconds: int
    daily_limit: int
    mode: str
    authorization_reference: str | None
    terms_reference: str | None
    observation_scope: ForwardObservationScope | None = None

    def __post_init__(self) -> None:
        require(self.market in ("KOSPI", "KOSDAQ", "KONEX"), "unsupported_market")
        validate_ticker(self.ticker)
        require(type(self.day) is date and self.day.weekday() < 5, "invalid_session_date")
        if self.observation_scope is None:
            require(type(self.split) is ExplicitResearchSplit, "split_owner_required")
            assert self.split is not None
            self.split.__post_init__()
            require(self.split.identifier == sha(self.split_pin), "split_pin_mismatch")
            require(self.partition in ("training", "tuning"), "forbidden_partition")
            span = self.split.training if self.partition == "training" else self.split.validation
            require(span.start <= self.day <= span.end, "outside_authorized_partition")
        else:
            require(
                type(self.observation_scope) is ForwardObservationScope
                and self.partition == "forward_observation"
                and self.split is None
                and self.split_pin is None,
                "forward_scope_cannot_relabel_research_partition",
            )
            self.observation_scope.validate_day(self.day)
            require(
                self.mode == "authorized_provider"
                and self.market == "KOSPI"
                and self.ticker == "005930"
                and self.max_requests == self.daily_limit == 1
                and self.max_seconds <= 30,
                "forward_scope_operator_pilot_only",
            )
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
        research = (
            {
                "partition": self.partition,
                "split": self.split.to_dict() if self.split is not None else None,
                "split_pin": self.split_pin,
            }
            if self.observation_scope is None
            else {"observation_scope": self.observation_scope.to_dict()}
        )
        return FrozenJSON.freeze(
            {
                "version": (
                    "forward_capture_plan_v1"
                    if self.observation_scope is None
                    else "forward_capture_plan_v2"
                ),
                "provider": "krx",
                "currency": "KRW",
                "market": self.market,
                "ticker": self.ticker,
                "day": self.day.isoformat(),
                **research,
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
        version = value.get("version")
        require(version in ("forward_capture_plan_v1", "forward_capture_plan_v2"), "plan_contract")
        forward = version == "forward_capture_plan_v2"
        fields = set(cls.__dataclass_fields__) - {"observation_scope"}
        if forward:
            fields = fields - {"partition", "split", "split_pin"} | {"observation_scope"}
        require(
            set(value) == fields | {"version", "provider", "currency", "concurrency"}, "plan_fields"
        )
        require(
            value["provider"] == "krx"
            and value["currency"] == "KRW"
            and type(value["concurrency"]) is int
            and value["concurrency"] == 1,
            "plan_contract",
        )
        args = {name: value[name] for name in fields}
        if forward:
            args.update(
                partition="forward_observation",
                split=None,
                split_pin=None,
                observation_scope=ForwardObservationScope.from_dict(value["observation_scope"]),
            )
        else:
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
