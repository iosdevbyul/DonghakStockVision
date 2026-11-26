"""Bounded projections of the independently pinned historical study owners."""

import json
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models import research_v2
from donghak_stock_vision.models.stock_generalization import regime
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.real_market_training import _quiescent

RANGES = dict(
    train=dict(start="2022-01-03", end="2023-06-30"),
    calibration=dict(start="2023-08-01", end="2023-12-28"),
    evaluation=dict(start="2025-09-01", end="2026-06-30"),
    evaluation_context=dict(start="2025-07-01", end="2026-06-30"),
)


def source_owner(study: dict[str, Any]) -> dict[str, Any]:
    root = Path(study["root"])
    _quiescent(root / DB, study["database_sha256"])
    pins = Path(study["plan_pin_file"]).parent
    with closing(ReadOnlyAnalysisStore(root / DB)) as reader:
        pin = read_pin(pins / "plan.pin")
        plan = reader.get("autonomous_plan", pin)
        row = reader.connection.execute(
            "SELECT json_extract(payload,'$.split'),json_extract(payload,'$.historical.windows'), "
            "json_extract(payload,'$.historical.evaluations'), "
            "json_extract(payload,'$.historical.diagnostic.start'), "
            "json_extract(payload,'$.goals.\"5\".contract'),"
            "json_extract(payload,'$.protected_final_test_read') "
            "FROM analysis_artifacts WHERE kind='autonomous_dataset' AND id=?",
            (plan["dataset_id"],),
        ).fetchone()
        gaps = []
        for end, start in (
            (RANGES["train"]["end"], RANGES["calibration"]["start"]),
            (RANGES["evaluation_context"]["start"], RANGES["evaluation"]["start"]),
        ):
            (n,) = reader.connection.execute(
                """SELECT COUNT(DISTINCT json_extract(b.value,'$.trading_date'))
                FROM analysis_artifacts a,json_each(a.payload,'$.rows') t,json_each(t.value) b
                WHERE a.kind='autonomous_snapshot' AND a.id=?
                AND json_extract(b.value,'$.trading_date')>?
                AND json_extract(b.value,'$.trading_date')<?""",
                (plan["snapshot_id"], end, start),
            ).fetchone()
            gaps.append(n)
    require(row is not None, "feedback_source_missing")
    split = ExplicitResearchSplit.from_dict(json.loads(row[0]))
    train, calibration, evaluation, context = (
        RANGES[k] for k in ("train", "calibration", "evaluation", "evaluation_context")
    )
    original_windows = json.loads(row[1])
    original_eval = json.loads(row[2])
    require(
        plan["version"] == "autonomous_plan_v6"
        and pin == study["plan_pin"]
        and read_pin(pins / "dataset.pin") == study["dataset_id"] == plan["dataset_id"]
        and read_pin(pins / "snapshot.pin") == plan["snapshot_id"]
        and read_pin(pins / "source.pin") == plan["source_pin"]
        and json.loads(row[4]) == research_v2.contract(5)
        and row[5] == 0
        and original_windows[0] == split.training.to_dict()
        and split.training.start.isoformat()
        <= train["start"]
        <= train["end"]
        < calibration["start"]
        <= calibration["end"]
        <= split.training.end.isoformat()
        and split.final_test.end.isoformat() < context["start"] < evaluation["start"]
        and evaluation["end"] == context["end"] < row[3]
        and (context["start"] == original_windows[1]["start"])
        and (evaluation["start"], evaluation["end"])
        == (original_eval[0]["start"], original_eval[-1]["end"]),
        "feedback_source_scope",
    )
    require(min(gaps) >= 5, "feedback_horizon_embargo")
    return study | dict(
        snapshot_id=plan["snapshot_id"],
        source_pin=plan["source_pin"],
        original_final_test=split.final_test.to_dict(),
        embargo_observed_dates=gaps,
    )


def samples(study: dict[str, Any], split: str) -> list[Sample]:
    span = RANGES[split]
    held = set(study["heldout_tickers"])
    tickers = sorted(held if split == "evaluation" else set(study["tickers"]) - held)
    with closing(ReadOnlyAnalysisStore(Path(study["root"]) / DB)) as reader:
        records = reader.connection.execute(
            """SELECT s.value FROM analysis_artifacts a,
            json_each(a.payload,'$.goals."5".samples') s
            WHERE a.kind='autonomous_dataset' AND a.id=?
            AND json_extract(s.value,'$.trading_date')>=?
            AND json_extract(s.value,'$.label_end')<=?
            AND json_extract(s.value,'$.ticker') IN ("""
            + ",".join("?" for _ in tickers)
            + ") ORDER BY CAST(s.key AS INTEGER) LIMIT 20001",
            (study["dataset_id"], span["start"], span["end"], *tickers),
        ).fetchall()
    require(0 < len(records) <= 20000, "feedback_sample_bound")
    rows = [Sample.from_dict(json.loads(payload)) for (payload,) in records]
    require(
        len({(s.ticker, s.trading_date) for s in rows}) == len(rows)
        and all(
            s.label_status == "labeled"
            and s.label in (0, 1)
            and len(s.label_sessions) == 5
            and s.label_end is not None
            and s.label_sessions[-1] == s.label_end
            and span["start"] <= s.trading_date.isoformat() < s.label_end.isoformat() <= span["end"]
            and s.ticker in tickers
            for s in rows
        ),
        "feedback_sample_scope",
    )
    return rows


