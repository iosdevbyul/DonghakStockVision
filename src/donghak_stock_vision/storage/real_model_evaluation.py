"""Frozen real-model evaluation/export; no training or protected source writes."""

import csv
import hashlib
import io
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.learning import canonical, digest, timestamp
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.snapshot import snapshot_bars
from donghak_stock_vision.models.diagnostics import diagnose
from donghak_stock_vision.models.service import EvaluationService, load_model, provenance
from donghak_stock_vision.signals.dataset import build_dataset
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import document, prepare_dataset
from donghak_stock_vision.storage.real_market_training import _quiescent, validate_real_daily_model

VERSION = "real_model_evaluation_request_v1"


def _verified(
    d: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    fields(
        d,
        "version dataset_request dataset_request_pin_file model_database model_database_pin "
        "model_version source_pins_root evaluated_at",
    )
    require(d["version"] == VERSION, "unsupported_real_model_evaluation_version")
    model_path = Path(d["model_database"])
    _quiescent(model_path, d["model_database_pin"])
    dataset_request = document(d["dataset_request"])
    require(
        digest(dataset_request) == read_pin(Path(d["dataset_request_pin_file"])),
        "evaluation_dataset_request_pin_mismatch",
    )
    with closing(ReadOnlyAnalysisStore(model_path)) as store:
        store.connection.execute("BEGIN")
        model = load_model(store, d["model_version"], "historical_research")
        validate_real_daily_model(store, model, None)
        require(
            store.created_at("model", d["model_version"])
            <= timestamp(d["evaluated_at"])
            <= datetime.now(UTC),
            "evaluation_before_model_creation",
        )
        snapshot = store.get("snapshot", model["snapshot_id"])
        source = snapshot.get("real_market_source", {})
        require(
            source.get("version") == "real_market_dataset_source_v1"
            and source["input_request_pin"] == digest(dataset_request),
            "evaluation_original_dataset_source_required",
        )
        require(
            model["training_policy"]["threshold"] == 0.5 and model["calibration"] == "none",
            "evaluation_frozen_score_contract",
        )
        # The original versioned source owner rejects protected windows before raw replay.
        rebuilt, manifest = prepare_dataset(dataset_request)
        require(
            rebuilt.identifier == model["snapshot_id"] and rebuilt.to_dict() == snapshot,
            "evaluation_snapshot_reconstruction_mismatch",
        )
        require(
            store.get("real_market_dataset", manifest.identifier) == manifest.to_dict(),
            "evaluation_manifest_reconstruction_mismatch",
        )
        for name, pin in (
            ("source", digest(source)),
            ("manifest", manifest.identifier),
            ("snapshot", rebuilt.identifier),
        ):
            require(
                read_pin(Path(d["source_pins_root"]) / (name + ".pin")) == pin,
                "evaluation_independent_source_pin_mismatch",
            )
        verified = EvaluationService(store).evaluate(
            d["model_version"], "historical_research", persist=False
        )
        dataset = store.get("dataset", model["dataset_id"])
        metadata = dict(
            dataset_version=manifest.to_dict()["dataset_version"],
            dataset_manifest_pin=manifest.identifier,
            snapshot_pin=rebuilt.identifier,
            source_pin=digest(source),
            model_identifier=d["model_version"],
            model_sha256=digest(model),
            model_hash_basis="canonical_AnalysisStore_model_JSON",
            parameters_pin=digest({k: v["parameters"] for k, v in model["directions"].items()}),
            dataset_pin=model["dataset_id"],
            evaluation_service_pin=verified["evaluation_id"],
            model_created_at=store.created_at("model", d["model_version"]).isoformat(),
        )
    _quiescent(model_path, d["model_database_pin"])
    return model, dataset, snapshot, metadata


def _trace(snapshot: dict[str, Any], dataset: dict[str, Any], model_created: str) -> dict[str, Any]:
    source = snapshot["real_market_source"]["request"]
    access = ExplicitResearchSplit.from_dict(source["access_split"])
    fit = ExplicitResearchSplit.from_dict(source["fit_split"])
    forbidden = access.final_test
    require(
        all(
            not forbidden.start.isoformat() <= row["trading_date"] <= forbidden.end.isoformat()
            for rows in snapshot["rows"].values()
            for row in rows
        ),
        "evaluation_protected_rows",
    )
    histories = {}
    for ticker in sorted(snapshot["rows"]):
        bars = snapshot_bars(snapshot, ticker)
        for i, bar in enumerate(bars):
            histories[ticker, bar.trading_date.isoformat()] = bars[max(0, i - 11) : i + 1]
    trace = {}
    for part, rows in dataset["split"]["partitions"].items():
        span = {"train": fit.training, "validation": fit.validation, "test": fit.final_test}[part]
        for sample in rows:
            anchor = date.fromisoformat(sample["trading_date"])
            history = histories[sample["ticker"], sample["trading_date"]]
            require(
                span.start <= anchor <= date.fromisoformat(sample["label_end"]) <= span.end
                and date.fromisoformat(sample["label_start"]) > anchor,
                "evaluation_label_boundary",
            )
            require(
                all(b.trading_date <= anchor for b in history)
                and digest([b.to_dict() for b in history]) == sample["input_hash"],
                "evaluation_causal_input_hash",
            )
            require(
                not any(
                    forbidden.start.isoformat() <= day <= forbidden.end.isoformat()
                    or day in source["known_missing_sessions"]
                    for day in sample["label_sessions"]
                ),
                "evaluation_forbidden_label_session",
            )
        trace["diagnostic" if part == "test" else part] = dict(
            samples=len(rows),
            input_collected_after_nominal_anchor=sum(
                any(
                    b.collected_at > timestamp(s["anchor_at"])
                    for b in histories[s["ticker"], s["trading_date"]]
                )
                for s in rows
            ),
            model_created_after_anchor=sum(
                timestamp(model_created) > timestamp(s["anchor_at"]) for s in rows
            ),
        )
    excluded = {}
    for ticker in snapshot["rows"]:
        selected = {**snapshot, "rows": {ticker: snapshot["rows"][ticker]}}
        samples, reasons = build_dataset(selected)
        excluded[ticker] = dict(
            observations=len(selected["rows"][ticker]),
            feature_samples=len(samples),
            exclusions=reasons,
        )
    return dict(
        partitions=trace,
        per_ticker_availability=excluded,
        final_test_observations_read=False,
        feature_input_hashes_verified=True,
        labels_within_original_partitions=True,
        known_missing_sessions=source["known_missing_sessions"],
        causal_features_do_not_establish_historical_availability=True,
        nominal_anchor_not_authenticated_publication=True,
        official_publication_time="UNKNOWN",
        corporate_actions="UNVERIFIED",
        historical_revisions="POSSIBLE",
        independent_publication_time_verified=False,
        pit_qualified=False,
        genuine_forward_prediction_evidence=False,
    )


def _csv(rows: list[dict[str, Any]]) -> str:
    columns = [
        "partition",
        "direction",
        "period_kind",
        "period",
        "ticker",
        "n",
        "dates",
        "positive",
        "negative",
        "event_positive_fraction",
        "accuracy",
        "balanced_accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc_ap",
        "tn",
        "fp",
        "fn",
        "tp",
        "prior_ap",
        "reversal_strength_ap",
        "training_majority_accuracy",
        "ap_minus_best_existing_baseline",
        "interpretation_limited",
        "undefined_reasons",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        m = row["metrics"]
        c = m["confusion_matrix"]
        writer.writerow(
            {
                **{k: row.get(k, "") for k in columns},
                **{
                    k: m.get(k)
                    for k in (
                        "n",
                        "positive",
                        "negative",
                        "accuracy",
                        "balanced_accuracy",
                        "precision",
                        "recall",
                        "f1",
                        "roc_auc",
                        "pr_auc_ap",
                    )
                },
                **c,
                "event_positive_fraction": m["prevalence"],
                "prior_ap": row["baselines"]["prior"]["pr_auc_ap"],
                "reversal_strength_ap": row["baselines"]["reversal_strength"]["pr_auc_ap"],
                "training_majority_accuracy": row["baselines"]["training_majority"]["accuracy"],
                "undefined_reasons": canonical(m["undefined_reasons"]),
            }
        )
    return stream.getvalue()


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Frozen real KRX model evaluation",
        "",
        (
            "Retrospective conditional reversal-barrier research; scores are not calibrated "
            "price probabilities."
        ),
        "Training and Validation roles are unchanged. The original Final Test stays sealed.",
        "",
        (
            "| Direction | Partition | N | Accuracy | Balanced accuracy | Precision | Recall "
            "| F1 | ROC-AUC | AP | AP minus best existing baseline |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for direction, entry in result["diagnostics"]["directions"].items():
        for part, bucket in entry["partitions"].items():
            m = bucket["metrics"]
            values = [direction, part, str(m["n"])] + [
                f"{m[k]:.6f}" if m[k] is not None else "undefined"
                for k in (
                    "accuracy",
                    "balanced_accuracy",
                    "precision",
                    "recall",
                    "f1",
                    "roc_auc",
                    "pr_auc_ap",
                )
            ]
            margin = bucket["ap_minus_best_existing_baseline"]
            values.append(f"{margin:.6f}" if margin is not None else "undefined")
            lines.append("| " + " | ".join(values) + " |")
    lines += [
        "",
        "## Per-ticker diagnostic",
        "",
        "| Direction | Ticker | N | Positive fraction | ROC-AUC | AP | AP margin | Recall |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    def number(value: Any) -> str:
        return f"{value:.6f}" if value is not None else "undefined"

    for direction, entry in result["diagnostics"]["directions"].items():
        for ticker, bucket in entry["by_ticker"].items():
            m = bucket["metrics"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        direction,
                        ticker,
                        str(m["n"]),
                        number(m["prevalence"]),
                        number(m["roc_auc"]),
                        number(m["pr_auc_ap"]),
                        number(bucket["ap_minus_best_existing_baseline"]),
                        number(m["recall"]),
                    ]
                )
                + " |"
            )
    lines += [
        "",
        "## Quarterly diagnostic",
        "",
        "| Direction | Quarter | N | Positive / negative | AP | AP margin | Limited sample |",
        "|---|---|---:|---|---:|---:|---|",
    ]
    for row in result["diagnostics"]["temporal"]:
        if row["period_kind"] == "quarter" and row["ticker"] == "ALL":
            m = row["metrics"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["direction"],
                        row["period"],
                        str(m["n"]),
                        f"{m['positive']} / {m['negative']}",
                        number(m["pr_auc_ap"]),
                        number(row["ap_minus_best_existing_baseline"]),
                        str(row["interpretation_limited"]),
                    ]
                )
                + " |"
            )
    lines += ["", "## Numerical findings", ""]
    for direction, entry in result["diagnostics"]["directions"].items():
        finding = entry["descriptive_comparison"]
        lines.append(
            f"- {direction}: Validation-to-diagnostic AP change "
            f"{number(finding['validation_to_diagnostic_ap_change'])}; diagnostic accuracy "
            f"minus fixed Training-majority accuracy "
            f"{number(finding['diagnostic_accuracy_minus_training_majority'])}. "
            f"{finding['tickers_above_best_existing_ap_baseline']} / "
            f"{finding['tickers_with_defined_ap']} tickers exceed both existing AP baselines. "
            f"Ticker AP margins: {finding['ticker_ap_margins']}."
        )
    lines += [
        "",
        "## Interpretation",
        "",
        (
            "See per-ticker and temporal CSVs for every core ticker, month and quarter, "
            "including empty/single-class buckets. Group by anchor date; labels can mature in"
            " the next month within the unchanged diagnostic partition."
        ),
        (
            "The majority baseline is fixed using Training. Prior/reversal_strength and the "
            "AP margin are the existing Phase 2 references. Their diagnostic comparison is "
            "descriptive, not a new approval gate."
        ),
        (
            "Training resubstitution, reused Validation, overlapping five-observation labels,"
            " small buckets, unverified corporate actions/calendar and feature-distribution "
            "shifts limit inference. Differences do not prove overfitting, regime change or a"
            " causal failure mechanism."
        ),
        (
            "No verified benchmark index series is bound to this dataset, so market regimes "
            "are not invented."
        ),
        (
            "Original inputs and the model were retained after many historical anchors. "
            "Source integrity and causal feature construction do not establish PIT or "
            "prospective prediction existence. Publication time and historical revisions "
            "remain unauthenticated."
        ),
        (
            "No refit, threshold change, calibration, original Final Test scoring, model "
            "registration, scheduled prediction, network request or protected-data write."
        ),
        "performance_approved=false; pit_qualified=false; execution_authorized=false.",
        "",
    ]
    return "\n".join(lines)


