"""Research-only model/event consumer of existing capture, journal and label owners."""

import re
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import CalendarMonthSource
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.forward_capture import FullMarketScope
from donghak_stock_vision.data.learning import FEATURES, Minimums, digest, timestamp
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.data.schema import SEOUL, DailyBar
from donghak_stock_vision.data.shadow_prediction import MODES, VERSION, identifier, validate_event
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.dataset import context, label_event
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.forward_signal_journal import (
    GENESIS,
    MAX_ENTRIES,
    append_shadow_event,
    history,
    recover_shadow_event,
)
from donghak_stock_vision.storage.krx_authorization import bounded
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig, _policy
from donghak_stock_vision.storage.real_market_dataset import document, verify_operational_window
from donghak_stock_vision.storage.real_market_training import _quiescent, validate_real_daily_model

Clock = Callable[[], datetime]


def now() -> datetime:
    return datetime.now(UTC)


def _calendar(d: dict[str, Any]) -> tuple[dict[str, str], str]:
    require(type(d["calendars"]) is list and 1 <= len(d["calendars"]) <= 3, "shadow_calendar_bound")
    days: dict[str, str] = {}
    pins = []
    for item in d["calendars"]:
        fields(item, "raw source normalized_pin")
        month = CalendarMonthSource(
            bounded(Path(item["raw"]), 1024**2),
            FrozenJSON.freeze(document(item["source"])),
            item["normalized_pin"],
        ).verify()
        require(not days.keys() & month.to_dict()["days"].keys(), "shadow_calendar_overlap")
        days.update(month.to_dict()["days"])
        pins.append(month.identifier)
    return days, digest(sorted(pins))


