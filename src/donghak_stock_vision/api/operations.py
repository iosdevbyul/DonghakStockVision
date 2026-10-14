"""Read-only projections of validated DART evidence and a pinned research snapshot.

No execution, repair, key loading or network clients belong in this module.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Literal
from zipfile import BadZipFile
from zlib import error as ZlibError

from donghak_stock_vision.api.models import FrozenModel
from donghak_stock_vision.api.ohlcv import OhlcvMarketReader
from donghak_stock_vision.api.service import ResearchAPIReader
from donghak_stock_vision.backtest import dart_document_batch as batch
from donghak_stock_vision.backtest import dart_multi_batch_session as session
from donghak_stock_vision.backtest.collect_corporate_action_documents import PILOT_DIRECTORY
from donghak_stock_vision.backtest.dart_batch_orchestrator import snapshot_lock

EvidenceStatus = Literal["validated", "partial", "invalid", "unavailable"]


class OperationRun(FrozenModel):
    id: str
    operationType: Literal["dart_batch_acquisition", "dart_session", "market_ingestion"]
    startedAt: str | None = None
    completedAt: str | None = None
    executionStatus: Literal["completed", "incomplete", "failed", "interrupted", "not_started"]
    evidenceStatus: EvidenceStatus
    recordCount: int | None
    sourceType: Literal["dart_ledger", "sqlite_collection_audit"] = "dart_ledger"
    semanticApproval: Literal[False] = False
    blockingStatus: Literal["blocked", "unknown"] | None = None
    diagnostic: str | None = None


class RunsPayload(FrozenModel):
    schemaVersion: Literal["operations_v1"] = "operations_v1"
    items: tuple[OperationRun, ...]
    total: int
    offset: int
    limit: int
    evidenceStatus: EvidenceStatus
    diagnostic: str | None = None


class SourceFreshness(FrozenModel):
    sourceId: str
    latestVerifiedDataAt: str | None = None
    latestSuccessfulIngestionAt: str | None = None
    latestDataDate: str | None = None
    freshnessStatus: Literal["fresh", "stale", "unknown", "blocked"]
    evidenceStatus: EvidenceStatus
    recordCount: int | None = None
    thresholdSeconds: int | None = None
    freshnessBasis: Literal["acquisition_age", "unavailable"] = "unavailable"
    blockingReason: str | None = None


class FreshnessPayload(FrozenModel):
    schemaVersion: Literal["operations_v1"] = "operations_v1"
    items: tuple[SourceFreshness, ...]


class OperationAlert(FrozenModel):
    id: str
    category: Literal[
        "pipeline_blocked",
        "missing_evidence",
        "validation_failure",
        "execution_interruption",
        "acquisition_failure",
        "unresolved_reservation",
        "freshness_unknown",
        "freshness_stale",
    ]
    severity: Literal["warning", "error"]
    basis: Literal["recorded", "advisory"]
    sourceId: str
    diagnostic: str
    occurredAt: str | None = None
    blockingStatus: Literal["blocked", "unknown"] | None = None
    verifiedCount: int | None = None
    requiredCount: int | None = None


class AlertsPayload(FrozenModel):
    schemaVersion: Literal["operations_v1"] = "operations_v1"
    items: tuple[OperationAlert, ...]
    total: int
    offset: int
    limit: int


def utc_stamp(value: object) -> str | None:
    """Reject absent/naive timestamps, never replace them with wall clock time."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None:
            return None
        return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def run_order(item: OperationRun) -> tuple[datetime, str]:
    stamp = item.completedAt or item.startedAt
    return (datetime.fromisoformat(stamp) if stamp else datetime.min.replace(tzinfo=UTC), item.id)


