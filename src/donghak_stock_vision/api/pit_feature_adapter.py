"""Read-only real OHLCV -> PIT snapshot -> causal feature candidate adapter."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from donghak_stock_vision.api.pit_inference_input import PITInferenceInput
from donghak_stock_vision.data.learning import (
    FEATURE_VERSION,
    FEATURES,
    AnalysisError,
    digest,
    timestamp,
)
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import capture, snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.storage.base import MarketDataStore


@dataclass(frozen=True)
class PITFeatureBuildResult:
    candidate: PITInferenceInput | None
    blockers: tuple[str, ...]
    inference_authorized: bool = False


def build_pit_features(
    store: MarketDataStore,
    ticker: str,
    *,
    start: date,
    end: date,
    as_of: datetime,
    quality: dict[str, object] | None,
) -> PITFeatureBuildResult:
    """Use the Phase 1 storage boundary; never execute models or override quality flags."""
    try:
        cutoff = timestamp(as_of)
        snapshot = capture(
            store,
            [ticker],
            start,
            end,
            cutoff,
            "point_in_time",
            quality=quality,
        )
        if snapshot["data_origin"] != "real" or snapshot["mode"] != "point_in_time":
            raise AnalysisError("real_pit_source_required")
        bars = snapshot_bars(snapshot, ticker)
        if not bars:
            raise AnalysisError("insufficient_history")
        latest = bars[-1]
        # Full session completion must be observable at the chosen PIT cutoff.
        session_complete = datetime.combine(
            latest.trading_date + timedelta(days=1),
            datetime.min.time(),
            SEOUL,
        )
        if cutoff < session_complete:
            raise AnalysisError("last_session_not_completed")
        values = feature_vector(bars, snapshot, cutoff)
        candidate = PITInferenceInput(
            schemaVersion="pit_inference_input_v1",
            ticker=ticker,
            mode="point_in_time",
            dataOrigin="real",
            featureVersion=FEATURE_VERSION,
            featureNames=FEATURES,
            featureValues=values,
            lastTradingDate=latest.trading_date.isoformat(),
            asOf=cutoff,
            dataCutoff=session_complete,
            sourceCutoff=cutoff,
            snapshotId=digest(snapshot),
            inputHash=digest([bar.to_dict() for bar in bars[-12:]]),
            qualityFlags=tuple(snapshot["quality_flags"]),
        )
    except (AnalysisError, ValueError, KeyError, TypeError) as error:
        return PITFeatureBuildResult(None, (str(error),))
    return PITFeatureBuildResult(
        candidate,
        ("snapshot_quality_review_required", "operational_execution_not_authorized"),
    )
