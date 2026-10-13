"""Deterministic read-only exporter for the Tris Market Lens API snapshot."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from donghak_stock_vision.api.models import (
    BacktestFingerprint,
    BacktestReadinessStep,
    BacktestRule,
    BacktestRuleGroup,
    BacktestTimelineItem,
    BaselineBacktestSnapshot,
    CoverageSummary,
    EvidenceDetail,
    EvidenceMetric,
    ModelDetail,
    ProjectStatus,
    ResearchCheckpoint,
    StockDetail,
    SummaryMetric,
)
from donghak_stock_vision.api.snapshot import ResearchAPISnapshot
from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy, baseline_rules
from donghak_stock_vision.data.learning import digest


class SnapshotExportError(ValueError):
    """Raised when publication inputs cannot be reconciled without guessing."""


class PublicationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    runnerTailIntegrated: bool
    sourceNotes: tuple[str, ...]
    models: tuple[ModelDetail, ...]


@dataclass(frozen=True)
class LoadedArtifact:
    artifact: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True)
class MarketSecurity:
    ticker: str
    start: str
    end: str
    rows: int


@dataclass(frozen=True)
class MarketSnapshot:
    total_rows: int
    first_date: str
    last_date: str
    securities: tuple[MarketSecurity, ...]
    sha256: str

    @property
    def universe(self) -> int:
        return len(self.securities)


@dataclass(frozen=True)
class ExportSources:
    dart_manifest: LoadedArtifact
    dart_mapping: LoadedArtifact
    dart_integrated: LoadedArtifact
    historical_mapping: LoadedArtifact
    missing_sessions: LoadedArtifact
    official_calendar: LoadedArtifact
    settlement_calendar: LoadedArtifact
    exact_boundaries: LoadedArtifact
    corporate_actions: LoadedArtifact
    security_lifecycle_manifest: LoadedArtifact
    security_lifecycle_reconciliation: LoadedArtifact
    security_lifecycle_unresolved: LoadedArtifact
    security_lifecycle_integrated: LoadedArtifact


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_artifact(path: Path) -> LoadedArtifact:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        artifact = raw["artifact"]
        fingerprint = raw["fingerprint"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise SnapshotExportError(f"invalid artifact: {path}") from error
    if not isinstance(artifact, dict) or not isinstance(fingerprint, str):
        raise SnapshotExportError(f"invalid artifact envelope: {path}")
    if digest(artifact) != fingerprint:
        raise SnapshotExportError(f"artifact fingerprint mismatch: {path}")
    return LoadedArtifact(cast(dict[str, Any], artifact), fingerprint)


def load_publication(path: Path) -> PublicationInput:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        publication = PublicationInput.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise SnapshotExportError("invalid research API publication input") from error
    if publication.version != "research_api_publication_v1":
        raise SnapshotExportError("unsupported research API publication version")
    ids = tuple(model.id for model in publication.models)
    if ids != ("up", "down"):
        raise SnapshotExportError("publication must contain ordered up/down model records")
    return publication


def read_market_snapshot(path: Path) -> MarketSnapshot:
    if not path.is_file():
        raise SnapshotExportError("market database missing")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise SnapshotExportError("active market database sidecar")

    before = _file_sha256(path)
    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"]:
                raise SnapshotExportError("market database integrity failure")
            summary = db.execute(
                "SELECT count(*),min(trading_date),max(trading_date) FROM bars"
            ).fetchone()
            if summary is None or not summary[0] or summary[1] is None or summary[2] is None:
                raise SnapshotExportError("market database contains no bars")
            metadata = {
                tuple(row)
                for row in db.execute(
                    "SELECT DISTINCT "
                    "json_extract(payload,'$.provider'),"
                    "json_extract(payload,'$.market'),"
                    "json_extract(payload,'$.adjustment') "
                    "FROM bars"
                )
            }
            if metadata != {("krx", "KOSPI", "unadjusted")}:
                raise SnapshotExportError("market database metadata scope mismatch")
            securities = tuple(
                MarketSecurity(str(ticker), str(start), str(end), int(rows))
                for ticker, start, end, rows in db.execute(
                    "SELECT ticker,min(trading_date),max(trading_date),count(*) "
                    "FROM bars GROUP BY ticker ORDER BY ticker"
                )
            )
    except sqlite3.Error as error:
        raise SnapshotExportError("market database read failed") from error

    after = _file_sha256(path)
    if before != after:
        raise SnapshotExportError("market database changed during export")
    return MarketSnapshot(
        total_rows=int(summary[0]),
        first_date=str(summary[1]),
        last_date=str(summary[2]),
        securities=securities,
        sha256=before,
    )


def load_sources(root: Path) -> ExportSources:
    dart = root / "docs/evidence/baseline-dart/artifacts"
    official = root / "docs/evidence/baseline-official/artifacts"
    lifecycle = root / "docs/evidence/baseline-security-lifecycle/artifacts"
    return ExportSources(
        dart_manifest=load_artifact(dart / "dart-evidence-manifest.json"),
        dart_mapping=load_artifact(dart / "dart-corporation-mapping.json"),
        dart_integrated=load_artifact(dart / "integrated-official-evidence.json"),
        historical_mapping=load_artifact(dart / "historical-security-mapping.json"),
        missing_sessions=load_artifact(dart / "missing-session-explanations.json"),
        official_calendar=load_artifact(official / "official-calendar.json"),
        settlement_calendar=load_artifact(official / "settlement-calendar.json"),
        exact_boundaries=load_artifact(official / "exact-boundaries.json"),
        corporate_actions=load_artifact(official / "corporate-actions.json"),
        security_lifecycle_manifest=load_artifact(lifecycle / "security-lifecycle-manifest.json"),
        security_lifecycle_reconciliation=load_artifact(
            lifecycle / "missing-session-reconciliation.json"
        ),
        security_lifecycle_unresolved=load_artifact(lifecycle / "unresolved-security-gaps.json"),
        security_lifecycle_integrated=load_artifact(
            lifecycle / "integrated-security-evidence.json"
        ),
    )


def _mapping_by_ticker(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    securities = mapping.get("securities")
    if not isinstance(securities, list):
        raise SnapshotExportError("DART mapping securities missing")
    result: dict[str, dict[str, Any]] = {}
    for value in securities:
        if not isinstance(value, dict) or not isinstance(value.get("ticker"), str):
            raise SnapshotExportError("invalid DART mapping record")
        ticker = value["ticker"]
        if ticker in result:
            raise SnapshotExportError("duplicate DART mapping ticker")
        result[ticker] = cast(dict[str, Any], value)
    return result


def _english_name(record: dict[str, Any]) -> str | None:
    if record.get("current_status") != "CURRENT_IDENTITY_VERIFIED":
        return None
    matches = record.get("matches")
    if not isinstance(matches, list):
        raise SnapshotExportError("verified current identity has no match list")
    exact = [
        match
        for match in matches
        if isinstance(match, dict) and match.get("stock_code") == record.get("ticker")
    ]
    if len(exact) != 1:
        raise SnapshotExportError("verified current identity is not uniquely mapped")
    name = exact[0].get("corp_eng_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _stock_evidence_note(record: dict[str, Any], unexplained_count: int) -> str:
    if unexplained_count:
        return (
            f"Security lifecycle evidence remains unresolved for "
            f"{unexplained_count:,} required sessions."
        )
    if record.get("current_status") == "CURRENT_IDENTITY_VERIFIED":
        return (
            "No unresolved required-session lifecycle gap remains in the reviewed "
            "baseline window; current corporation mapping is verified, while "
            "full-period historical identity continuity remains unverified."
        )
    return (
        "No unresolved required-session lifecycle gap remains in the reviewed "
        "baseline window; current corporation mapping and full-period historical "
        "identity continuity remain unresolved."
    )


def _unexplained_by_ticker(unresolved: dict[str, Any]) -> dict[str, int]:
    securities = unresolved.get("securities")
    if not isinstance(securities, list):
        raise SnapshotExportError("lifecycle unresolved-security inventory missing")
    result: dict[str, int] = {}
    for value in securities:
        if not isinstance(value, dict):
            raise SnapshotExportError("invalid lifecycle unresolved-security record")
        ticker = value.get("ticker")
        dates = value.get("dates")
        if not isinstance(ticker, str) or not isinstance(dates, list):
            raise SnapshotExportError("invalid lifecycle unresolved-security ticker record")
        if ticker in result:
            raise SnapshotExportError("duplicate lifecycle unresolved-security ticker")
        result[ticker] = len(dates)
    return result


def _project_status(
    coverage: CoverageSummary, evidence: tuple[EvidenceDetail, ...]
) -> ProjectStatus:
    return ProjectStatus(
        productName="Tris Market Lens",
        sourceLabel="Reviewed DonghakStockVision snapshot",
        metrics=(
            SummaryMetric(
                label="Universe",
                value=str(coverage.universe),
                detail="Approved KOSPI securities",
            ),
            SummaryMetric(
                label="Market data",
                value=f"{coverage.totalBars:,}",
                detail="Persisted historical daily bars",
            ),
            SummaryMetric(
                label="Qualified model",
                value="HGB",
                detail="Reviewed research inference artifacts",
            ),
            SummaryMetric(
                label="OOS evaluation",
                value="Complete",
                detail="Frozen one-shot classification evaluation",
            ),
        ),
        evidence=tuple(
            ResearchCheckpoint(label=item.title, state=item.state, detail=item.summary)
            for item in evidence
        ),
        baseline=(
            ResearchCheckpoint(
                label="Run input freeze",
                state="blocked",
                detail="Waiting for remaining evidence and runner-tail blockers",
            ),
            ResearchCheckpoint(
                label="Historical backtest",
                state="not_started",
                detail="No locked historical baseline has been executed",
            ),
        ),
    )


def _evidence(
    market: MarketSnapshot,
    sources: ExportSources,
) -> tuple[EvidenceDetail, ...]:
    manifest = sources.dart_manifest.artifact
    missing = sources.missing_sessions.artifact
    calendar = sources.official_calendar.artifact
    settlement = sources.settlement_calendar.artifact
    historical = sources.historical_mapping.artifact
    corporate = sources.corporate_actions.artifact
    lifecycle_manifest = sources.security_lifecycle_manifest.artifact
    lifecycle_reconciliation = sources.security_lifecycle_reconciliation.artifact
    lifecycle_unresolved = sources.security_lifecycle_unresolved.artifact
    lifecycle_integrated = sources.security_lifecycle_integrated.artifact

    current = int(manifest["current_identity_count"])
    historical_count = int(manifest["historical_identity_count"])
    complete_corporate = manifest["complete_corporate_coverage_count"]
    if type(complete_corporate) is not int or not 0 <= complete_corporate <= market.universe:
        raise SnapshotExportError("invalid corporate-action coverage count")
    if complete_corporate > historical_count:
        raise SnapshotExportError("corporate-action coverage exceeds historical identity coverage")
    parent_unexplained = int(manifest["unresolved_missing_sessions"])
    parent_explained = int(manifest["explained_missing_sessions"])
    newly_explained = int(lifecycle_reconciliation["explained"])
    unexplained = int(lifecycle_reconciliation["unexplained"])
    unresolved_securities = cast(list[str], lifecycle_reconciliation["unresolved_securities"])
    unresolved_records = cast(list[dict[str, Any]], lifecycle_unresolved["securities"])
    unresolved_security_count = len(unresolved_records)
    days = cast(dict[str, str], calendar["days"])
    sessions = sum(state == "session" for state in days.values())
    settlement_mapping = cast(
        dict[str, str],
        settlement["verification"]["details"]["mapping"],
    )

    parent_unresolved_security_count = sum(
        bool(value.get("unexplained"))
        for value in cast(list[dict[str, Any]], missing["securities"])
    )
    if (
        int(missing["unresolved"]) != parent_unexplained
        or int(missing["explained"]) != parent_explained
    ):
        raise SnapshotExportError("DART evidence manifest disagrees with missing-session artifact")
    if lifecycle_manifest.get("parent_gap_fingerprint") != sources.missing_sessions.fingerprint:
        raise SnapshotExportError("security lifecycle parent gap fingerprint mismatch")
    if (
        lifecycle_manifest.get("parent_integrated_fingerprint")
        != sources.dart_integrated.fingerprint
    ):
        raise SnapshotExportError("security lifecycle parent integrated fingerprint mismatch")
    if lifecycle_manifest.get("parent_mapping_fingerprint") != sources.dart_mapping.fingerprint:
        raise SnapshotExportError("security lifecycle parent mapping fingerprint mismatch")
    if int(lifecycle_manifest["starting_unexplained_sessions"]) != parent_unexplained:
        raise SnapshotExportError("security lifecycle starting session count mismatch")
    if (
        int(lifecycle_manifest["starting_unresolved_securities"])
        != parent_unresolved_security_count
    ):
        raise SnapshotExportError("security lifecycle starting security count mismatch")
    if int(lifecycle_manifest["newly_explained"]) != newly_explained:
        raise SnapshotExportError("security lifecycle explained count mismatch")
    if int(lifecycle_manifest["unexplained"]) != unexplained:
        raise SnapshotExportError("security lifecycle unexplained count mismatch")
    if int(lifecycle_reconciliation["total"]) != parent_unexplained:
        raise SnapshotExportError("security lifecycle reconciliation total mismatch")
    if newly_explained + unexplained != parent_unexplained:
        raise SnapshotExportError("security lifecycle reconciliation accounting mismatch")
    artifact_fingerprints = cast(dict[str, str], lifecycle_manifest["artifact_fingerprints"])
    if (
        artifact_fingerprints.get("missing-session-reconciliation")
        != sources.security_lifecycle_reconciliation.fingerprint
    ):
        raise SnapshotExportError("security lifecycle reconciliation fingerprint mismatch")
    if (
        artifact_fingerprints.get("unresolved-security-gaps")
        != sources.security_lifecycle_unresolved.fingerprint
    ):
        raise SnapshotExportError("security lifecycle unresolved-gap fingerprint mismatch")
    if (
        lifecycle_integrated.get("manifest_fingerprint")
        != sources.security_lifecycle_manifest.fingerprint
    ):
        raise SnapshotExportError("integrated security lifecycle manifest fingerprint mismatch")
    if (
        lifecycle_integrated.get("parent_integrated_fingerprint")
        != sources.dart_integrated.fingerprint
    ):
        raise SnapshotExportError("integrated security lifecycle parent fingerprint mismatch")
    if unresolved_securities != sorted(
        record["ticker"] for record in unresolved_records if isinstance(record.get("ticker"), str)
    ):
        raise SnapshotExportError("security lifecycle unresolved inventory mismatch")
    if lifecycle_integrated.get("security_calendar") == "VERIFIED":
        if unexplained or unresolved_security_count:
            raise SnapshotExportError("verified security lifecycle still has unresolved gaps")
    else:
        if not unexplained:
            raise SnapshotExportError("blocked security lifecycle has no unresolved gaps")
    if lifecycle_integrated.get("corporate_action") != "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE":
        raise SnapshotExportError("unexpected corporate-action lifecycle status")
    if complete_corporate == market.universe:
        raise SnapshotExportError(
            "corporate-action coverage contradicts blocked lifecycle evidence"
        )
    if int(historical["full_period_verified"]) != historical_count:
        raise SnapshotExportError("historical identity count mismatch")
    if current > market.universe or complete_corporate > market.universe:
        raise SnapshotExportError("evidence count exceeds market universe")

    return (
        EvidenceDetail(
            id="calendar",
            title="Exchange calendar",
            state="verified",
            summary=(
                "The official market-wide session calendar is verified for the baseline "
                "window; per-security absences remain a separate lifecycle concern."
            ),
            sourceLabel="KIND / KRX",
            sourceScope="Official exchange trading calendar",
            metrics=(
                EvidenceMetric(
                    label="Coverage",
                    value=f"{calendar['effective_start']} → {calendar['effective_end']}",
                    detail="Official calendar evidence window",
                ),
                EvidenceMetric(
                    label="Trading sessions",
                    value=str(sessions),
                    detail="Official exchange sessions in the preserved calendar",
                ),
                EvidenceMetric(
                    label="Calendar days",
                    value=str(len(days)),
                    detail="Classified dates in the official calendar artifact",
                ),
            ),
            findings=(
                "The market-wide date union was verified against the official exchange calendar.",
                "Research exclusions remain distinct from exchange holidays.",
                "Calendar verification does not infer security lifecycle state from missing bars.",
            ),
            blocker=None,
            fingerprint=sources.official_calendar.fingerprint,
        ),
        EvidenceDetail(
            id="settlement",
            title="Settlement",
            state="verified",
            summary="Official T+2 settlement evidence is mapped for possible baseline trade dates.",
            sourceLabel="KRX",
            sourceScope="Official cash-equity settlement rule and verified session mapping",
            metrics=(
                EvidenceMetric(label="Convention", value="T+2", detail="KOSPI cash-equity rule"),
                EvidenceMetric(
                    label="Mapped sessions",
                    value=str(len(settlement_mapping)),
                    detail="Possible execution dates with verified settlement dates",
                ),
                EvidenceMetric(
                    label="Final settlement",
                    value=settlement_mapping[max(settlement_mapping)],
                    detail="Settlement date for the last mapped trade session",
                ),
            ),
            findings=(
                "Settlement uses eligible exchange sessions rather than calendar-day arithmetic.",
                "The final execution-tail session settles within the preserved settlement tail.",
            ),
            blocker=None,
            fingerprint=sources.settlement_calendar.fingerprint,
        ),
        EvidenceDetail(
            id="dart",
            title="DART evidence",
            state="verified",
            summary=(
                "The official DART acquisition package is reproducible and secret-safe; "
                "current corporation mapping is not historical identity continuity."
            ),
            sourceLabel="Open DART / KIND",
            sourceScope="Official corporation mapping and disclosure evidence",
            metrics=(
                EvidenceMetric(
                    label="Current mapping",
                    value=f"{current} / {market.universe}",
                    detail="Exact current stock-code mappings",
                ),
                EvidenceMetric(
                    label="Current unresolved",
                    value=str(market.universe - current),
                    detail="Securities without verified current corporation mapping",
                ),
                EvidenceMetric(
                    label="Historical identity",
                    value=f"{historical_count} / {market.universe}",
                    detail="Full required-period identity continuity",
                ),
            ),
            findings=(
                "DART credential access was verified without persisting API secrets.",
                "Current corporation mapping is not promoted to historical identity evidence.",
            ),
            blocker=None,
            fingerprint=sources.dart_manifest.fingerprint,
        ),
        EvidenceDetail(
            id="security-lifecycle",
            title="Security lifecycle",
            state="in_progress" if unexplained else "verified",
            summary=(
                "The inherited security-level missing-session inventory is reconciled "
                "against preserved official lifecycle evidence without promoting boundary "
                "identity to full-period historical continuity."
            ),
            sourceLabel="KIND / KRX / DART",
            sourceScope="Official security lifecycle evidence and effective-date intervals",
            metrics=(
                EvidenceMetric(
                    label="Unresolved securities",
                    value=str(unresolved_security_count),
                    detail="Securities still carrying unexplained required-session gaps",
                ),
                EvidenceMetric(
                    label="Unexplained sessions",
                    value=f"{unexplained:,}",
                    detail="Ticker-sessions still requiring authoritative lifecycle evidence",
                ),
                EvidenceMetric(
                    label="Newly explained sessions",
                    value=f"{newly_explained:,}",
                    detail="Previously unexplained sessions reconciled by this lifecycle layer",
                ),
            ),
            findings=(
                f"{newly_explained:,} inherited unexplained ticker-sessions are reconciled "
                "to preserved official lifecycle evidence.",
                "Missing bars are never treated as proof of suspension or delisting.",
                "Full-period historical identity continuity and complete corporate-action "
                "coverage remain separate unresolved claims.",
            ),
            blocker=(
                f"{unexplained:,} ticker-sessions across {unresolved_security_count} securities "
                "still require official lifecycle reconciliation."
                if unexplained
                else None
            ),
            fingerprint=sources.security_lifecycle_integrated.fingerprint,
        ),
        EvidenceDetail(
            id="corporate-actions",
            title="Corporate actions",
            state="blocked" if complete_corporate < market.universe else "verified",
            summary=(
                "Complete historical corporate-action and identity coverage is not yet "
                "established for the approved universe."
            ),
            sourceLabel="DART / KIND / KRX",
            sourceScope="Official corporate actions, corrections, effective dates, and identity",
            metrics=(
                EvidenceMetric(
                    label="Complete coverage",
                    value=f"{complete_corporate} / {market.universe}",
                    detail="Full-period corporate-action coverage verified",
                ),
                EvidenceMetric(
                    label="Historical identity",
                    value=f"{historical_count} / {market.universe}",
                    detail="Full required-period identity continuity verified",
                ),
                EvidenceMetric(
                    label="Official schedule rows",
                    value=str(corporate["official_schedule_rows"]),
                    detail="Preserved official KIND schedule rows across returned markets",
                ),
            ),
            findings=(
                "Candidate disclosures are not treated as confirmed economic events.",
                "Decision dates and effective dates remain distinct.",
                "No price adjustment is authorized by the publication layer.",
            ),
            blocker=(
                "Historical identity, event-class, correction-chain, and effective-date "
                "coverage remain incomplete."
                if complete_corporate < market.universe
                else None
            ),
            fingerprint=sources.corporate_actions.fingerprint,
        ),
    )


def _baseline(
    publication: PublicationInput,
    sources: ExportSources,
    evidence: tuple[EvidenceDetail, ...],
) -> BaselineBacktestSnapshot:
    rules = baseline_rules()
    costs = BaselineCostPolicy(Decimal("1"), "down_per_component_KRW")
    policy = BaselineBacktestPolicy(costs)
    boundaries = sources.exact_boundaries.artifact
    anchors = cast(dict[str, str], boundaries["signal_anchors"])
    execution_tail = cast(list[str], boundaries["execution_tail"])
    settlement_tail = cast(list[str], boundaries["settlement_tail"])
    by_id = {item.id: item for item in evidence}
    lifecycle_blocked = by_id["security-lifecycle"].state != "verified"
    corporate_blocked = by_id["corporate-actions"].state != "verified"
    runner_blocked = not publication.runnerTailIntegrated
    allocation_percent = Decimal(rules["target_notional_fraction"]) * 100

    readiness = (
        BacktestReadinessStep(
            label="Baseline policy",
            state="verified",
            detail="Signal, execution, sizing, liquidity, cost, and exit rules are locked.",
        ),
        BacktestReadinessStep(
            label="Market calendar",
            state="verified",
            detail="Official market-wide calendar evidence is verified for the run window.",
        ),
        BacktestReadinessStep(
            label="Settlement",
            state="verified",
            detail="Official T+2 settlement mapping is verified for possible execution sessions.",
        ),
        BacktestReadinessStep(
            label="Security lifecycle",
            state="in_progress" if lifecycle_blocked else "verified",
            detail=by_id["security-lifecycle"].blocker or "Lifecycle evidence is reconciled.",
        ),
        BacktestReadinessStep(
            label="Corporate actions",
            state="blocked" if corporate_blocked else "verified",
            detail=by_id["corporate-actions"].blocker or "Corporate-action coverage is complete.",
        ),
        BacktestReadinessStep(
            label="Runner tail integration",
            state="blocked" if runner_blocked else "verified",
            detail=(
                "Execution-only tail sessions must resolve pending entries and exits "
                "without generating new signals."
                if runner_blocked
                else "Execution-only tail behavior is integrated."
            ),
        ),
        BacktestReadinessStep(
            label="Run input freeze",
            state="blocked"
            if lifecycle_blocked or corporate_blocked or runner_blocked
            else "verified",
            detail=(
                "The immutable baseline input manifest cannot be frozen yet."
                if lifecycle_blocked or corporate_blocked or runner_blocked
                else "All pre-freeze gates are satisfied."
            ),
        ),
        BacktestReadinessStep(
            label="Historical execution",
            state="not_started",
            detail="No locked historical baseline run has been executed.",
        ),
    )

    return BaselineBacktestSnapshot(
        id="baseline",
        title="Locked historical baseline",
        state="blocked",
        summary=(
            "The baseline policy is locked, but execution remains blocked until all "
            "pre-freeze evidence and runner-tail gates are verified."
        ),
        readiness=readiness,
        timeline=(
            BacktestTimelineItem(
                label="Signal anchor",
                value=f"{anchors['start']} → {anchors['end']}",
                detail="Approved historical simulation signal range; not an untouched final test.",
            ),
            BacktestTimelineItem(
                label="Execution-only tail",
                value=f"{execution_tail[0]} → {execution_tail[-1]}",
                detail=(
                    "No new signals; only pending entries, open positions, "
                    + "and exits may resolve."
                ),
            ),
            BacktestTimelineItem(
                label="Settlement tail",
                value=f"{settlement_tail[0]} → {settlement_tail[-1]}",
                detail="Allows final execution-tail activity to settle under T+2.",
            ),
        ),
        policyGroups=(
            BacktestRuleGroup(
                title="Signal and position",
                rules=(
                    BacktestRule(
                        label="Executable model",
                        value="Up HGB-7",
                        detail="Only the qualified Up direction is executable.",
                    ),
                    BacktestRule(
                        label="Signal threshold",
                        value=f"Score ≥ {rules['signal_threshold']}",
                        detail="Preapproved raw Up model threshold.",
                    ),
                    BacktestRule(
                        label="Position side",
                        value="Long only",
                        detail="No shorts, leverage, fractional shares, or pyramiding.",
                    ),
                    BacktestRule(
                        label="Position limit",
                        value="1 per ticker",
                        detail="At most one open or pending position per security.",
                    ),
                ),
            ),
            BacktestRuleGroup(
                title="Entry and sizing",
                rules=(
                    BacktestRule(
                        label="Entry timing",
                        value="Next observed T+1 open",
                        detail="Same-session entry is prohibited; failed entries are not retried.",
                    ),
                    BacktestRule(
                        label="Initial capital",
                        value=f"₩{int(rules['initial_capital_krw']):,}",
                        detail="Locked starting capital for the historical baseline.",
                    ),
                    BacktestRule(
                        label="Target allocation",
                        value=f"{allocation_percent:g}% current equity",
                        detail="Quantity is planned at signal time.",
                    ),
                    BacktestRule(
                        label="Concurrent positions",
                        value=f"{rules['maximum_concurrent_positions']} max",
                        detail="Candidates rank by raw score descending, then ticker ascending.",
                    ),
                ),
            ),
            BacktestRuleGroup(
                title="Liquidity and reservation",
                rules=(
                    BacktestRule(
                        label="Zero volume",
                        value="Reject",
                        detail="A session with zero volume cannot fill.",
                    ),
                    BacktestRule(
                        label="Volume cap",
                        value="1%",
                        detail="Maximum fill is one percent of observed session volume.",
                    ),
                    BacktestRule(
                        label="Reservation",
                        value="Immutable planned quantity",
                        detail="Signal-time notional and commission are reserved.",
                    ),
                    BacktestRule(
                        label="Actual cost guard",
                        value="No resize on T+1",
                        detail="Reservation overrun rejects the planned entry.",
                    ),
                ),
            ),
            BacktestRuleGroup(
                title="Exit policy",
                rules=(
                    BacktestRule(
                        label="Barrier",
                        value="max(2%, 2 × volatility_10)",
                        detail="Target and stop distances are anchored to entry price.",
                    ),
                    BacktestRule(
                        label="Holding limit",
                        value=f"{rules['maximum_holding_sessions']} observed sessions",
                        detail="Positions exit by target, stop, or holding limit.",
                    ),
                    BacktestRule(
                        label="Same-bar conflict",
                        value="Stop first",
                        detail="The pessimistic stop wins when both barriers cross.",
                    ),
                ),
            ),
            BacktestRuleGroup(
                title="Costs",
                rules=(
                    BacktestRule(
                        label="Slippage",
                        value="10 bps / side",
                        detail="Adverse proportional slippage is applied to buys and sells.",
                    ),
                    BacktestRule(
                        label="Commission",
                        value="1.5 bps / side",
                        detail="Commission is rounded up to whole KRW.",
                    ),
                    BacktestRule(
                        label="Tax and levy",
                        value="Settlement-date aware",
                        detail="The locked bounded KOSPI transfer-rate schedule is used.",
                    ),
                ),
            ),
        ),
        fingerprints=(
            BacktestFingerprint(label="Baseline policy", value=policy.fingerprint),
            BacktestFingerprint(
                label="Official calendar",
                value=sources.official_calendar.fingerprint,
            ),
            BacktestFingerprint(
                label="Settlement evidence",
                value=sources.settlement_calendar.fingerprint,
            ),
            BacktestFingerprint(label="DART evidence", value=sources.dart_manifest.fingerprint),
            BacktestFingerprint(
                label="Security lifecycle",
                value=sources.security_lifecycle_integrated.fingerprint,
            ),
        ),
        performanceAvailable=False,
    )


def build_snapshot(
    *,
    market_db: Path,
    root: Path,
    publication_path: Path,
) -> ResearchAPISnapshot:
    market = read_market_snapshot(market_db)
    sources = load_sources(root)
    publication = load_publication(publication_path)
    mapping = _mapping_by_ticker(sources.dart_mapping.artifact)
    market_tickers = {security.ticker for security in market.securities}
    if market_tickers != set(mapping):
        raise SnapshotExportError("market database universe differs from DART mapping universe")

    manifest = sources.dart_manifest.artifact
    verified_current_count = sum(
        1
        for record in mapping.values()
        if record.get("current_status") == "CURRENT_IDENTITY_VERIFIED"
    )
    if int(manifest["current_identity_count"]) != verified_current_count:
        raise SnapshotExportError("current identity count mismatch")

    unexplained = _unexplained_by_ticker(sources.security_lifecycle_unresolved.artifact)
    evidence = _evidence(market, sources)
    lifecycle_reconciliation = sources.security_lifecycle_reconciliation.artifact
    coverage = CoverageSummary(
        universe=market.universe,
        totalBars=market.total_rows,
        startDate=market.first_date,
        endDate=market.last_date,
        currentIdentityVerified=int(manifest["current_identity_count"]),
        historicalIdentityVerified=int(manifest["historical_identity_count"]),
        unresolvedSecurities=len(unexplained),
        unexplainedTickerSessions=int(lifecycle_reconciliation["unexplained"]),
    )

    stocks = tuple(
        StockDetail(
            ticker=security.ticker,
            name=_english_name(mapping[security.ticker]),
            market="KOSPI",
            sector=None,
            dataStatus="api",
            availableFrom=security.start,
            latestDataDate=security.end,
            evidenceNote=_stock_evidence_note(
                mapping[security.ticker],
                unexplained.get(security.ticker, 0),
            ),
            chartNote=(
                "Coverage dates come from persisted KRX daily bars. "
                "The API does not fabricate price observations."
            ),
        )
        for security in market.securities
    )

    return ResearchAPISnapshot(
        projectStatus=_project_status(coverage, evidence),
        coverage=coverage,
        models=publication.models,
        evidence=evidence,
        baselineBacktest=_baseline(publication, sources, evidence),
        stocks=stocks,
    )


def snapshot_bytes(snapshot: ResearchAPISnapshot) -> bytes:
    value = snapshot.model_dump(mode="json")
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def export_snapshot(
    *,
    market_db: Path,
    root: Path,
    publication_path: Path,
    output: Path,
) -> str:
    if output.exists():
        raise SnapshotExportError("output already exists")
    snapshot = build_snapshot(
        market_db=market_db,
        root=root,
        publication_path=publication_path,
    )
    content = snapshot_bytes(snapshot)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return hashlib.sha256(content).hexdigest()