def _snapshot(study: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    path = Path(study["root"]) / DB
    span = RANGES["evaluation_context"]
    tickers = study["heldout_tickers"]
    with closing(ReadOnlyAnalysisStore(path)) as reader:
        metadata = reader.connection.execute(
            "SELECT json_extract(payload,'$.quality'), json_extract(payload,'$.session_basis'), "
            "json_extract(payload,'$.unavailable_dates'), "
            "json_extract(payload,'$.research_calendar') "
            "FROM analysis_artifacts "
            "WHERE kind='autonomous_snapshot' AND id=?",
            (study["snapshot_id"],),
        ).fetchone()
        require(metadata is not None, "feedback_snapshot_missing")
        market_days = reader.connection.execute(
            """SELECT DISTINCT json_extract(b.value,'$.trading_date') AS day
            FROM analysis_artifacts a,json_each(a.payload,'$.rows') t,json_each(t.value) b
            WHERE a.kind='autonomous_snapshot' AND a.id=? AND day BETWEEN ? AND ?
            ORDER BY day LIMIT 501""",
            (study["snapshot_id"], RANGES["evaluation"]["start"], span["end"]),
        ).fetchall()
        rows: dict[str, list[dict[str, Any]]] = {t: [] for t in tickers}
        for ticker, payload in reader.connection.execute(
            """SELECT t.key,b.value FROM analysis_artifacts a,json_each(a.payload,'$.rows') t,
            json_each(t.value) b WHERE a.kind='autonomous_snapshot' AND a.id=?
            AND t.key IN ("""
            + ",".join("?" for _ in tickers)
            + """)
            AND json_extract(b.value,'$.trading_date') BETWEEN ? AND ?
            ORDER BY t.key, CAST(b.key AS INTEGER) LIMIT 20001""",
            (study["snapshot_id"], *tickers, span["start"], span["end"]),
        ):
            rows[ticker].append(json.loads(payload))
    require(
        0 < len(market_days) <= 500
        and sum(map(len, rows.values())) <= 20000
        and metadata[1] == "observed_bars_unverified",
        "feedback_snapshot_population",
    )
    unavailable = json.loads(metadata[2])
    projected = dict(
        rows=rows,
        mode="historical_research",
        quality=json.loads(metadata[0]) if metadata[0] is not None else None,
        session_basis=metadata[1],
        unavailable_dates={
            t: [d for d in unavailable[t] if span["start"] <= d <= span["end"]] for t in tickers
        },
    )
    if metadata[3] is not None:
        projected["research_calendar"] = json.loads(metadata[3])
    return projected, [r[0] for r in market_days]


def outcomes(
    study: dict[str, Any],
) -> tuple[list[Sample], list[dict[str, Any]], list[dict[str, Any]]]:
    retained = samples(study, "evaluation")
    snapshot, days = _snapshot(study)
    excluded: dict[tuple[str, str], str] = {}
    rebuilt, returns, _ = research_v2.build(
        snapshot,
        5,
        feature_cache=research_v2.prepare_features(snapshot),
        label_start=date.fromisoformat(RANGES["evaluation"]["start"]),
        excluded_inputs=excluded,
    )
    require(
        [s.to_dict() for s in rebuilt] == [s.to_dict() for s in retained],
        "feedback_retained_population_mismatch",
    )
    prices = {
        (t, r["trading_date"]): r["close"] for t, bars in snapshot["rows"].items() for r in bars
    }
    output = []
    evaluated = set()
    for sample in retained:
        day = sample.trading_date.isoformat()
        ref = sample.anchor_at.astimezone(SEOUL).date().isoformat()
        target = sample.label_sessions[-1].isoformat()
        base, actual = prices[sample.ticker, ref], prices[sample.ticker, target]
        sample_id = digest(sample.to_dict())
        require(
            research_v2.direction_label(base, actual) == sample.label
            and returns[sample_id] == actual / base - 1,
            "feedback_price_binding",
        )
        output.append(
            dict(
                sample_id=sample_id,
                ticker=sample.ticker,
                input_date=day,
                reference_date=ref,
                target_date=target,
                reference_price=base,
                actual_price=actual,
                actual_return=returns[sample_id],
                label=sample.label,
                regimes=list(regime(sample)),
            )
        )
        evaluated.add((sample.ticker, day))
    ledger = []
    for day in days:
        for ticker in sorted(study["heldout_tickers"]):
            key = ticker, day
            if key in evaluated:
                continue
            reason = excluded.get(key)
            require(reason is not None or key not in prices, "feedback_unexplained_exclusion")
            status = {
                None: "missing",
                "research_neutral_cost_band": "neutral",
                "research_immature_label": "unresolved",
            }.get(reason, "excluded")
            ledger.append(
                dict(
                    ticker=ticker,
                    input_date=day,
                    status=status,
                    reason=reason or "missing_input",
                )
            )
    return retained, output, ledger
