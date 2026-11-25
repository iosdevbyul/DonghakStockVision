"""Read-only R1 audit of pinned development and post-Q3 prediction evidence."""

import json
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.evaluation_evidence import calendar_evidence
from donghak_stock_vision.models.research_v2 import contract, direction_label
from donghak_stock_vision.models.stock_generalization import regime
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB
from donghak_stock_vision.storage.evaluation_evidence import output_paths, readout_artifacts
from donghak_stock_vision.storage.operational_market import DB as OP_DB
from donghak_stock_vision.storage.operational_market import OperationalMarketStore
from donghak_stock_vision.storage.real_market_dataset import _protocol
from donghak_stock_vision.storage.research_studies import _file_sha256
from donghak_stock_vision.storage.selection_review import REVIEW_DB, retained_review_plan


def _prices(
    root: Path, sources: dict[str, Any], window: dict[str, str]
) -> tuple[dict[tuple[str, str], int], list[str], list[str]]:
    proofs = next(iter(sources.values()))["operational_proofs"]
    require(
        all(s["operational_proofs"] == proofs for s in sources.values())
        and 0 < len(proofs) <= 100
        and len({p["day"] for p in proofs}) == len(proofs)
        and all(window["start"] <= p["day"] <= window["end"] for p in proofs),
        "prediction_audit_batch_scope",
    )
    tickers = sorted({t for s in sources.values() for t in s["tickers"]})
    require(0 < len(tickers) <= 50, "prediction_audit_ticker_bound")
    days = sorted(p["day"] for p in proofs)
    if not (root / OP_DB).exists():
        return {}, days, days
    prices = {}
    missing = []
    with closing(OperationalMarketStore(root)) as op:
        op.connection.execute("BEGIN")
        for proof in proofs:
            day = proof["day"]
            row = op.connection.execute(
                "SELECT b.raw_id, b.fingerprint, r.source_date "
                "FROM ingestion_batches b JOIN raw_pages r ON r.id=b.raw_id "
                "WHERE b.batch_id=?",
                (proof["batch_id"],),
            ).fetchone()
            if row is None:
                missing.append(day)
                continue
            require(row[1:] == (proof["batch_pin"], day), "prediction_audit_batch_identity")
            manifest = op._verify_batch(proof["batch_id"], row[0])
            request = manifest.to_dict()["request"]
            require(
                manifest.identifier == proof["batch_pin"]
                and request["source_date"] == day
                and request.get("source_capture", {}).get("origin")
                == "real_provider_https_local_capture"
                and (
                    "source_capture" not in proof
                    or request["source_capture"] == proof["source_capture"]
                ),
                "prediction_audit_batch_binding",
            )
            observations = op.connection.execute(
                "SELECT o.payload FROM market_observations o JOIN observation_sources s "
                "USING(market,trading_date,ticker) WHERE s.raw_id=? AND o.trading_date=? "
                "AND o.ticker IN (" + ",".join("?" for _ in tickers) + ")",
                (row[0], day, *tickers),
            )
            for (payload,) in observations:
                bar = DailyBar.from_dict(FrozenJSON(payload).to_dict())
                key = (bar.ticker, day)
                require(key not in prices, "prediction_audit_duplicate_price")
                prices[key] = bar.close
    return prices, days, sorted(missing)


def _evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return calendar_evidence(
        [
            dict(
                ticker=r["ticker"],
                day=r["input_date"],
                end=r["target_date"],
                label=r["actual_label"],
            )
            for r in rows
        ]
    )


def _counts(m: dict[str, Any]) -> dict[str, Any]:
    tn, fp, fn, tp = (m["confusion_matrix"][k] for k in ("tn", "fp", "fn", "tp"))
    return dict(
        correct=tp + tn,
        incorrect=fp + fn,
        predicted_up=tp + fp,
        predicted_down=tn + fn,
        down_precision=tn / (tn + fn) if tn + fn else None,
        down_recall=tn / (tn + fp) if tn + fp else None,
    )


