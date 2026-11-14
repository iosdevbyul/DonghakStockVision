"""Separate delayed-input research contract; no v1 model or policy mutation."""

import os
from collections import Counter
from contextlib import closing
from datetime import datetime, time
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import (
    FEATURES,
    Direction,
    Minimums,
    Sample,
    digest,
)
from donghak_stock_vision.data.next_day_research import CONTRACT as CONTRACT
from donghak_stock_vision.data.next_day_research import REQUEST as REQUEST
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.linear import score, validate_parameters
from donghak_stock_vision.models.metrics import baseline_scores, report
from donghak_stock_vision.models.service import TrainingService, provenance
from donghak_stock_vision.signals.dataset import (
    context,
    label_event,
    population,
    requirements,
    split_dataset,
)
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.real_market_dataset import prepare_dataset


def build(snapshot: dict[str, Any]) -> tuple[list[Sample], dict[str, int]]:
    """Historical diagnostic labels: skip one observed session, never use its values."""
    require(digest(snapshot["next_day_contract"]) == digest(CONTRACT), "next_day_contract_mismatch")
    samples: list[Sample] = []
    excluded: Counter[str] = Counter()
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        for i, bar in enumerate(bars):
            try:
                values = feature_vector(bars[: i + 1], snapshot)
            except ValueError as error:
                excluded[str(error)] += 1
                continue
            anchor = datetime.combine(bar.trading_date, time(16), SEOUL)
            if i + 1 >= len(bars):
                excluded["missing_skipped_session"] += 1
                continue
            # Keep the original input-date anchor for chronological splitting.
            # Only the label-selection anchor advances over the skipped session.
            label_anchor = datetime.combine(bars[i + 1].trading_date, time(16), SEOUL)
            label, start, end, available, status = label_event(
                bars, i, label_anchor, values, snapshot
            )
            missing = snapshot["unavailable_dates"].get(ticker, [])
            if end is not None and any(
                bar.trading_date.isoformat() < d <= end.isoformat() for d in missing
            ):
                label, status = None, "missing_or_protected_event_segment"
            if status != "labeled":
                excluded[status] += 1
            samples.append(
                Sample(
                    ticker,
                    bar.trading_date,
                    anchor,
                    values,
                    context(values),
                    label,
                    start,
                    end,
                    available,
                    status,
                    digest([b.to_dict() for b in bars[max(0, i - 11) : i + 1]]),
                    tuple(
                        b.trading_date
                        for b in bars
                        if start is not None and end is not None and start <= b.trading_date <= end
                    ),
                )
            )
    return sorted(samples, key=lambda s: (s.anchor_at, s.ticker)), dict(sorted(excluded.items()))


def prepare(d: dict[str, Any]) -> tuple[FrozenJSON, FrozenJSON]:
    require(
        set(d) == {"version", "dataset_version", "base_request", "contract"}
        and d["version"] == REQUEST
        and digest(d["contract"]) == digest(CONTRACT),
        "unsupported_next_day_request",
    )
    require(
        type(d["dataset_version"]) is str and 0 < len(d["dataset_version"]) <= 100,
        "next_day_dataset_version_required",
    )
    frozen, base = prepare_dataset(d["base_request"])
    snapshot, manifest = frozen.to_dict(), base.to_dict()
    snapshot["next_day_contract"] = CONTRACT
    samples, exclusions = build(snapshot)
    fit = ExplicitResearchSplit.from_dict(manifest["source"]["request"]["fit_split"])
    split = split_dataset(samples, "historical_research", fit)
    directions: tuple[Direction, ...] = ("up", "down")
    checks = {side: requirements(samples, split, side, Minimums()) for side in directions}
    manifest.update(
        version="next_day_dataset_manifest_v2",
        dataset_version=d["dataset_version"],
        base_manifest_pin=base.identifier,
        base_dataset_version=base.to_dict()["dataset_version"],
        contract=CONTRACT,
        snapshot_pin=digest(snapshot),
        input_request_pin=digest(d),
        feature_samples=len(samples),
        exclusions=exclusions,
        directions=checks,
        can_train=any(not c["failures"] for c in checks.values()),
        sample_pin=digest([s.to_dict() for s in samples]),
        split_pin=digest(split),
        historical_replay_only=True,
        official_final_test=False,
        availability_at_historical_anchor_verified=False,
        gap_policy="skip_one_observed_session_no_imputation_unknown_cause_for_absence",
    )
    return FrozenJSON.freeze(snapshot), FrozenJSON.freeze(manifest)