def _model(d: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ref = fields(d["model"], "database pin model_version source_pins_root")
    path = Path(ref["database"])
    _quiescent(path, ref["pin"])
    with closing(ReadOnlyAnalysisStore(path)) as store:
        store.connection.execute("BEGIN")
        model = load_model(store, ref["model_version"], "historical_research")
        validate_real_daily_model(store, model, None)
        require(
            model["operational_status"] == "unregistered"
            and store.connection.execute(
                "SELECT COUNT(*) FROM operational_registry WHERE model_version=?",
                (ref["model_version"],),
            ).fetchone()[0]
            == 0,
            "shadow_unregistered_research_model_required",
        )
        snapshot = store.get("snapshot", model["snapshot_id"])
        require(
            snapshot["real_market_source"]["version"] == "real_market_dataset_source_v1",
            "shadow_real_source_model_required",
        )
        pins = Path(ref["source_pins_root"])
        manifest = store.get("real_market_dataset", read_pin(pins / "manifest.pin"))
        require(
            read_pin(pins / "snapshot.pin") == model["snapshot_id"] == manifest["snapshot_pin"]
            and read_pin(pins / "source.pin")
            == digest(snapshot["real_market_source"])
            == manifest["source_pin"],
            "shadow_model_original_pins",
        )
        info = dict(
            identifier=ref["model_version"],
            artifact_sha256=digest(model),
            database_pin=ref["pin"],
            created_at=store.created_at("model", ref["model_version"]).isoformat(),
            snapshot_pin=model["snapshot_id"],
            dataset_version=manifest["dataset_version"],
            access_split=snapshot["real_market_source"]["request"]["access_split"],
            fit_split=snapshot["real_market_source"]["request"]["fit_split"],
        )
    _quiescent(path, ref["pin"])
    return model, info


def _request(d: dict[str, Any]) -> None:
    fields(d, "version mode source_kind target_trading_date tickers model operational calendars")
    require(
        d["version"] == "research_shadow_request_v1" and d["mode"] in MODES,
        "shadow_request_version_or_mode",
    )
    require(d["source_kind"] in ("local_provider_capture", "offline_fixture"), "shadow_source_kind")
    require(
        type(d["tickers"]) is list
        and 0 < len(d["tickers"]) <= 25
        and d["tickers"] == sorted(set(d["tickers"])),
        "shadow_ticker_bound",
    )
    require(
        date.fromisoformat(d["target_trading_date"]).isoformat() == d["target_trading_date"],
        "shadow_canonical_target",
    )


def _horizon(
    d: dict[str, Any], info: dict[str, Any], days: dict[str, str]
) -> tuple[list[str], list[str]]:
    target = d["target_trading_date"]
    require(days.get(target) == "session", "shadow_target_session_unverified")
    sessions = sorted(day for day, state in days.items() if state == "session")
    i = sessions.index(target)
    require(i >= 12 and len(sessions[i : i + 5]) == 5, "shadow_calendar_history_or_horizon_missing")
    history_days, future = sessions[i - 12 : i], sessions[i : i + 5]
    protected = ExplicitResearchSplit.from_dict(info["access_split"]).final_test
    require(
        all(
            not protected.start.isoformat() <= day <= protected.end.isoformat()
            for day in history_days + future
        ),
        "shadow_protected_final_test_forbidden",
    )
    return history_days, future


def _rows(
    d: dict[str, Any], start: str, end: str
) -> tuple[dict[str, list[DailyBar]], list[dict[str, Any]]]:
    bars, proofs = verify_operational_window(
        d["operational"],
        tuple(d["tickers"]),
        ResearchDateRange(date.fromisoformat(start), date.fromisoformat(end)),
        strict_forward_membership=False,
        allow_synthetic_fixture=d["source_kind"] == "offline_fixture",
    )
    return {
        t: sorted([b for b in bars if b.ticker == t], key=lambda b: b.trading_date)
        for t in d["tickers"]
    }, proofs


def _source(bars: list[DailyBar], proofs: list[dict[str, Any]]) -> dict[str, Any]:
    dates = {b.trading_date.isoformat() for b in bars}
    return dict(
        bars=[b.to_dict() for b in bars],
        batches=sorted(
            [p for p in proofs if p["day"] in dates], key=lambda p: (p["day"], p["batch_id"])
        ),
    )


def _event(kind: str, payload: dict[str, Any], at: datetime) -> FrozenJSON:
    event_id = (
        payload["prediction_id"]
        if kind == "prediction"
        else identifier(
            kind,
            {k: v for k, v in payload.items() if k not in ("checked_at", "result_available_at")},
        )
    )
    owner = FrozenJSON.freeze(
        dict(
            version=VERSION,
            kind=kind,
            event_id=event_id,
            recorded_at=timestamp(at).isoformat(),
            payload=payload,
            pit_qualified=False,
            performance_approved=False,
            genuine_forward_evidence=False,
            execution_authorized=False,
        )
    )
    validate_event(owner)
    return owner


def events(path: Path, head: str) -> list[dict[str, Any]]:
    if not path.exists():
        require(head == GENESIS, "shadow_missing_journal_with_non_genesis_head")
        return []
    rows = history(path, head, limit=MAX_ENTRIES).to_dict()["entries"]
    require(
        all(e["entry"]["version"] == "research_shadow_journal_entry_v1" for e in rows),
        "separate_shadow_journal_required",
    )
    result = [e["entry"]["event"] for e in rows]
    predictions = {e["event_id"] for e in result if e["kind"] == "prediction"}
    for e in result:
        if e["kind"] in ("outcome", "correction"):
            require(
                e["payload"]["prediction_id"] in predictions, "shadow_original_prediction_missing"
            )
    return result


def _persist(
    journal: Path,
    head: str,
    event: FrozenJSON,
    intents: Path,
    pins: Path,
    *,
    resume_at: datetime | None = None,
) -> FrozenJSON:
    intents.mkdir(mode=0o700, parents=True, exist_ok=True)
    pins.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(
        not intents.resolve().is_relative_to(journal.resolve())
        and not pins.resolve().is_relative_to(intents.resolve()),
        "shadow_separate_intent_pins",
    )
    name = event.to_dict()["event_id"]
    intent = intents / (name + ".json")
    pin = pins / (name + ".pin")
    if intent.exists():
        original = FrozenJSON(bounded(intent, 128 * 1024).decode())
        require(read_pin(pin) == original.identifier, "shadow_conflicting_intent")
        if original != event:
            prior, attempt = original.to_dict(), event.to_dict()
            ignored = {
                "generated_at",
                "inputs_verified_available_at",
                "feature_generated_at",
                "source_database_pin_at_recording",
            }
            require(
                prior["kind"] == attempt["kind"] == "prediction"
                and resume_at is not None
                and all(
                    prior["payload"][key] == value
                    for key, value in attempt["payload"].items()
                    if key not in ignored
                )
                and 0
                <= (timestamp(resume_at) - timestamp(prior["recorded_at"])).total_seconds()
                <= 300,
                "shadow_conflicting_or_stale_intent",
            )
            event = original
    else:
        write_new(intent, event.payload_json)
        write_new(pin, event.identifier)
    result = append_shadow_event(journal, event, head)
    retained = pins / ("head-" + result.to_dict()["head_pin"] + ".pin")
    if retained.exists():
        require(
            read_pin(retained) == result.to_dict()["head_pin"], "shadow_head_checkpoint_conflict"
        )
    else:
        write_new(retained, result.to_dict()["head_pin"])
    return result


def _paths(d: dict[str, Any], journal: Path, intents: Path, pins: Path) -> None:
    require(
        all(p.is_absolute() and p.resolve() == p for p in (journal, intents, pins)),
        "shadow_canonical_paths",
    )
    roots = (Path(d["model"]["database"]).parent, Path(d["operational"]["root"]))
    require(
        all(not path.is_relative_to(root) for path in (journal, intents, pins) for root in roots),
        "shadow_output_inside_protected_source",
    )
    require(
        not intents.is_relative_to(pins)
        and not pins.is_relative_to(intents)
        and journal.parent.stat().st_mode & 0o077 == 0,
        "shadow_private_separate_storage",
    )


def record(
    d: dict[str, Any], journal: Path, head: str, intents: Path, pins: Path, *, clock: Clock = now
) -> FrozenJSON:
    _request(d)
    _paths(d, journal, intents, pins)
    old = events(journal, head)
    model, info = _model(d)
    days, calendar_pin = _calendar(d)
    history_days, future = _horizon(d, info, days)
    histories, proofs = _rows(d, history_days[0], history_days[-1])
    verified_at = timestamp(clock())
    target = date.fromisoformat(d["target_trading_date"])
    results = []
    for ticker in d["tickers"]:
        slot = identifier(
            "slot",
            dict(
                mode=d["mode"],
                source_kind=d["source_kind"],
                ticker=ticker,
                target=d["target_trading_date"],
                model=info["identifier"],
                calendar_pin=calendar_pin,
            ),
        )
        prior = next(
            (e for e in old if e["kind"] == "prediction" and e["payload"]["slot_id"] == slot), None
        )
        try:
            bars = histories[ticker]
            require(
                [b.trading_date.isoformat() for b in bars] == history_days,
                "shadow_missing_required_input_session",
            )
            require(
                all(b.collected_at <= verified_at for b in bars),
                "shadow_input_collected_after_execution",
            )
            require(
                timestamp(info["created_at"]) <= verified_at, "shadow_model_created_after_execution"
            )
            if d["mode"] == "research_forward_candidate":
                # Preserve the model's original next-observation event alignment.
                require(target > verified_at.astimezone(SEOUL).date(), "shadow_target_not_future")
                fit = ExplicitResearchSplit.from_dict(info["fit_split"])
                require(
                    date.fromisoformat(history_days[-1]) > fit.final_test.end,
                    "shadow_model_development_overlap",
                )
            else:
                require(
                    target <= verified_at.astimezone(SEOUL).date(),
                    "shadow_replay_target_must_be_past",
                )
            values = feature_vector(
                bars,
                dict(quality=None, unavailable_dates={}, session_basis="observed_bars_unverified"),
            )
            direction = context(values)
            require(direction is not None, "shadow_no_directional_context")
            assert direction is not None
            parameters = model["directions"][direction]["parameters"]
            require(parameters is not None, "shadow_model_direction_unavailable")
            value = score(parameters, values)[0]
            feature_at = timestamp(clock())
            generated = timestamp(clock())
            require(
                verified_at <= feature_at <= generated
                and 0 <= (timestamp(clock()) - generated).total_seconds() <= 300,
                "shadow_execution_clock_order",
            )
            source = _source(bars, proofs)
            payload = dict(
                prediction_id=identifier("prediction", slot),
                slot_id=slot,
                ticker=ticker,
                mode=d["mode"],
                source_classification=d["source_kind"],
                event_rule="reversal_barrier_v1",
                horizon_observations=5,
                target_trading_date=d["target_trading_date"],
                outcome_sessions=future,
                outcome_not_before=datetime.combine(date.fromisoformat(future[-1]), time(16), SEOUL)
                .astimezone(UTC)
                .isoformat(),
                input_latest_trading_date=history_days[-1],
                input_source_pin=digest(source),
                inputs=source,
                source_database_pin_at_recording=d["operational"]["pin"],
                calendar_pin=calendar_pin,
                model={
                    **info,
                    "parameters": parameters,
                    "parameters_pin": digest(parameters),
                    "prior": model["directions"][direction]["prior"],
                },
                feature_version="ohlcv_value_v1",
                feature_names=list(FEATURES),
                features=list(values),
                feature_hash=digest(list(values)),
                direction=direction,
                score=value,
                threshold=0.5,
                generated_at=generated.isoformat(),
                inputs_verified_available_at=verified_at.isoformat(),
                feature_generated_at=feature_at.isoformat(),
                input_ingestion_time=None,
                input_ingestion_time_reason="original_storage_does_not_retain_commit_timestamp",
                actual_outcome=None,
                initial_evaluation_state="pending",
                pit=dict(
                    local_order_consistent=True,
                    pit_qualified=False,
                    independent_publication_time_verified=False,
                    model_creation_time_independently_authenticated=False,
                    source_publication_time="UNKNOWN",
                    availability_at_target_independently_verified=False,
                    corporate_actions="UNVERIFIED",
                    historical_revisions="POSSIBLE",
                ),
            )
            if prior:
                comparable = (
                    "input_source_pin",
                    "feature_hash",
                    "score",
                    "threshold",
                    "model",
                    "calendar_pin",
                    "outcome_sessions",
                )
                require(
                    all(prior["payload"][k] == payload[k] for k in comparable),
                    "shadow_conflicting_prediction_slot",
                )
                results.append(
                    dict(
                        ticker=ticker,
                        status="recorded",
                        duplicate=True,
                        prediction_id=prior["event_id"],
                    )
                )
                continue
            result = _persist(
                journal,
                head,
                _event("prediction", payload, generated),
                intents,
                pins,
                resume_at=generated,
            )
            head = result.to_dict()["head_pin"]
            old.append(result.to_dict()["event"])
            results.append(
                dict(
                    ticker=ticker,
                    status="recorded",
                    duplicate=False,
                    prediction_id=payload["prediction_id"],
                )
            )
        except ValueError as error:
            reason = (
                str(error)
                if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", str(error))
                else "shadow_verification_failed"
            )
            failure = dict(
                slot_id=slot,
                ticker=ticker,
                mode=d["mode"],
                target_trading_date=d["target_trading_date"],
                reason=reason,
                model_identifier=info["identifier"],
                required_input_dates=history_days,
                missing_input_dates=sorted(
                    set(history_days) - {b.trading_date.isoformat() for b in histories[ticker]}
                ),
                latest_available_date=max(
                    (b.trading_date.isoformat() for b in histories[ticker]), default=None
                ),
            )
            event = _event("failure", failure, timestamp(clock()))
            prior_failure = next(
                (e for e in old if e["event_id"] == event.to_dict()["event_id"]), None
            )
            if prior_failure:
                event = FrozenJSON.freeze(prior_failure)
            result = _persist(journal, head, event, intents, pins)
            head = result.to_dict()["head_pin"]
            old.append(result.to_dict()["event"])
            results.append(dict(ticker=ticker, status="blocked", reason=reason))
    return FrozenJSON.freeze(
        dict(
            version="research_shadow_recording_v1",
            results=results,
            head_pin=head,
            recorded=sum(r["status"] == "recorded" for r in results),
            new_predictions=sum(
                r["status"] == "recorded" and not r.get("duplicate", False) for r in results
            ),
            duplicate_predictions=sum(r.get("duplicate", False) for r in results),
            blocked=sum(r["status"] == "blocked" for r in results),
            genuine_forward_evidence=False,
            pit_qualified=False,
            performance_approved=False,
            network_requests=0,
        )
    )


def _verify_prediction(
    p: dict[str, Any],
    model: dict[str, Any],
    info: dict[str, Any],
    histories: dict[str, list[DailyBar]],
    proofs: list[dict[str, Any]],
) -> None:
    require(
        all(p["model"][key] == value for key, value in info.items())
        and p["model"]["identifier"] == info["identifier"]
        and p["model"]["artifact_sha256"] == info["artifact_sha256"],
        "shadow_original_model_mismatch",
    )
    selected = [
        b
        for b in histories[p["ticker"]]
        if b.trading_date.isoformat() in {row["trading_date"] for row in p["inputs"]["bars"]}
    ]
    require(_source(selected, proofs) == p["inputs"], "shadow_original_input_mismatch")
    values = feature_vector(
        selected, dict(quality=None, unavailable_dates={}, session_basis="observed_bars_unverified")
    )
    require(
        list(values) == p["features"] and p["direction"] == context(values),
        "shadow_original_feature_mismatch",
    )
    parameters = model["directions"][p["direction"]]["parameters"]
    require(
        parameters == p["model"]["parameters"]
        and p["model"]["prior"] == model["directions"][p["direction"]]["prior"]
        and score(parameters, values)[0] == p["score"]
        and p["threshold"] == 0.5,
        "shadow_original_score_mismatch",
    )


def resolve(
    d: dict[str, Any], journal: Path, head: str, intents: Path, pins: Path, *, clock: Clock = now
) -> FrozenJSON:
    _request(d)
    _paths(d, journal, intents, pins)
    entries = events(journal, head)
    model, info = _model(d)
    days, calendar_pin = _calendar(d)
    at = timestamp(clock())
    predictions = [e["payload"] for e in entries if e["kind"] == "prediction"]
    withdrawn = {e["payload"]["prediction_id"] for e in entries if e["kind"] == "correction"}
    selected = [p for p in predictions if p["prediction_id"] not in withdrawn]
    if not selected:
        return status(journal, head)
    require(
        all(timestamp(p["generated_at"]) <= at for p in selected),
        "shadow_evaluation_before_prediction",
    )
    start = min(p["inputs"]["bars"][0]["trading_date"] for p in selected)
    end = max(
        p["outcome_sessions"][-1]
        if at >= timestamp(p["outcome_not_before"])
        else p["input_latest_trading_date"]
        for p in selected
    )
    histories, proofs = _rows(d, start, end)
    for p in selected:
        require(
            p["calendar_pin"] == calendar_pin
            and all(days.get(day) == "session" for day in p["outcome_sessions"]),
            "shadow_original_calendar_mismatch",
        )
        _verify_prediction(p, model, info, histories, proofs)
        old = [
            e
            for e in entries
            if e["kind"] == "outcome" and e["payload"]["prediction_id"] == p["prediction_id"]
        ]
        state, reason, label = "pending", "horizon_not_elapsed", None
        source = None
        available = None
        label_start = label_end = None
        if at >= timestamp(p["outcome_not_before"]):
            future = [
                b
                for b in histories[p["ticker"]]
                if b.trading_date.isoformat() in p["outcome_sessions"]
            ]
            if [b.trading_date.isoformat() for b in future] != p["outcome_sessions"]:
                reason = "missing_required_outcome_session"
            elif any(b.collected_at > at for b in future):
                reason = "outcome_not_locally_available"
            else:
                require(
                    p["mode"] == "historical_replay"
                    or all(b.collected_at >= timestamp(p["generated_at"]) for b in future),
                    "shadow_outcome_clock_before_prediction",
                )
                past = [DailyBar.from_dict(row) for row in p["inputs"]["bars"]]
                anchor = datetime.combine(
                    date.fromisoformat(p["input_latest_trading_date"]), time(16), SEOUL
                )
                label, first, last, observed, label_status = label_event(
                    past + future,
                    len(past) - 1,
                    anchor,
                    tuple(p["features"]),
                    dict(
                        quality=None, unavailable_dates={}, session_basis="observed_bars_unverified"
                    ),
                )
                source = _source(future, proofs)
                label_start = first.isoformat() if first else None
                label_end = last.isoformat() if last else None
                available = observed.isoformat() if observed else None
                state = "evaluated" if label is not None else "unavailable"
                reason = (
                    "verified_five_observation_event"
                    if label is not None
                    else "event_label_" + label_status
                )
        payload = dict(
            prediction_id=p["prediction_id"],
            mode=p["mode"],
            state=state,
            reason=reason,
            actual_label=label,
            future=source,
            future_source_pin=digest(source) if source else None,
            label_start=label_start,
            label_end=label_end,
            result_raw_observed_at=available,
            result_available_at=at.isoformat() if source else None,
            checked_at=at.isoformat(),
            correct=(int(p["score"] >= p["threshold"]) == label) if label is not None else None,
        )
        event = _event("outcome", payload, at)
        terminal = next(
            (e for e in old if e["payload"]["state"] in ("evaluated", "unavailable")), None
        )
        if terminal:
            require(
                event.to_dict()["event_id"] == terminal["event_id"],
                "shadow_conflicting_terminal_outcome",
            )
            continue
        prior = next((e for e in old if e["event_id"] == event.to_dict()["event_id"]), None)
        if prior:
            continue
        result = _persist(journal, head, event, intents, pins)
        head = result.to_dict()["head_pin"]
        entries.append(result.to_dict()["event"])
    return status(journal, head)


def status(journal: Path, head: str) -> FrozenJSON:
    from donghak_stock_vision.models.metrics import baseline_values

    entries = events(journal, head)
    predictions = [e["payload"] for e in entries if e["kind"] == "prediction"]
    withdrawn = {e["payload"]["prediction_id"] for e in entries if e["kind"] == "correction"}
    latest = {
        e["payload"]["prediction_id"]: e["payload"] for e in entries if e["kind"] == "outcome"
    }
    groups = {}
    for mode in (*MODES, "offline_fixture_rehearsal"):
        selected = [
            p
            for p in predictions
            if (
                p["mode"]
                if p["source_classification"] != "offline_fixture"
                else "offline_fixture_rehearsal"
            )
            == mode
            and p["prediction_id"] not in withdrawn
        ]
        counts = {
            name: sum(
                latest.get(p["prediction_id"], {}).get("state", "pending") == name for p in selected
            )
            for name in ("pending", "evaluated", "unavailable")
        }
        complete = [
            p for p in selected if latest.get(p["prediction_id"], {}).get("state") == "evaluated"
        ]

        def measured(rows: list[dict[str, Any]]) -> dict[str, Any]:
            labels = [latest[p["prediction_id"]]["actual_label"] for p in rows]
            baseline = [
                baseline_values(tuple(p["features"]), p["direction"], p["model"]["prior"])
                for p in rows
            ]
            return dict(
                metrics=metrics(labels, [p["score"] for p in rows]),
                baselines={
                    name: metrics(labels, [b[name] for b in baseline])
                    for name in ("prior", "reversal_strength")
                },
                statistical_interpretation_limited=(
                    len(rows) < Minimums().holdout_rows
                    or len({p["target_trading_date"] for p in rows}) < Minimums().holdout_dates
                    or min(sum(labels), len(labels) - sum(labels)) < Minimums().holdout_class
                ),
            )

        groups[mode] = dict(
            predictions=len(selected),
            **counts,
            directions={
                direction: measured([p for p in complete if p["direction"] == direction])
                for direction in ("up", "down")
            },
            by_ticker={
                ticker: measured([p for p in complete if p["ticker"] == ticker])
                for ticker in sorted({p["ticker"] for p in selected})
            },
            by_target_month={
                month: measured([p for p in complete if p["target_trading_date"][:7] == month])
                for month in sorted({p["target_trading_date"][:7] for p in selected})
            },
        )
    return FrozenJSON.freeze(
        dict(
            version="research_shadow_status_v1",
            head_pin=head,
            event_count=len(entries),
            prediction_count=len(predictions),
            withdrawn=len(withdrawn),
            failure_attempts=sum(e["kind"] == "failure" for e in entries),
            groups=groups,
            performance_approved=False,
            performance_metrics=None,
            pit_qualified=False,
            genuine_forward_evidence=False,
            independent_publication_time_verified=False,
            execution_authorized=False,
        )
    )


def correct(
    journal: Path,
    head: str,
    prediction_id: str,
    reason: str,
    intents: Path,
    pins: Path,
    *,
    clock: Clock = now,
) -> FrozenJSON:
    old = events(journal, head)
    require(
        any(e["kind"] == "prediction" and e["event_id"] == prediction_id for e in old),
        "shadow_correction_prediction_missing",
    )
    payload = dict(prediction_id=prediction_id, reason=reason)
    event = _event("correction", payload, clock())
    prior = next((e for e in old if e["event_id"] == event.to_dict()["event_id"]), None)
    return _persist(journal, head, FrozenJSON.freeze(prior) if prior else event, intents, pins)


def recover(
    d: dict[str, Any], journal: Path, previous_head: str, intent: Path, intent_pin: Path
) -> FrozenJSON:
    event = FrozenJSON(bounded(intent, 128 * 1024).decode())
    require(event.identifier == read_pin(intent_pin), "shadow_recovery_intent_pin")
    validate_event(event)
    # Recover only a committed exact intent; no new or backdated append occurs.
    recovered = recover_shadow_event(journal, event, previous_head)
    verify(d, journal, recovered.to_dict()["head_pin"])
    checkpoint = intent_pin.parent / ("head-" + recovered.to_dict()["head_pin"] + ".pin")
    if not checkpoint.exists():
        write_new(checkpoint, recovered.to_dict()["head_pin"])
    else:
        require(
            read_pin(checkpoint) == recovered.to_dict()["head_pin"],
            "shadow_head_checkpoint_conflict",
        )
    return recovered


def verify(d: dict[str, Any], journal: Path, head: str) -> FrozenJSON:
    """Read-only semantic replay of recorded predictions and terminal outcomes."""
    _request(d)
    entries = events(journal, head)
    model, info = _model(d)
    days, calendar_pin = _calendar(d)
    predictions = {e["event_id"]: e["payload"] for e in entries if e["kind"] == "prediction"}
    terminal = [
        e["payload"]
        for e in entries
        if e["kind"] == "outcome" and e["payload"]["future"] is not None
    ]
    if predictions:
        # Reject altered calendar/horizon/input dates, including sealed windows,
        # before deriving any raw-source read range from a retained event.
        for p in predictions.values():
            expected_history, expected_future = _horizon(
                {**d, "target_trading_date": p["target_trading_date"]}, info, days
            )
            require(
                p["calendar_pin"] == calendar_pin
                and p["outcome_sessions"] == expected_future
                and [b["trading_date"] for b in p["inputs"]["bars"]] == expected_history,
                "shadow_original_horizon_mismatch",
            )
        for outcome in terminal:
            require(
                [b["trading_date"] for b in outcome["future"]["bars"]]
                == predictions[outcome["prediction_id"]]["outcome_sessions"],
                "shadow_original_outcome_horizon_mismatch",
            )
        start = min(p["inputs"]["bars"][0]["trading_date"] for p in predictions.values())
        end = max(
            [p["input_latest_trading_date"] for p in predictions.values()]
            + [b["trading_date"] for outcome in terminal for b in outcome["future"]["bars"]]
        )
        rows, proofs = _rows(d, start, end)
        for p in predictions.values():
            _verify_prediction(p, model, info, rows, proofs)
        for outcome in terminal:
            p = predictions[outcome["prediction_id"]]
            future = [
                b for b in rows[p["ticker"]] if b.trading_date.isoformat() in p["outcome_sessions"]
            ]
            require(
                _source(future, proofs) == outcome["future"],
                "shadow_original_outcome_source_mismatch",
            )
            past = [DailyBar.from_dict(b) for b in p["inputs"]["bars"]]
            label, first, last, observed, label_status = label_event(
                past + future,
                len(past) - 1,
                datetime.combine(
                    date.fromisoformat(p["input_latest_trading_date"]), time(16), SEOUL
                ),
                tuple(p["features"]),
                dict(quality=None, unavailable_dates={}, session_basis="observed_bars_unverified"),
            )
            require(
                label == outcome["actual_label"]
                and (label is not None) == (outcome["state"] == "evaluated")
                and outcome["correct"]
                == ((int(p["score"] >= p["threshold"]) == label) if label is not None else None)
                and outcome["result_raw_observed_at"]
                == (observed.isoformat() if observed else None)
                and outcome["label_start"] == (first.isoformat() if first else None)
                and outcome["label_end"] == (last.isoformat() if last else None),
                "shadow_outcome_reproduction_failed",
            )
            require(
                timestamp(outcome["checked_at"]) >= timestamp(p["outcome_not_before"])
                and outcome["result_available_at"] == outcome["checked_at"]
                and all(b.collected_at <= timestamp(outcome["checked_at"]) for b in future),
                "shadow_outcome_availability_clock",
            )
    view = status(journal, head).to_dict()
    return FrozenJSON.freeze(
        dict(
            version="research_shadow_verification_v1",
            head_pin=head,
            predictions_verified=len(predictions),
            terminal_outcomes_verified=len(terminal),
            status_pin=digest(view),
            pit_qualified=False,
            performance_approved=False,
            genuine_forward_evidence=False,
            independent_publication_time_verified=False,
            network_requests=0,
        )
    )


def evaluation_readiness(
    d: dict[str, Any],
    journal: Path,
    head: str,
    *,
    clock: Clock = now,
    config: SchedulerConfig | None = None,
) -> FrozenJSON:
    """Read-only time/retention projection; resolve remains the only outcome owner.

    Do not read uncommitted future bars, infer their publication, label events or
    append Pending events here. A passed local time guard only permits attempting
    the existing verifier; it never establishes that five result sources exist.
    """
    _request(d)
    started = timestamp(clock())
    entries = events(journal, head)
    require(
        all(timestamp(e["recorded_at"]) <= started for e in entries),
        "shadow_readiness_before_recorded_event",
    )
    proof = verify(d, journal, head).to_dict()
    checked = timestamp(clock())
    require(started <= checked, "shadow_readiness_clock_order")
    scope = None
    if config is not None:
        require(
            d["operational"]["root"] == str(config.path("operational_root")),
            "shadow_readiness_operational_owner",
        )
        policy = _policy(config)
        scope = FullMarketScope.from_dict(policy["full_market_scope"])
    withdrawn = {e["payload"]["prediction_id"] for e in entries if e["kind"] == "correction"}
    latest = {
        e["payload"]["prediction_id"]: e["payload"] for e in entries if e["kind"] == "outcome"
    }
    rows = []
    for event in entries:
        if event["kind"] != "prediction":
            continue
        p = event["payload"]
        outcome = latest.get(p["prediction_id"], {})
        state = "withdrawn" if p["prediction_id"] in withdrawn else outcome.get("state", "pending")
        not_before = timestamp(p["outcome_not_before"])
        pending = state == "pending"
        capture_bound = None
        if scope is not None:
            last = date.fromisoformat(p["outcome_sessions"][-1])
            capture_bound = max(
                datetime.combine(last + timedelta(days=1), time(), SEOUL), scope.valid_from
            )
        rows.append(
            dict(
                prediction_id=p["prediction_id"],
                ticker=p["ticker"],
                mode=p["mode"],
                source_classification=p["source_classification"],
                input_latest_trading_date=p["input_latest_trading_date"],
                target_trading_date=p["target_trading_date"],
                outcome_sessions=p["outcome_sessions"],
                state=state,
                original_generated_at=p["generated_at"],
                resolve_local_not_before=p["outcome_not_before"],
                local_time_guard_elapsed=checked >= not_before,
                can_attempt_existing_resolve=pending and checked >= not_before,
                reason=(
                    "horizon_not_elapsed"
                    if checked < not_before
                    else "result_source_verification_required"
                )
                if pending
                else "retained_" + state,
                last_recorded_outcome_reason=outcome.get("reason"),
                pending_result_sources_checked=False,
                terminal_outcome_replayed=state in ("evaluated", "unavailable"),
                earliest_last_outcome_capture_by_policy=capture_bound.isoformat()
                if capture_bound
                else None,
                configured_policy_covers_outcome_dates=(
                    all(
                        date.fromisoformat(day) in scope.permitted_days
                        for day in p["outcome_sessions"]
                    )
                    if scope is not None
                    else None
                ),
                policy_capture_bound_is_publication_guarantee=False,
                configured_policy_active_at_capture_bound=(
                    scope.valid_from <= capture_bound < scope.valid_until
                    if scope is not None and capture_bound is not None
                    else None
                ),
            )
        )
    pending = [r for r in rows if r["state"] == "pending"]
    return FrozenJSON.freeze(
        dict(
            version="research_shadow_evaluation_readiness_v1",
            checked_at=checked.isoformat(),
            head_pin=head,
            status_pin=proof["status_pin"],
            predictions_verified=proof["predictions_verified"],
            results=rows,
            pending=len(pending),
            evaluated=sum(r["state"] == "evaluated" for r in rows),
            resolve_time_eligible=sum(r["can_attempt_existing_resolve"] for r in rows),
            next_local_resolve_guard=min(
                (
                    r["resolve_local_not_before"]
                    for r in pending
                    if not r["local_time_guard_elapsed"]
                ),
                default=None,
            ),
            result_source_verification_owner="existing_shadow_resolve",
            network_permission_and_budget_checked=False,
            independent_result_publication_time_verified=False,
            new_predictions=0,
            journal_changed=False,
            network_requests=0,
            pit_qualified=False,
            performance_approved=False,
            performance_metrics=None,
            genuine_forward_evidence=False,
            execution_authorized=False,
        )
    )