def _outcomes(
    case: dict[str, Any],
    source: dict[str, Any],
    prices: dict[tuple[str, str], int],
    *,
    score_key: str,
    experiment: dict[str, Any],
    threshold: float,
) -> list[dict[str, Any]]:
    h = case["horizon"]
    window = source["evaluation_window"]
    samples = {(s["ticker"], s["trading_date"]): s for s in source["goals"][str(h)]["samples"]}
    rows = []
    for original in case["rows"]:
        ticker, day, target = (original[k] for k in ("ticker", "day", "label_end"))
        raw = samples.get((ticker, day))
        require(raw is not None, "prediction_audit_missing_sample")
        assert raw is not None
        sample = Sample.from_dict(raw)
        reference = sample.anchor_at.astimezone(SEOUL).date().isoformat()
        require(
            len(sample.label_sessions) == h
            and window["start"]
            <= day
            < reference
            < sample.label_sessions[0].isoformat()
            <= target
            <= window["end"]
            and sample.label_sessions[-1].isoformat() == target
            and sample.label_status == "labeled"
            and raw["label"] == original["label"]
            and raw["label_end"] == target,
            "prediction_audit_outcome_binding",
        )
        base, actual = prices.get((ticker, reference)), prices.get((ticker, target))
        actual_label = original["label"]
        if base is not None and actual is not None:
            require(
                base > 0 and direction_label(base, actual) == actual_label,
                "prediction_audit_price_label_mismatch",
            )
        score = original[score_key]
        prediction = int(score >= threshold)
        rows.append(
            dict(
                experiment_id=experiment["experiment_id"],
                model_id=experiment["model_sha256"],
                evaluation_id=experiment["result_id"],
                sample_id=digest(raw),
                evidence_status="retained_retrospective_post_q3_readout",
                ticker=ticker,
                input_date=day,
                reference_date=reference,
                target_date=target,
                reference_price=base,
                actual_price=actual,
                actual_return=actual / base - 1
                if base is not None and actual is not None
                else None,
                actual_label=actual_label,
                actual_direction="up" if actual_label else "down",
                prediction_score=score,
                predicted_direction="up" if prediction else "down",
                outcome="correct" if prediction == actual_label else "incorrect",
                stock_regimes=regime(sample),
            )
        )
    require(
        len({(r["ticker"], r["input_date"]) for r in rows}) == len(rows),
        "prediction_audit_duplicate_sample",
    )
    return rows


def _groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        score_bin = min(9, int(r["prediction_score"] * 10))
        for key in (
            "ticker:" + r["ticker"],
            "input_month:" + r["input_date"][:7],
            "predicted:" + r["predicted_direction"],
            f"score_decile:{score_bin}",
            *("regime:" + name for name in r["stock_regimes"]),
        ):
            groups[key].append(r)
    summaries = {}
    for key, group in sorted(groups.items()):
        evidence = _evidence(group)
        m = metrics([r["actual_label"] for r in group], [r["prediction_score"] for r in group])
        summaries[key] = dict(
            metrics=m,
            **_counts(m),
            input_dates=len({r["input_date"] for r in group}),
            calendar_anchors=evidence["nonoverlapping_calendar_anchors"],
            status=evidence["status"],
        )
    return summaries