def compute(d: dict[str, Any], retained_code: dict[str, Any] | None = None) -> dict[str, str]:
    model, dataset, snapshot, identity = _verified(d)
    before = digest(model)
    first = diagnose(model, dataset, snapshot)
    second = diagnose(model, dataset, snapshot)
    require(
        FrozenJSON.freeze(first) == FrozenJSON.freeze(second) and digest(model) == before,
        "evaluation_reproducibility_or_parameter_failure",
    )
    code = provenance()
    if retained_code is not None:
        require(
            code["implementation_hash"] == retained_code["implementation_hash"]
            and code["versions"] == retained_code["versions"],
            "evaluation_code_or_runtime_mismatch",
        )
        code = retained_code  # Squash merge SHA may differ; implementation bytes must agree.
    result = dict(
        version="real_model_evaluation_summary_v1",
        identity=identity,
        evaluated_at=timestamp(d["evaluated_at"]).isoformat(),
        code=code,
        diagnostics=first,
        trace=_trace(snapshot, dataset, identity["model_created_at"]),
        parameters_unchanged=True,
        reproducibility_verified=True,
        performance_approved=False,
        performance_metrics=None,
        pit_qualified=False,
        execution_authorized=False,
        network_requests=0,
    )
    ticker_rows = [
        dict(partition="diagnostic", direction=direction, ticker=ticker, **bucket)
        for direction, entry in first["directions"].items()
        for ticker, bucket in entry["by_ticker"].items()
    ]
    matrices = {
        d: {p: b["metrics"]["confusion_matrix"] for p, b in entry["partitions"].items()}
        for d, entry in first["directions"].items()
    }
    files = {
        "evaluation-summary.json": canonical(result) + "\n",
        "per-ticker-metrics.csv": _csv(ticker_rows),
        "temporal-metrics.csv": _csv(
            [dict(partition="diagnostic", **row) for row in first["temporal"]]
        ),
        "confusion-matrices.json": canonical(matrices) + "\n",
        "evaluation-report.md": _markdown(result),
    }
    manifest = dict(
        version="real_model_evaluation_manifest_v1",
        request_pin=digest(d),
        identity=identity,
        model_database_pin=d["model_database_pin"],
        code=code,
        diagnostic_range=dataset["split"]["configuration"]["final_test"],
        original_split=snapshot["real_market_source"]["request"]["access_split"],
        parameters_unchanged=True,
        reproducibility_verified=True,
        final_test_observations_read=False,
        file_sha256={
            name: hashlib.sha256(body.encode()).hexdigest() for name, body in files.items()
        },
        baselines=first["baselines"],
        pit_qualified=False,
        performance_approved=False,
        network_requests=0,
    )
    files["evaluation-manifest.json"] = canonical(manifest) + "\n"
    _quiescent(Path(d["model_database"]), d["model_database_pin"])
    return files


def _same_files(output: Path, files: dict[str, str]) -> bool:
    for name, body in files.items():
        path = output / name
        expected = body.encode()
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_nlink != 1
            or path.stat().st_size != len(expected)
            or path.read_bytes() != expected
        ):
            return False
    return True


def export(d: dict[str, Any], output: Path, pins: Path, *, verify: bool = False) -> None:
    require(
        output.is_absolute()
        and output.resolve() == output
        and pins.is_absolute()
        and pins.resolve() == pins
        and not output.is_relative_to(pins)
        and not pins.is_relative_to(output),
        "evaluation_separate_canonical_output_required",
    )
    original = document(d["dataset_request"])
    protected_roots = (Path(d["model_database"]).parent, Path(original["operational"]["root"]))
    require(
        all(
            not output.is_relative_to(root) and not pins.is_relative_to(root)
            for root in protected_roots
        ),
        "evaluation_separate_source_output_required",
    )
    retained = document(str(output / "evaluation-manifest.json"), 8 * 1024**2) if verify else None
    if not verify:
        require(not output.exists() and not pins.exists(), "evaluation_new_output_required")
    files = compute(d, retained["code"] if retained else None)
    manifest = FrozenJSON(files["evaluation-manifest.json"])
    if verify:
        require(
            read_pin(pins / "manifest.pin") == manifest.identifier,
            "evaluation_retained_pin_mismatch",
        )
        require(
            _same_files(output, files),
            "evaluation_retained_output_mismatch",
        )
        return
    output.mkdir(mode=0o700, parents=True)
    pins.mkdir(mode=0o700, parents=True)
    for name, body in files.items():
        write_new(output / name, body.removesuffix("\n"))
    write_new(pins / "manifest.pin", manifest.identifier)
    require(
        _same_files(output, files),
        "evaluation_export_readback_failed",
    )
