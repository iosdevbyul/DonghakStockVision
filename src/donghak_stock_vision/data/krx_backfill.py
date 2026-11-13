"""Bounded historical acquisition owners; no forward, PIT or trading authority."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, utc

POLICY = "operator_krx_historical_backfill_v1"
PLAN = "krx_historical_backfill_plan_v1"


@dataclass(frozen=True)
class BackfillPolicy:
    document: FrozenJSON

    def __post_init__(self) -> None:
        d = fields(
            self.document.to_dict(),
            "version acknowledgement reviewed_at valid_from valid_until start end "
            "scheduler_config scheduler_config_pin research_database research_database_sha256 "
            "archive_root source_pins_root calendars max_requests daily_limit "
            "reserved_daily_requests min_rows required_tickers "
            "known_request_ledgers",
        )
        require(
            d["version"] == POLICY
            and d["acknowledgement"] == "private_historical_acquisition_not_forward_authority",
            "historical_operator_review_required",
        )
        start, end = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
        require(
            start.isoformat() == d["start"] and end.isoformat() == d["end"],
            "canonical_backfill_window_required",
        )
        require(
            date(2010, 1, 4) <= start <= end and (end - start).days <= 366,
            "bounded_kospi_historical_window_required",
        )
        first, last, reviewed = (utc(d[n]) for n in ("valid_from", "valid_until", "reviewed_at"))
        require(
            first <= reviewed < last and last - first <= timedelta(days=31),
            "historical_review_window_invalid",
        )
        for key in ("valid_from", "valid_until", "reviewed_at"):
            require(utc(d[key]).isoformat() == d[key], "canonical_backfill_time_required")
        for key in ("scheduler_config_pin", "research_database_sha256"):
            sha(d[key])
        require(
            all(
                type(d[k]) is int
                for k in ("max_requests", "daily_limit", "reserved_daily_requests")
            )
            and 1 <= d["max_requests"] <= 366
            and 1 <= d["reserved_daily_requests"] <= 100
            and 1 <= d["daily_limit"] <= 9500 - d["reserved_daily_requests"],
            "historical_budget_invalid",
        )
        require(
            type(d["calendars"]) is list
            and 1 <= len(d["calendars"]) <= 13
            and type(d["known_request_ledgers"]) is list
            and len(d["known_request_ledgers"]) <= 16,
            "historical_evidence_budget",
        )
        require(
            type(d["min_rows"]) is int and 1 <= d["min_rows"] <= 10000,
            "backfill_minimum_rows_required",
        )
        require(
            type(d["required_tickers"]) is list
            and 1 <= len(d["required_tickers"]) <= 25
            and d["required_tickers"] == sorted(set(d["required_tickers"]))
            and all(
                type(t) is str and len(t) == 6 and t.isascii() and t.isdecimal()
                for t in d["required_tickers"]
            ),
            "backfill_required_tickers_invalid",
        )
        for record in d["calendars"]:
            fields(record, "raw source normalized_pin")
            sha(record["normalized_pin"])

    @property
    def values(self) -> dict[str, Any]:
        return self.document.to_dict()


@dataclass(frozen=True)
class BackfillPlan:
    document: FrozenJSON

    def __post_init__(self) -> None:
        d = fields(self.document.to_dict(), "version policy_pin dates skipped inventory_pin")
        require(d["version"] == PLAN, "unsupported_backfill_plan")
        sha(d["policy_pin"])
        sha(d["inventory_pin"])
        require(
            type(d["dates"]) is list
            and len(d["dates"]) <= 366
            and d["dates"] == sorted(set(d["dates"]), reverse=True)
            and type(d["skipped"]) is list
            and len(d["skipped"]) <= 367,
            "backfill_dates_not_bounded_or_ordered",
        )
        for value in d["dates"]:
            require(date.fromisoformat(value).isoformat() == value, "canonical_backfill_date")
        skipped_days: set[str] = set()
        for value in d["skipped"]:
            item = fields(value, "day reason")
            require(
                date.fromisoformat(item["day"]).isoformat() == item["day"]
                and item["day"] not in skipped_days
                and item["reason"]
                in {
                    "non_session_original_retained",
                    "non_session",
                    "original_already_retained",
                    "bars_already_stored",
                    "request_budget_not_selected",
                },
                "invalid_backfill_skip_classification",
            )
            skipped_days.add(item["day"])
        require(
            not skipped_days.intersection(d["dates"]), "backfill_date_both_selected_and_skipped"
        )


@dataclass(frozen=True)
class BackfillRequestLimits:
    """The existing CaptureHTTP streaming recorder consumes these same limits."""

    max_requests: int = 1
    max_response_bytes: int = 2 * 1024**2
    max_seconds: int = 30
    interval_seconds: int = 1

    def __post_init__(self) -> None:
        require(
            all(
                type(v) is int
                for v in (
                    self.max_requests,
                    self.max_response_bytes,
                    self.max_seconds,
                    self.interval_seconds,
                )
            )
            and self.max_requests == 1
            and 1 <= self.max_response_bytes <= 2 * 1024**2
            and 1 <= self.max_seconds <= 30
            and self.interval_seconds >= 1,
            "single_bounded_backfill_request_required",
        )