def _readout(
    plan: dict[str, Any],
    retained: dict[str, Any],
    prices: dict[tuple[str, str], int],
    days: list[str],
    missing_batches: list[str],
) -> list[dict[str, Any]]:
    views = []
    assessment = retained["assessment"]
    require(
        len(assessment["cases"]) == len(plan["cases"])
        and assessment["q3_diagnostic_reused"] is False
        and assessment["new_model_training"] == 0,
        "prediction_audit_assessment_scope",
    )
    by_case = {(c["study"], c["horizon"], c["stock_policy"]): c for c in plan["cases"]}
    for case in assessment["cases"]:
        h = case["horizon"]
        frozen = by_case.pop((case["study"], h, case["stock_policy"]))
        study = plan["studies"][case["study"]]
        source = retained["sources"][study["cohort"]]
        label_contract = contract(h)
        held = set(study["heldout_tickers"])
        expected_samples = {
            (s["ticker"], s["trading_date"])
            for s in source["goals"][str(h)]["samples"]
            if s["ticker"] in held
        }
        require(
            frozen["selection"] == case["selection"]
            and study["cohort"] == case["cohort"]
            and study["policy"] == case["policy"]
            and case["prospective_prediction"] is False
            and case["unseen_at_fit"] == (case["stock_policy"] == "heldout")
            and expected_samples == {(r["ticker"], r["day"]) for r in case["rows"]},
            "prediction_audit_case_binding",
        )
        alternatives = [
            ("original_group_selection", "old"),
            ("stable_selection", "new"),
        ]
        variants = {}
        for choice, score_key in alternatives:
            exp = case["selection"][choice]
            if exp is None:
                continue
            record = frozen["models"][exp]
            require(
                record["experiment_id"] == exp and record["config"]["goal"] == label_contract,
                "prediction_audit_model_binding",
            )
            rows = _outcomes(
                case,
                source,
                prices,
                score_key=score_key,
                experiment=record,
                threshold=label_contract["threshold"],
            )
            labels = [r["actual_label"] for r in rows]
            m = metrics(labels, [r["prediction_score"] for r in rows])
            baseline = metrics(labels, [frozen["training_prior"]] * len(rows))
            stored = "old" if score_key == "old" else "stable_or_prior"
            require(
                m == case["metrics"][stored] and baseline == case["metrics"]["training_prior"],
                "prediction_audit_metric_mismatch",
            )
            variants[choice] = dict(
                experiment_id=exp,
                model_id=record["model_sha256"],
                result_id=record["result_id"],
                model_created_at=record["finished_at"],
                candidate=record["config"]["candidate"],
                mask=record["config"]["mask"],
                metrics=m,
                **_counts(m),
                baselines=dict(training_prior=baseline),
                rows_with_verified_prices=sum(r["actual_return"] is not None for r in rows),
                rows=rows,
                breakdown=_groups(rows),
            )
        original_rows = variants["original_group_selection"]["rows"]
        candidates = {(t, d) for t in held for d in days}
        require(expected_samples <= candidates, "prediction_audit_candidate_scope")
        unscored = candidates - expected_samples
        missing = sum(day not in missing_batches and (t, day) not in prices for t, day in unscored)
        views.append(
            dict(
                cohort=case["cohort"],
                policy=case["policy"],
                horizon=h,
                stock_policy=case["stock_policy"],
                unseen_at_fit=case["unseen_at_fit"],
                evaluation_window=source["evaluation_window"],
                evidence_status="retained_retrospective_post_q3_readout",
                score_meaning="uncalibrated_positive_class_score_for_delayed_close_direction",
                contract=label_contract,
                cutoff=plan["request"]["assessment"]["cutoff"],
                original_report_selection=frozen["recorded_report_selection"],
                stability_selection=case["selection"]["state"],
                market_regime="unknown_stock_local_regimes_available",
                highest_search_ap_id=case["selection"]["highest_search_ap"],
                highest_search_ap_retained_metrics=case["metrics"]["highest_search_ap"],
                denominators=dict(
                    candidate_ticker_sessions=len(candidates),
                    evaluated=len(original_rows),
                    observed_input_dates=len({r["input_date"] for r in original_rows}),
                    missing_input_observations=missing,
                    excluded_or_unresolved_reason_unavailable=len(unscored) - missing,
                    cohort_scope_original_exclusions=source["goals"][str(h)]["excluded"],
                ),
                calendar=_evidence(original_rows),
                models=variants,
            )
        )
    require(not by_case, "prediction_audit_missing_case")
    return views


def _development(study: dict[str, Any], final_test: dict[str, str]) -> list[dict[str, Any]]:
    """Use only the pinned development projections, without loading Q3 diagnostics."""
    items = []
    root = Path(study["root"])
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        plan = store.get("autonomous_plan", study["plan_pin"])
        records = store.connection.execute(
            "SELECT payload FROM analysis_artifacts "
            "WHERE kind='autonomous_terminal' ORDER BY id LIMIT 13"
        ).fetchall()
        require(
            len(records) == 12 and plan["dataset_id"] == study["dataset_id"],
            "prediction_audit_study_binding",
        )
        for (payload,) in records:
            terminal = json.loads(payload)
            require(terminal["state"] == "completed", "prediction_audit_terminal_unavailable")
            exp, result = terminal["experiment_id"], terminal["result_id"]
            checkpoint = root / "models" / (exp + ".checkpoint")
            require(
                str(checkpoint) == terminal["checkpoint"]
                and checkpoint.resolve() == checkpoint
                and checkpoint.is_file()
                and checkpoint.stat().st_size <= 16 * 1024**2
                and _file_sha256(checkpoint) == terminal["model_sha256"],
                "prediction_audit_checkpoint_binding",
            )
            cfg = terminal["config"]
            h = cfg["goal"]["horizon"]
            require(
                cfg["goal"] == contract(h)
                and any(
                    r["experiment_id"] == exp and r["config"] == cfg for r in plan["configurations"]
                ),
                "prediction_audit_development_configuration",
            )
            folds = store.connection.execute(
                "SELECT json_extract(payload, ?) FROM analysis_artifacts "
                "WHERE kind='autonomous_dataset' AND id=?",
                (f'$.goals."{h}".folds', plan["dataset_id"]),
            ).fetchone()
            require(folds is not None, "prediction_audit_development_folds")
            descriptors = json.loads(folds[0])
            require(
                all(
                    f["training"]["end"]
                    < f["evaluation"]["start"]
                    <= f["evaluation"]["end"]
                    < "2026-07-01"
                    and (
                        f["evaluation"]["end"] < final_test["start"]
                        or f["evaluation"]["start"] > final_test["end"]
                    )
                    for f in descriptors
                ),
                "prediction_audit_development_date_scope",
            )
            # The study database is independently byte-pinned by the selection plan.
            retained = store.connection.execute(
                "SELECT json_extract(payload,'$.folds'), json_extract(payload,'$.experiment_id'), "
                "json_extract(payload,'$.model_sha256') FROM analysis_artifacts "
                "WHERE kind='autonomous_result' AND id=?",
                (result,),
            ).fetchone()
            require(
                retained is not None and retained[1:] == (exp, terminal["model_sha256"]),
                "prediction_audit_development_owner",
            )
            reports = []
            for f, evaluated in zip(descriptors, json.loads(retained[0]), strict=True):
                e = evaluated["evaluation"]
                m = e["metrics"]
                reports.append(
                    f
                    | dict(
                        metrics=m,
                        **_counts(m),
                        baselines=e["baselines"],
                        purged=evaluated["purged"],
                        counts=e["counts"],
                        nonoverlapping_counts=e["nonoverlapping_counts"],
                        calendar_anchors=len(e["nonoverlapping_calendar_dates"]),
                        original_prediction_digest=e["prediction_pin"],
                        by_ticker=e["by_ticker"],
                        by_quarter=e["by_quarter"],
                        seen_regimes=e["seen_robustness"]["input_regimes"],
                        stock_generalization=e["stock_generalization"],
                    )
                )
            items.append(
                dict(
                    cohort=study["cohort"],
                    policy=study["policy"],
                    horizon=h,
                    stock_policy=cfg["stock_policy"],
                    experiment_id=exp,
                    model_id=terminal["model_sha256"],
                    result_id=result,
                    parent_experiment=terminal.get("parent_experiment"),
                    finished_at=terminal["finished_at"],
                    selection_reason=terminal["selection_reason"],
                    candidate=cfg["candidate"],
                    mask=cfg["mask"],
                    folds=reports,
                    evidence_status="retained_development_fold_aggregates",
                    per_row_status="unavailable_fold_estimator_and_row_scores_not_retained",
                )
            )
    return sorted(items, key=lambda e: (e["finished_at"], e["experiment_id"]))