class OperationsReader:
    """Root is operator configured, never supplied by an HTTP query.

    Each DART response uses the existing non-writing shared kernel lock and validates
    the entire ledger and parent sessions before projecting any completion claim.
    Research state comes from an already validated immutable snapshot reader.
    """

    def __init__(
        self,
        root: Path | None = None,
        research: ResearchAPIReader | None = None,
        *,
        acquisition_threshold_seconds: int | None = None,
        market: OhlcvMarketReader | None = None,
    ) -> None:
        if acquisition_threshold_seconds is not None and acquisition_threshold_seconds <= 0:
            raise ValueError("positive acquisition freshness threshold required")
        self.acquisition_threshold_seconds = acquisition_threshold_seconds
        self.market = market
        self.root = root
        self.research = research

    def runs(self, offset: int, limit: int) -> RunsPayload:
        return self._combined(offset, limit)[0]

    def _combined(
        self, offset: int, limit: int | None
    ) -> tuple[RunsPayload, int | None, str | None, RunsPayload]:
        payload, count, stamp = self._capture(0, 100_000)
        items = list(payload.items)
        diagnostic = payload.diagnostic
        if self.market is not None:
            try:
                market_items: list[OperationRun] = []
                for row in self.market.collection_audit():
                    if row.status not in {"success", "empty", "failed"} or row.rows < 0:
                        raise ValueError("invalid collection audit")
                    market_items.append(
                        OperationRun(
                            id=f"collection-{row.identifier}",
                            operationType="market_ingestion",
                            completedAt=utc_stamp(row.finished_at),
                            executionStatus="failed" if row.status == "failed" else "completed",
                            evidenceStatus="partial",
                            recordCount=row.rows,
                            sourceType="sqlite_collection_audit",
                            diagnostic="recorded_outcome_not_provenance",
                        )
                    )
                items.extend(market_items)
            except (ValueError, TypeError, OverflowError, OSError, SQLiteError):
                diagnostic = "market_audit_validation_failed"
        items.sort(
            key=run_order,
            reverse=True,
        )
        return (
            RunsPayload(
                items=tuple(
                    items[offset : offset + limit] if limit is not None else items[offset:]
                ),
                total=len(items),
                offset=offset,
                limit=limit if limit is not None else len(items),
                evidenceStatus="invalid"
                if diagnostic and diagnostic.endswith("validation_failed")
                else "partial"
                if any(item.evidenceStatus == "partial" for item in items)
                else payload.evidenceStatus,
                diagnostic=diagnostic,
            ),
            count,
            stamp,
            payload,
        )

    def _capture(self, offset: int, limit: int) -> tuple[RunsPayload, int | None, str | None]:
        unavailable = RunsPayload(
            items=(),
            total=0,
            offset=offset,
            limit=limit,
            evidenceStatus="unavailable",
            diagnostic="dart_evidence_not_configured",
        )
        if self.root is None:
            return unavailable, None, None
        try:
            if not any(
                (self.root / path).exists()
                for path in (
                    batch.DIRECTORY,
                    session.DIRECTORY,
                    PILOT_DIRECTORY,
                    "docs/evidence/baseline-dart/artifacts/corporate-action-events.json",
                )
            ):
                return unavailable, None, None
            if (self.root / ".data").is_symlink():
                raise ValueError("invalid_root")
            with snapshot_lock(self.root):
                plan = batch.plan_for(self.root)
                view = batch.inventory(self.root, plan)
                parents = session.checked_records(self.root)
                items: list[OperationRun] = []
                runs = self.root / batch.DIRECTORY / "runs"
                for unit in sorted(runs.iterdir()) if runs.exists() else ():
                    approval = batch.load(unit / "approval.json")
                    states = [view["states"][r] for r in approval["receipts"]]
                    complete = (unit / "events/complete.json").is_file()
                    if complete and any(state != "Verified Local" for state in states):
                        raise ValueError("inconsistent completion")
                    failed = "Failed" in states
                    unresolved = any(s in {"Attempted", "Blocked"} for s in states)
                    items.append(
                        OperationRun(
                            id=unit.name,
                            operationType="dart_batch_acquisition",
                            executionStatus="completed"
                            if complete
                            else "failed"
                            if failed
                            else "incomplete",
                            evidenceStatus="partial" if unresolved else "validated",
                            blockingStatus="blocked" if unresolved or failed else None,
                            recordCount=sum(s == "Verified Local" for s in states),
                            diagnostic="unresolved_reservation"
                            if unresolved
                            else "acquisition_failure"
                            if failed
                            else None,
                        )
                    )
                for path in parents:
                    complete = (path / "complete.json").is_file()
                    started = (path / "approval.json").exists() or (
                        path / "approval-intent.json"
                    ).exists()
                    # An unapproved Draft is not an execution record.
                    if not started:
                        continue
                    draft = batch.load(path / "draft.json")
                    items.append(
                        OperationRun(
                            id=path.name,
                            operationType="dart_session",
                            executionStatus="completed"
                            if complete
                            else "interrupted"
                            if (path / "stopped.json").exists()
                            else "incomplete",
                            evidenceStatus="validated" if complete else "partial",
                            recordCount=sum(
                                view["states"][r] == "Verified Local" for r in draft["receipts"]
                            ),
                        )
                    )
                stamps: list[str] = []
                source_dirs = [self.root / PILOT_DIRECTORY]
                if runs.exists():
                    source_dirs.extend(unit / "sources" for unit in runs.iterdir())
                for directory in source_dirs:
                    for receipt in directory.glob("*.json"):
                        metadata = batch.load(receipt)
                        if view["states"].get(metadata["params"]["rcept_no"]) != "Verified Local":
                            continue
                        stamp = utc_stamp(metadata.get("retrieved_at"))
                        if stamp:
                            stamps.append(stamp)
                # Completion markers contain no execution timestamps. IDs break ties.
                items.sort(
                    key=run_order,
                    reverse=True,
                )
                payload = RunsPayload(
                    items=tuple(items[offset : offset + limit]),
                    total=len(items),
                    offset=offset,
                    limit=limit,
                    evidenceStatus="validated",
                )
                return (
                    payload,
                    sum(state == "Verified Local" for state in view["states"].values()),
                    max(stamps, key=datetime.fromisoformat, default=None),
                )
        except (
            ValueError,
            OSError,
            TypeError,
            KeyError,
            RuntimeError,
            EOFError,
            BadZipFile,
            ZlibError,
        ):
            # Never return exception text, approval contents, file names or paths.
            payload = RunsPayload(
                items=(),
                total=0,
                offset=offset,
                limit=limit,
                evidenceStatus="invalid",
                diagnostic="dart_evidence_validation_failed",
            )

            return payload, None, None

    def freshness(self) -> FreshnessPayload:
        runs, count, stamp = self._capture(0, 1)
        return self._freshness(runs, count, stamp)

    def _freshness(
        self, runs: RunsPayload, count: int | None, stamp: str | None
    ) -> FreshnessPayload:
        rows: list[SourceFreshness] = []
        if self.research is None:
            rows.append(
                SourceFreshness(
                    sourceId="market_snapshot",
                    freshnessStatus="unknown",
                    evidenceStatus="unavailable",
                    blockingReason="research_snapshot_unavailable",
                )
            )
        else:
            coverage = self.research.coverage_summary()
            try:
                latest_date = date.fromisoformat(coverage.endDate).isoformat()
            except ValueError:
                latest_date = None
            rows.append(
                SourceFreshness(
                    sourceId="market_snapshot",
                    latestDataDate=latest_date,
                    recordCount=coverage.totalBars if coverage.totalBars >= 0 else None,
                    freshnessStatus="unknown",
                    evidenceStatus="partial",
                    blockingReason="timestamp_and_freshness_policy_unavailable",
                )
            )
        status: Literal["fresh", "stale", "unknown", "blocked"] = "unknown"
        threshold = self.acquisition_threshold_seconds
        if runs.evidenceStatus == "invalid":
            status = "blocked"
        elif threshold is not None and stamp is not None:
            age = (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()
            if age >= 0:
                status = "stale" if age > threshold else "fresh"
        rows.append(
            SourceFreshness(
                sourceId="dart_acquisition",
                latestSuccessfulIngestionAt=stamp,
                recordCount=count,
                freshnessStatus=status,
                thresholdSeconds=threshold,
                freshnessBasis="acquisition_age" if threshold is not None else "unavailable",
                evidenceStatus=runs.evidenceStatus,
                blockingReason=runs.diagnostic
                or ("acquisition_timestamp_or_policy_unavailable" if status == "unknown" else None),
            )
        )
        return FreshnessPayload(items=tuple(rows))

    def alerts(self, offset: int, limit: int) -> AlertsPayload:
        alerts: list[OperationAlert] = []
        runs, count, stamp, dart = self._combined(0, None)
        if dart.diagnostic:
            alerts.append(
                OperationAlert(
                    id="dart-evidence",
                    category="validation_failure"
                    if dart.evidenceStatus == "invalid"
                    else "missing_evidence",
                    severity="error" if dart.evidenceStatus == "invalid" else "warning",
                    basis="recorded" if dart.evidenceStatus == "invalid" else "advisory",
                    sourceId="dart_acquisition",
                    diagnostic=dart.diagnostic,
                )
            )
        if runs.diagnostic == "market_audit_validation_failed":
            alerts.append(
                OperationAlert(
                    id="market-audit",
                    category="validation_failure",
                    severity="error",
                    basis="recorded",
                    sourceId="market_ingestion",
                    diagnostic="market_audit_validation_failed",
                )
            )
        for run in runs.items:
            if run.executionStatus != "completed":
                category: Literal[
                    "execution_interruption", "acquisition_failure", "unresolved_reservation"
                ] = (
                    "unresolved_reservation"
                    if run.diagnostic == "unresolved_reservation"
                    else "acquisition_failure"
                    if run.executionStatus == "failed"
                    else "execution_interruption"
                )
                alerts.append(
                    OperationAlert(
                        id=f"run-{run.id}",
                        category=category,
                        severity="error",
                        basis="recorded",
                        sourceId=run.id,
                        diagnostic=category,
                        occurredAt=run.completedAt,
                        blockingStatus=run.blockingStatus,
                    )
                )
        if self.research is None:
            for identifier in ("corporate-actions", "runner-tail", "run-input-freeze"):
                alerts.append(
                    OperationAlert(
                        id=identifier,
                        category="missing_evidence",
                        severity="warning",
                        basis="advisory",
                        sourceId=identifier,
                        diagnostic="readiness_evidence_unavailable",
                        blockingStatus="unknown",
                    )
                )
            alerts.append(
                OperationAlert(
                    id="research-evidence",
                    category="missing_evidence",
                    severity="warning",
                    basis="advisory",
                    sourceId="research_snapshot",
                    diagnostic="research_snapshot_unavailable",
                )
            )
        else:
            labels = {
                "Corporate actions": "corporate-actions",
                "Runner tail integration": "runner-tail",
                "Run input freeze": "run-input-freeze",
            }
            readiness = {
                step.label: step.state for step in self.research.baseline_backtest().readiness
            }
            corporate = self.research.evidence_detail("corporate-actions")
            coverage = None
            if corporate is not None:
                metric = next(
                    (m.value for m in corporate.metrics if m.label == "Complete coverage"), ""
                )
                coverage = re.fullmatch(r"([0-9]+) / ([0-9]+)", metric)
                if coverage and not (
                    0 <= int(coverage[1]) <= int(coverage[2])
                    and int(coverage[2]) == self.research.coverage_summary().universe
                ):
                    coverage = None
            for label, identifier in labels.items():
                state = readiness.get(label)
                if state == "blocked":
                    alerts.append(
                        OperationAlert(
                            id=identifier,
                            category="pipeline_blocked",
                            severity="error",
                            basis="recorded",
                            sourceId=identifier,
                            diagnostic="snapshot_pipeline_blocked",
                            blockingStatus="blocked",
                            verifiedCount=int(coverage[1])
                            if identifier == "corporate-actions" and coverage
                            else None,
                            requiredCount=int(coverage[2])
                            if identifier == "corporate-actions" and coverage
                            else None,
                        )
                    )
                elif state is None:
                    alerts.append(
                        OperationAlert(
                            id=identifier,
                            category="missing_evidence",
                            severity="warning",
                            basis="advisory",
                            sourceId=identifier,
                            diagnostic="readiness_evidence_unavailable",
                            blockingStatus="unknown",
                        )
                    )
        for source in self._freshness(dart, count, stamp).items:
            if source.freshnessStatus in {"unknown", "stale"}:
                alerts.append(
                    OperationAlert(
                        id=f"freshness-{source.sourceId}",
                        category="freshness_stale"
                        if source.freshnessStatus == "stale"
                        else "freshness_unknown",
                        severity="warning",
                        basis="advisory",
                        sourceId=source.sourceId,
                        diagnostic="acquisition_stale"
                        if source.freshnessStatus == "stale"
                        else "freshness_policy_or_timestamp_unavailable",
                    )
                )
        alerts.sort(key=lambda item: item.id)
        return AlertsPayload(
            items=tuple(alerts[offset : offset + limit]),
            total=len(alerts),
            offset=offset,
            limit=limit,
        )