def _dataset(
    snapshot_id: str, snapshot: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    samples, exclusions = build(snapshot)
    fit = ExplicitResearchSplit.from_dict(manifest["source"]["request"]["fit_split"])
    return dict(
        snapshot_id=snapshot_id,
        samples=[s.to_dict() for s in samples],
        exclusions=exclusions,
        split=split_dataset(samples, "historical_research", fit),
    )


def train(d: dict[str, Any], output: Path) -> FrozenJSON:
    require(
        output.is_absolute() and output.resolve() == output and not output.exists(),
        "new_training_output_required",
    )
    require(
        not output.is_relative_to(Path(d["base_request"]["operational"]["root"])),
        "dataset_output_inside_operational_storage",
    )
    snapshot, manifest = prepare(d)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(AnalysisStore(output)) as store:
        dataset = _dataset(snapshot.identifier, snapshot.to_dict(), manifest.to_dict())
        service = TrainingService(store)
        directions: tuple[Direction, ...] = ("up", "down")
        entries = {side: service._direction(dataset, side, Minimums()) for side in directions}
        artifact = dict(
            version="next_day_model_v2",
            contract=CONTRACT,
            snapshot_id=snapshot.identifier,
            manifest_id=manifest.identifier,
            dataset_id=digest(dataset),
            features=list(FEATURES),
            directions=entries,
            provenance=provenance(),
            usage_restriction="research_only",
            operational_status="unregistered",
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
            data_origin=snapshot.to_dict()["data_origin"],
        )
        fitted = any(e["parameters"] is not None for e in entries.values())
        records = [
            ("snapshot", snapshot.to_dict()),
            ("real_market_dataset", manifest.to_dict()),
            ("next_day_dataset", dataset),
        ]
        if fitted:
            records.append(("next_day_model", artifact))
        store.put_many(records)
        version = digest(artifact) if fitted else None
        evaluation = evaluate(store, version) if version else None
        result = dict(
            version="next_day_training_v2",
            snapshot_id=snapshot.identifier,
            manifest_id=manifest.identifier,
            training=dict(
                status="trained" if fitted else "insufficient_data", model_version=version
            ),
            directions=entries,
            evaluation=evaluation,
            model_validation_completed=fitted,
            diagnostic_test_only=True,
            performance_approved=False,
            pit_qualified=False,
            execution_authorized=False,
            network_requests=0,
        )
        store.put("next_day_training", result)
    return FrozenJSON.freeze(result)


def load(store: AnalysisStore, version: str) -> dict[str, Any]:
    model = store.get("next_day_model", version)
    require(
        model["version"] == "next_day_model_v2"
        and digest(model["contract"]) == digest(CONTRACT)
        and model["features"] == list(FEATURES)
        and model["operational_status"] == "unregistered"
        and model["usage_restriction"] == "research_only"
        and all(
            model[k] is False
            for k in ("pit_qualified", "performance_approved", "execution_authorized")
        ),
        "next_day_model_contract",
    )
    snapshot = store.get("snapshot", model["snapshot_id"])
    manifest = store.get("real_market_dataset", model["manifest_id"])
    require(
        manifest["version"] == "next_day_dataset_manifest_v2"
        and digest(manifest["contract"]) == digest(CONTRACT)
        and manifest["snapshot_pin"] == model["snapshot_id"]
        and manifest["source_pin"] == digest(snapshot["real_market_source"]),
        "next_day_model_source_binding",
    )
    source = snapshot["real_market_source"]
    request = source["request"]
    access = ExplicitResearchSplit.from_dict(request["access_split"])
    fit = ExplicitResearchSplit.from_dict(request["fit_split"])
    require(
        source["version"] == "real_market_dataset_source_v1"
        and source["local_originals_verified"] is True
        and source["protected_final_test_read"] is False
        and source["request_pin"] == digest(request)
        and source["selected_rows_pin"] == digest(snapshot["rows"])
        and fit.training == access.training
        and fit.validation == access.validation
        and fit.final_test.start > access.final_test.end
        and model["data_origin"] == snapshot["data_origin"]
        and 0 < len(snapshot["rows"]) <= 25
        and sum(len(rows) for rows in snapshot["rows"].values()) <= 20000
        and all(
            not access.final_test.start.isoformat()
            <= row["trading_date"]
            <= access.final_test.end.isoformat()
            for rows in snapshot["rows"].values()
            for row in rows
        ),
        "next_day_original_source_or_split_binding",
    )
    rebuilt = _dataset(model["snapshot_id"], snapshot, manifest)
    require(
        rebuilt == store.get("next_day_dataset", model["dataset_id"]),
        "next_day_dataset_reproduction",
    )
    require(
        manifest["sample_pin"] == digest(rebuilt["samples"])
        and manifest["split_pin"] == digest(rebuilt["split"]),
        "next_day_manifest_dataset_binding",
    )
    for entry in model["directions"].values():
        if entry["parameters"] is not None:
            validate_parameters(entry["parameters"])
    return model


def evaluate(store: AnalysisStore, version: str) -> dict[str, Any]:
    model = load(store, version)
    dataset = store.get("next_day_dataset", model["dataset_id"])
    result: dict[str, Any] = {}
    directions: tuple[Direction, ...] = ("up", "down")
    for side in directions:
        entry = model["directions"][side]
        if entry["parameters"] is None:
            result[side] = dict(status="insufficient_data", requirements=entry["requirements"])
            continue
        parts = {}
        for part in ("validation", "test"):
            rows = population(dataset["split"], side, part)
            metrics = report(rows, [score(entry["parameters"], s.values)[0] for s in rows])
            require(metrics == entry[part], "next_day_evaluation_reproduction")
            parts[part] = dict(
                metrics=metrics,
                baselines={
                    name: report(rows, scores)
                    for name, scores in baseline_scores(rows, side, entry["prior"]).items()
                },
                by_ticker={
                    ticker: report(
                        [s for s in rows if s.ticker == ticker],
                        [
                            score(entry["parameters"], s.values)[0]
                            for s in rows
                            if s.ticker == ticker
                        ],
                    )
                    for ticker in sorted({s.ticker for s in rows})
                },
                by_year={
                    str(year): report(
                        [s for s in rows if s.trading_date.year == year],
                        [
                            score(entry["parameters"], s.values)[0]
                            for s in rows
                            if s.trading_date.year == year
                        ],
                    )
                    for year in sorted({s.trading_date.year for s in rows})
                },
            )
        result[side] = parts
    return dict(
        version="next_day_evaluation_v2",
        model_version=version,
        directions=result,
        diagnostic_only=True,
        official_final_test=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )
