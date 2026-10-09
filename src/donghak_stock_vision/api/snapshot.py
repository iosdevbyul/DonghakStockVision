"""Validated immutable JSON snapshot adapter for the read-only API."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from donghak_stock_vision.api.models import (
    BaselineBacktestSnapshot,
    CoverageSummary,
    EvidenceDetail,
    EvidenceLayerId,
    EvidenceLayerSummary,
    ModelDetail,
    ModelDirection,
    ModelSummary,
    ProjectStatus,
    StockDetail,
    StockSummary,
)


class ResearchAPISnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projectStatus: ProjectStatus
    coverage: CoverageSummary
    models: tuple[ModelDetail, ...]
    evidence: tuple[EvidenceDetail, ...]
    baselineBacktest: BaselineBacktestSnapshot
    stocks: tuple[StockDetail, ...]


class SnapshotValidationError(ValueError):
    """Raised when a configured API snapshot is missing or invalid."""


class SnapshotReader:
    """Serve a validated snapshot without opening mutable research stores."""

    def __init__(self, snapshot: ResearchAPISnapshot) -> None:
        self._snapshot = snapshot

    @classmethod
    def from_path(cls, path: Path) -> "SnapshotReader":
        if not path.is_file():
            raise SnapshotValidationError("API snapshot file does not exist")
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            snapshot = ResearchAPISnapshot.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise SnapshotValidationError("API snapshot is invalid") from error
        return cls(snapshot)

    def project_status(self) -> ProjectStatus:
        return self._snapshot.projectStatus

    def coverage_summary(self) -> CoverageSummary:
        return self._snapshot.coverage

    def model_summaries(self) -> tuple[ModelSummary, ...]:
        return tuple(
            ModelSummary.model_validate(
                model.model_dump(
                    include={
                        "id",
                        "direction",
                        "modelName",
                        "role",
                        "validationAveragePrecision",
                        "oosAveragePrecision",
                        "note",
                    }
                )
            )
            for model in self._snapshot.models
        )

    def model_detail(self, direction: ModelDirection) -> ModelDetail | None:
        return next((model for model in self._snapshot.models if model.id == direction), None)

    def evidence_layers(self) -> tuple[EvidenceLayerSummary, ...]:
        return tuple(
            EvidenceLayerSummary.model_validate(
                layer.model_dump(include={"id", "title", "state", "summary", "sourceLabel"})
            )
            for layer in self._snapshot.evidence
        )

    def evidence_detail(self, layer: EvidenceLayerId) -> EvidenceDetail | None:
        return next((item for item in self._snapshot.evidence if item.id == layer), None)

    def baseline_backtest(self) -> BaselineBacktestSnapshot:
        return self._snapshot.baselineBacktest

    def stocks(self) -> tuple[StockSummary, ...]:
        return tuple(
            StockSummary.model_validate(
                stock.model_dump(include={"ticker", "name", "market", "sector"})
            )
            for stock in self._snapshot.stocks
        )

    def stock_detail(self, ticker: str) -> StockDetail | None:
        return next((stock for stock in self._snapshot.stocks if stock.ticker == ticker), None)
