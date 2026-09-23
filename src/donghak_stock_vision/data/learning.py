"""Versioned Phase 2 contracts. Prices always enter through MarketDataStore."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

Mode = Literal["historical_research", "point_in_time"]
Direction = Literal["up", "down"]
FEATURE_VERSION = "ohlcv_value_v1"
LABEL_VERSION = "reversal_barrier_v1"
FEATURES = (
    "return_1",
    "return_3",
    "return_5",
    "close_to_sma10",
    "volatility_10",
    "range_to_close",
    "volume_change_10",
    "trading_value_change_10",
)
HORIZON = 5
MIN_BARRIER = 0.02
VOLATILITY_MULTIPLIER = 2.0
THRESHOLD = 0.5
EVENT = "직전 반대 추세 문맥에서 5개 미래 세션 내 단기 역방향 장벽 최초 도달"


class AnalysisError(ValueError):
    """Machine-readable failure without fabricated scores."""


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise AnalysisError("timezone_required")
    return result.astimezone(UTC)


def mode_value(value: str) -> Mode:
    value = value.replace("-", "_")
    if value not in {"historical_research", "point_in_time"}:
        raise AnalysisError("invalid_mode")
    return cast(Mode, value)


def event_contract(session_basis: str) -> dict[str, Any]:
    return {
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "event_description": EVENT,
        "horizon_sessions": HORIZON,
        "min_barrier": MIN_BARRIER,
        "volatility_multiplier": VOLATILITY_MULTIPLIER,
        "event_scope_note": (
            "거래일 달력 미확인: 5개 관측 일봉 기준"
            if session_basis == "observed_bars_unverified"
            else "검증된 세션 기준"
        ),
        "session_basis": session_basis,
        "calibration": "none",
    }


@dataclass(frozen=True)
class Sample:
    ticker: str
    trading_date: date
    anchor_at: datetime
    values: tuple[float, ...]
    direction: Direction | None
    label: int | None
    label_start: date | None
    label_end: date | None
    label_available_at: datetime | None
    label_status: str
    input_hash: str
    label_sessions: tuple[date, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("trading_date", "label_start", "label_end", "anchor_at", "label_available_at"):
            value[key] = value[key].isoformat() if value[key] is not None else None
        value["label_sessions"] = [d.isoformat() for d in self.label_sessions]
        value["values"] = list(self.values)
        return value

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Sample":
        data = dict(row)
        for key in ("trading_date", "label_start", "label_end"):
            data[key] = date.fromisoformat(data[key]) if data[key] is not None else None
        for key in ("anchor_at", "label_available_at"):
            data[key] = timestamp(data[key]) if data[key] is not None else None
        data["label_sessions"] = tuple(date.fromisoformat(d) for d in data["label_sessions"])
        data["values"] = tuple(data["values"])
        return cls(**data)


@dataclass(frozen=True)
class Minimums:
    tickers: int = 5
    dates: int = 504
    train_rows: int = 1000
    train_dates: int = 252
    train_class: int = 50
    holdout_rows: int = 200
    holdout_dates: int = 63
    holdout_class: int = 20

    @classmethod
    def synthetic(cls) -> "Minimums":
        """Only usable when every source row is explicitly synthetic."""
        return cls(1, 80, 20, 10, 2, 6, 4, 1)
