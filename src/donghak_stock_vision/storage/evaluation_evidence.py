"""Read-only evidence availability and frozen, existing shadow-stream consumers."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.evaluation_evidence import (
    calendar_evidence,
    evidence_policy,
    shadow_rows,
)
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import context
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.operational_market import DB as OP_DB
from donghak_stock_vision.storage.real_market_dataset import _protocol
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.selection_review import REVIEW_DB, review_context
from donghak_stock_vision.storage.shadow_prediction import (
    _model,
    _request,
    evaluation_readiness,
    events,
    verify,
)


def _flags() -> dict[str, Any]:
    return dict(
        network_requests=0,
        new_training=0,
        independent_future_predictive_power_established=False,
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
        automatic_activation=False,
    )


def retained_readout(root: Path, pin: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # This reader never runs the frozen implementation or changes its outputs.
    # Writers still require exact implementation identity by default.
    plan = review_context(root, pin, code_check=False)
    with closing(ReadOnlyAnalysisStore(root / REVIEW_DB)) as store:
        assessment_pin = read_pin(Path(plan["pins_root"]) / "assessment.pin")
        assessment = store.get("selection_assessment", assessment_pin)
        require(
            FrozenJSON((root / "assessment.json").read_text()).identifier == assessment_pin
            and assessment["plan_pin"] == pin
            and assessment["policy_pin"] == plan["policy_pin"],
            "evidence_assessment_binding",
        )
        index = store.get(
            "selection_readout_index", read_pin(Path(plan["pins_root"]) / "readout.pin")
        )
        require(index["plan_pin"] == pin, "evidence_readout_owner")
        sources = {
            k: store.get("selection_assessment_source", v) for k, v in index["sources"].items()
        }
    for s in sources.values():
        require(
            s["evaluation_window"] == plan["request"]["assessment"]["evaluation_window"]
            and s["q3_labels_generated"] is False
            and s["protected_final_test_read"] is False,
            "evidence_readout_scope",
        )
    return plan, dict(assessment=assessment, sources=sources)


def _inventory(
    path: Path, table: str, pin: str, protected: dict[str, str], tickers: list[str]
) -> dict[str, Any]:
    require(table in ("bars", "market_observations"), "evidence_inventory_owner")
    _quiescent(path, pin)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        where = "trading_date NOT BETWEEN ? AND ?"
        args = (protected["start"], protected["end"])
        dates = conn.execute(
            f"SELECT trading_date,COUNT(*) FROM {table} WHERE {where} "
            "GROUP BY trading_date ORDER BY trading_date LIMIT 5001",
            args,
        ).fetchall()
        require(len(dates) <= 5000, "evidence_inventory_date_bound")
        count = conn.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM {table} WHERE {where}", args
        ).fetchone()[0]
        universe = {r[0] for r in dates}
        by_ticker = {}
        for ticker in tickers:
            ds = [
                r[0]
                for r in conn.execute(
                    f"SELECT trading_date FROM {table} WHERE {where} AND ticker=? "
                    "ORDER BY trading_date",
                    (*args, ticker),
                )
            ]
            by_ticker[ticker] = dict(
                rows=len(ds),
                first=ds[0] if ds else None,
                last=ds[-1] if ds else None,
                absent_from_observed_market_dates=sorted(universe - set(ds)),
                missing_cause="UNKNOWN_NOT_INFERRED_AS_SUSPENSION_OR_DELISTING",
            )
    _quiescent(path, pin)
    return dict(
        rows_excluding_final_test=sum(r[1] for r in dates),
        trading_dates_excluding_final_test=len(dates),
        tickers=count,
        first=dates[0][0] if dates else None,
        last=dates[-1][0] if dates else None,
        dates=[r[0] for r in dates],
        by_ticker=by_ticker,
        session_basis="observed_market_union_not_official_holiday_calendar",
        source_binding="original_quiescent_database_pin_metadata_only",
        raw_bodies_replayed=False,
        final_test_rows_queried=False,
    )


def _prior_metrics(cases: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    """Descriptive partitions of the existing readout, never a new evaluation."""
    result = []
    for case in cases:
        if case["horizon"] != horizon:
            continue
        rows = case["rows"]
        partitions = {
            **{
                f"ticker:{t}": [r for r in rows if r["ticker"] == t]
                for t in sorted({r["ticker"] for r in rows})
            },
            **{
                f"input_month:{m}": [r for r in rows if r["day"].startswith(m)]
                for m in sorted({r["day"][:7] for r in rows})
            },
        }
        result.append(
            dict(
                cohort=case["cohort"],
                policy=case["policy"],
                stock_policy=case["stock_policy"],
                selection_state=case["selection"]["state"],
                partitions={
                    key: dict(
                        evidence=calendar_evidence(
                            [
                                dict(
                                    ticker=r["ticker"],
                                    day=r["day"],
                                    end=r["label_end"],
                                    label=r["label"],
                                )
                                for r in group
                            ]
                        ),
                        old=metrics([r["label"] for r in group], [r["old"] for r in group]),
                        stable_or_prior=metrics(
                            [r["label"] for r in group], [r["new"] for r in group]
                        ),
                        classification="previously_exposed_descriptive_partition",
                    )
                    for key, group in partitions.items()
                },
            )
        )
    return result


def output_paths(report: Path, pin_file: Path, protected: list[Path]) -> None:
    require(
        all(p.is_absolute() and p.resolve() == p and not p.exists() for p in (report, pin_file))
        and not report.is_relative_to(pin_file.parent)
        and not pin_file.is_relative_to(report.parent)
        and all(not p.is_relative_to(r.resolve()) for p in (report, pin_file) for r in protected),
        "evidence_new_separate_private_outputs",
    )


def availability_audit(
    root: Path, pin: str, *, outputs: tuple[Path, Path] | None = None
) -> FrozenJSON:
    plan, retained = retained_readout(root, pin)
    spec = plan["request"]["assessment"]
    if outputs is not None:
        output_paths(
            *outputs,
            [
                root,
                Path(spec["research"]["database"]).parent,
                Path(spec["operational"]["root"]),
                *[Path(s["root"]) for s in plan["studies"]],
            ],
        )
    _, split = _protocol(spec)
    tickers = sorted({t for study in plan["studies"] for t in study["tickers"]})
    require(len(tickers) <= 50, "evidence_ticker_bound")
    protected = split.final_test.to_dict()
    inventories = dict(
        research=_inventory(
            Path(spec["research"]["database"]), "bars", spec["research"]["pin"], protected, tickers
        ),
        operational=_inventory(
            Path(spec["operational"]["root"]) / OP_DB,
            "market_observations",
            spec["operational"]["pin"],
            protected,
            tickers,
        ),
    )
    ledger = [
        dict(role="original_training", **split.training.to_dict()),
        dict(role="original_validation_exposed", **split.validation.to_dict()),
    ]
    datasets = set()
    for study in plan["studies"]:
        if study["dataset_id"] in datasets:
            continue
        datasets.add(study["dataset_id"])
        _, data = context(Path(study["root"]), study["plan_pin"], code_check=False)
        historical = data["historical"]
        # Entire input windows were already exposed, even where no label survived.
        ledger.extend(
            dict(role="prior_source_exposed_not_fresh", **w) for w in historical["windows"]
        )
        ledger.append(dict(role="q3_diagnostic_exposed", **historical["diagnostic"]))
    ledger.append(dict(role="selection_review_readout_exposed", **spec["evaluation_window"]))
    ledger.append(dict(role="feature_context_exposed", **spec["context_window"]))
    observed = sorted({day for inv in inventories.values() for day in inv["dates"]})
    unknown = [d for d in observed if not any(w["start"] <= d <= w["end"] for w in ledger)]
    labels = {}
    for h in (1, 5, 20):
        primary = []
        available = []
        excluded: dict[str, int] = {}
        for cohort, source in retained["sources"].items():
            study = next(s for s in plan["studies"] if s["cohort"] == cohort)
            g = source["goals"][str(h)]
            for name, n in g["excluded"].items():
                excluded[name] = excluded.get(name, 0) + n
            for sample in g["samples"]:
                require(
                    spec["evaluation_window"]["start"]
                    <= sample["trading_date"]
                    <= sample["label_end"]
                    <= spec["evaluation_window"]["end"]
                    and len(sample["label_sessions"]) == h,
                    "evidence_label_scope",
                )
                row = dict(
                    ticker=sample["ticker"],
                    day=sample["trading_date"],
                    end=sample["label_end"],
                    label=sample["label"],
                )
                available.append(row)
                if sample["ticker"] in study["heldout_tickers"]:
                    primary.append(row)
        labels[str(h)] = dict(
            prior_heldout_readout=calendar_evidence(primary),
            existing_labeled_population=calendar_evidence(available),
            original_exclusions=excluded,
            new_independent_rows=0,
            additional_eligible_periods=[],
            prospective_contract_status="EXISTING_5_OBSERVATION_SHADOW_SUPPORTED"
            if h == 5
            else "NO_EXISTING_SHADOW_CONTRACT_FOR_THIS_TARGET",
            prior_baseline_comparisons=[
                c for c in retained["assessment"]["comparisons"] if c["horizon"] == h
            ],
            prior_metrics_by_case=_prior_metrics(retained["assessment"]["cases"], h),
        )
    review_context(root, pin, code_check=False)
    return FrozenJSON.freeze(
        dict(
            version="evaluation_availability_audit_v1",
            original_review_pin=pin,
            inventories=inventories,
            exposure_ledger=ledger,
            horizons=labels,
            unclassified_dates_requiring_exposure_review=unknown,
            newly_qualified_independent_historical_dates=[],
            final_test=dict(window=protected, queried=False, eligible=False),
            status="INSUFFICIENT_EVIDENCE",
            reason="retained_readout_already_exposed_and_no_new_preregistered_interval",
            new_eligible_evaluation_rows=0,
            consistency_boundary="separate_quiescent_pinned_reads_not_cross_database_atomic_snapshot",
            **_flags(),
        )
    )


def _conditions(d: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    return dict(
        request_version=d["version"],
        mode=d["mode"],
        source_kind=d["source_kind"],
        tickers=d["tickers"],
        model=info,
        horizon_observations=5,
        threshold=0.5,
        feature_version="ohlcv_value_v1",
        evidence_policy=evidence_policy(),
    )


def freeze_shadow(
    d: dict[str, Any], journal: Path, head: str, at: datetime | None = None
) -> FrozenJSON:
    _request(d)
    require(d["mode"] == "research_forward_candidate", "evidence_forward_mode_required")
    verify(d, journal, head)
    _, info = _model(d)
    entries = events(journal, head)
    at = timestamp(at or datetime.now(UTC))
    require(
        timestamp(info["created_at"]) <= at
        and all(timestamp(e["recorded_at"]) <= at for e in entries),
        "evidence_registration_clock",
    )
    return FrozenJSON.freeze(
        dict(
            version="shadow_evidence_registration_v1",
            registered_at=at.isoformat(),
            journal=str(journal),
            baseline_head=head,
            baseline_event_count=len(entries),
            baseline_events_pin=digest(entries),
            conditions=_conditions(d, info),
            request_pin_at_registration=digest(d),
            minimum_calendar_anchors=20,
            **_flags(),
        )
    )


def shadow_readiness(
    registration: FrozenJSON,
    d: dict[str, Any],
    journal: Path,
    head: str,
    *,
    at: datetime | None = None,
) -> FrozenJSON:
    p = registration.to_dict()
    fields(
        p,
        "version registered_at journal baseline_head baseline_event_count baseline_events_pin "
        "conditions request_pin_at_registration minimum_calendar_anchors " + " ".join(_flags()),
    )
    require(
        p["version"] == "shadow_evidence_registration_v1"
        and p["minimum_calendar_anchors"] == 20
        and p["journal"] == str(journal),
        "evidence_registration_binding",
    )
    require(all(p[k] == v for k, v in _flags().items()), "evidence_approval_forbidden")
    at = timestamp(at or datetime.now(UTC))
    require(at >= timestamp(p["registered_at"]), "evidence_before_registration")
    proof = evaluation_readiness(d, journal, head, clock=lambda: at).to_dict()
    _, info = _model(d)
    require(_conditions(d, info) == p["conditions"], "evidence_frozen_conditions_changed")
    entries = events(journal, head)
    n = p["baseline_event_count"]
    require(
        type(n) is int
        and 0 <= n <= len(entries)
        and digest(entries[:n]) == p["baseline_events_pin"],
        "evidence_original_prefix_changed",
    )
    for e in entries[n:]:
        if e["kind"] == "prediction":
            payload = e["payload"]
            require(
                all(payload["model"][k] == v for k, v in info.items())
                and payload["ticker"] in d["tickers"],
                "evidence_prediction_model_changed",
            )
    rows, counts = shadow_rows(entries, p["registered_at"], n)
    result = calendar_evidence(rows)
    first = min(
        (e["payload"]["generated_at"] for e in entries if e["kind"] == "prediction"),
        default=p["registered_at"],
    )
    legacy_rows, legacy_counts = shadow_rows(entries, first, 0)
    require(events(journal, head) == entries, "evidence_journal_changed_during_read")
    return FrozenJSON.freeze(
        dict(
            version="shadow_evidence_readiness_v1",
            registration_pin=registration.identifier,
            journal_verified=True,
            source_and_model_replayed=True,
            horizons={
                "1": calendar_evidence([]) | dict(status="UNSUPPORTED_PROSPECTIVE_CONTRACT"),
                "5": result,
                "20": calendar_evidence([]) | dict(status="UNSUPPORTED_PROSPECTIVE_CONTRACT"),
            },
            existing_stream_evidence=calendar_evidence(legacy_rows),
            existing_stream_counts=legacy_counts,
            stream_counts=counts,
            retained_stream_pending=proof["pending"],
            new_eligible_evaluation_rows=len(rows),
            existing_readiness=proof,
            earliest_guaranteed_evaluation_at=None,
            accumulation_owner="existing_shadow_record_resolve_and_research_cycle",
            **_flags(),
        )
    )
