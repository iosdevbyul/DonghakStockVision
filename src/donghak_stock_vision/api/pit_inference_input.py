"""Point-in-time inference input validation without model execution or authorization."""

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from donghak_stock_vision.api.execution_gate import ExecutionGateAssessment
from donghak_stock_vision.data.learning import FEATURES, FEATURE_VERSION
from donghak_stock_vision.data.schema import SEOUL, validate_ticker


class PITInferenceInput(BaseModel):
    """Immutable candidate feature vector; raw provenance remains externally verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: Literal["pit_inference_input_v1"]
    ticker: str
    mode: Literal["point_in_time"]
    dataOrigin: Literal["real"]
    featureVersion: str
    featureNames: tuple[str, ...]
    featureValues: tuple[float, ...]
    lastTradingDate: str
    asOf: datetime
    dataCutoff: datetime
    sourceCutoff: datetime
    snapshotId: str = Field(min_length=1)
    inputHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualityFlags: tuple[str, ...]

    @model_validator(mode="after")
    def validate_vector(self) -> "PITInferenceInput":
        validate_ticker(self.ticker)
        if self.featureVersion != FEATURE_VERSION or self.featureNames != FEATURES:
            raise ValueError("feature_contract_mismatch")
        if len(self.featureValues) != len(FEATURES) or any(
            not math.isfinite(value) for value in self.featureValues
        ):
            raise ValueError("invalid_feature_values")
        return self


@dataclass(frozen=True)
class PITInputAssessment:
    structurally_valid: bool
    inference_authorized: bool
    blockers: tuple[str, ...]


def inspect_pit_input(
    candidate_bytes: bytes,
    gate: ExecutionGateAssessment,
) -> PITInputAssessment:
    """Never perform inference, infer score, or turn advisory evidence into permission."""
    try:
        candidate = PITInferenceInput.model_validate_json(candidate_bytes)
    except (ValueError, UnicodeError):
        return PITInputAssessment(False, False, ("invalid_pit_input_contract",))

    blockers: list[str] = []
    from datetime import date

    try:
        trading_day = date.fromisoformat(candidate.lastTradingDate)
        if trading_day.isoformat() != candidate.lastTradingDate:
            raise ValueError("noncanonical date")
    except ValueError:
        blockers.append("invalid_last_trading_date")
    else:
        timestamps = (candidate.asOf, candidate.dataCutoff, candidate.sourceCutoff)
        if any(value.utcoffset() is None for value in timestamps):
            blockers.append("timezone_required")
        else:
            closed_after = datetime.combine(trading_day + timedelta(days=1), time.min, SEOUL)
            if (
                candidate.dataCutoff < closed_after
                or candidate.sourceCutoff < candidate.dataCutoff
                or candidate.sourceCutoff > candidate.asOf
            ):
                blockers.append("pit_cutoff_chronology_invalid")
            if trading_day >= candidate.asOf.astimezone(SEOUL).date():
                blockers.append("same_day_or_future_bar_forbidden")
    if candidate.qualityFlags:
        blockers.append("unapproved_input_quality_flags")
    structurally_valid = not blockers
    if not gate.inference_authorized:
        blockers.append("operational_execution_not_authorized")
    # Even an externally presented gate with inference_authorized=True is not
    # a capability to execute; authoritative provenance is not yet implemented.
    blockers.append("independent_input_provenance_not_attested")
    return PITInputAssessment(structurally_valid, False, tuple(dict.fromkeys(blockers)))