def audit(root: Path, pin: str, *, outputs: tuple[Path, Path]) -> FrozenJSON:
    plan = retained_review_plan(root, pin)
    assessment = plan["request"]["assessment"]
    op = Path(assessment["operational"]["root"])
    output_paths(
        *outputs,
        [
            root,
            Path(plan["pins_root"]),
            op,
            Path(assessment["research"]["database"]).parent,
            Path(assessment["protocol"]["path"]).parent,
            *[Path(s["root"]) for s in plan["studies"]],
            *[Path(s["plan_pin_file"]).parent for s in plan["studies"]],
        ],
    )
    review_db = root / REVIEW_DB
    before = _file_sha256(review_db)
    retained = readout_artifacts(root, pin, plan)
    _, split = _protocol(assessment)
    start = assessment["evaluation_window"]["start"]
    require(
        date.fromisoformat(start) >= date(2026, 10, 1)
        and date.fromisoformat(start) > split.final_test.end,
        "prediction_audit_protected_window",
    )
    # Verify only the originally pinned batches. The enclosing operational store can
    # have advanced since the frozen readout. The original whole-file PIN is retained.
    prices, days, missing_batches = _prices(
        op, retained["sources"], assessment["evaluation_window"]
    )
    readout = _readout(plan, retained, prices, days, missing_batches)
    development = [
        e for study in plan["studies"] for e in _development(study, split.final_test.to_dict())
    ]
    retained_review_plan(root, pin)
    require(_file_sha256(review_db) == before, "prediction_audit_readout_changed")
    return FrozenJSON.freeze(
        dict(
            version="prediction_performance_audit_v1",
            status="PARTIAL",
            readout=readout,
            development=development,
            retained_selection_comparisons=retained["assessment"]["comparisons"],
            readout_id=digest(retained["assessment"]),
            evaluated_development_records=len(development),
            unique_development_models=len({e["model_id"] for e in development}),
            retained_development_folds=sum(len(e["folds"]) for e in development),
            readout_model_cases=sum(len(c["models"]) for c in readout),
            unique_readout_models=len(
                {m["experiment_id"] for c in readout for m in c["models"].values()}
            ),
            verified_market_sessions=len(days) - len(missing_batches),
            unavailable_market_sessions=missing_batches,
            readout_prediction_records=sum(
                len(m["rows"]) for c in readout for m in c["models"].values()
            ),
            price_linked_prediction_records=sum(
                m["rows_with_verified_prices"] for c in readout for m in c["models"].values()
            ),
            model_inferences=0,
            historical_replays=0,
            new_training=0,
            network_requests=0,
            protected_final_test_read=False,
            q3_outcomes_read=False,
            pit_qualified=False,
            independent_future_performance_established=False,
            performance_approved=False,
            execution_authorized=False,
        )
    )
