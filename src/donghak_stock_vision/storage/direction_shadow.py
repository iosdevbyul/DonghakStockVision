"""Multi-horizon consumer of existing source, model, journal and metric owners."""

from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.direction_shadow import VERSION, contract, event, identifier
from donghak_stock_vision.data.learning import Sample, digest, timestamp
from donghak_stock_vision.data.research_calendar import ResearchCalendarPolicy
from donghak_stock_vision.data.research_split import ResearchDateRange
from donghak_stock_vision.data.schema import SEOUL, DailyBar, validate_ticker
from donghak_stock_vision.data.snapshot import capture
from donghak_stock_vision.features.research import VERSION as FEATURE_VERSION
from donghak_stock_vision.features.research import vector
from donghak_stock_vision.models.autonomous import masked
from donghak_stock_vision.models.experiment import Candidate, predict_candidate
from donghak_stock_vision.models.research_v2 import contract as trained_contract
from donghak_stock_vision.models.research_v2 import direction_label
from donghak_stock_vision.storage import direction_ensemble
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, context, terminals
from donghak_stock_vision.storage.krx_backfill import read_pin
from donghak_stock_vision.storage.real_market_dataset import (
    _Panel,
    _protocol,
    document,
    verify_operational_window,
)
from donghak_stock_vision.storage.real_market_training import (
    RealMarketTrainingRequest,
    _quiescent,
    _VerifiedMarket,
)
from donghak_stock_vision.storage.research_checkpoint import verified_estimator
from donghak_stock_vision.storage.research_resources import lock, probe, resource_reason
from donghak_stock_vision.storage.shadow_prediction import _calendar, _persist, events


def _at(value: datetime | None) -> datetime:
    return timestamp(value or datetime.now(UTC))


def _retained_model(
    ref: dict[str, Any],
    root: Path,
    pin: str,
    at: datetime,
    load_checkpoint: bool,
) -> dict[str, Any]:
    from donghak_stock_vision.storage import predictive_research as owner

    _quiescent(root / DB, sha(ref["database_sha256"]))
    plan = owner._plan(root, pin)
    experiment_id = sha(ref["experiment_id"])
    result_id = sha(ref["result_id"])
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        link = store.connection.execute(
            "SELECT json_extract(payload,'$.plan_id'),json_extract(payload,'$.shortlist_id') "
            "FROM analysis_artifacts WHERE kind='predictive_research_result' AND id=?",
            (result_id,),
        ).fetchone()
        require(link is not None and link[0] == pin, "direction_retained_result_binding")
        shortlist = store.get("predictive_research_shortlist", link[1])
        require(shortlist["plan_id"] == pin, "direction_retained_shortlist_binding")
        matched = [
            t["trials"]["legacy_control"]
            for t in shortlist["selection"].values()
            if t["trials"]["legacy_control"]["experiment_id"] == experiment_id
        ]
        require(len(matched) == 1, "direction_retained_legacy_required")
        trial_id = digest(matched[0])
        trial = store.get("predictive_research_trial", trial_id)
        created = store.created_at("predictive_research_trial", trial_id)
        require(
            timestamp(plan["registered_at"]) <= created <= timestamp(shortlist["frozen_at"]) <= at,
            "direction_retained_model_clock",
        )
    cfg = trial["config"]
    h = cfg["goal"]["horizon"]
    train = trial["training_counts"]
    train_pin = sha(trial["training_population"])
    model_id = sha(trial["model_id"])
    checkpoint = root / "models" / (experiment_id + ".checkpoint")
    protected = plan["sources"][0]["original_final_test"]
    require(
        trial["state"] == "completed"
        and cfg == owner._candidate_configuration(h, 16, owner.RF, "v2_legacy")
        and cfg["goal"] == trained_contract(h)
        and experiment_id == digest(dict(plan_id=pin, config=cfg, train=train_pin))
        and trial["checkpoint"] == str(checkpoint.relative_to(root))
        and checkpoint.resolve() == checkpoint
        and train["tickers"] == 16
        and min(train["positive"], train["negative"]) > 0
        and train["positive"] + train["negative"] == train["rows"]
        and all(source["original_final_test"] == protected for source in plan["sources"]),
        "direction_retained_trial_binding",
    )
    _quiescent(checkpoint, model_id)
    if load_checkpoint:
        verified_estimator(
            checkpoint, model_id, cfg["candidate"]["family"], plan["code"]["versions"]
        )
    _quiescent(root / DB, ref["database_sha256"])
    return dict(
        reference=ref,
        experiment_id=experiment_id,
        sha256=model_id,
        created_at=created.isoformat(),
        config=cfg,
        versions=plan["code"]["versions"],
        checkpoint=str(checkpoint),
        fit_end=plan["windows"]["train"]["end"],
        protected=protected,
        baseline=train["positive"] / train["rows"],
        lineage=dict(
            plan_id=pin,
            result_id=result_id,
            trial_id=trial_id,
            source_pin=plan["sources"][0]["source_pin"],
            dataset_id=plan["sources"][0]["dataset_id"],
            training_population=train_pin,
            training_window=plan["windows"]["train"],
            exposed_through=plan["windows"]["evaluation"]["end"],
            cache_source=trial["cache_source"],
            descriptive_only=h == 20,
            baselines=["training_prior", "training_majority", "momentum"],
        ),
    )


def model_reference(
    ref: dict[str, Any],
    at: datetime,
    *,
    load_checkpoint: bool = True,
    context_cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    retained = "result_id" in ref
    fields(
        ref,
        "root plan_pin_file experiment_id" + (" result_id database_sha256" if retained else ""),
    )
    root = Path(ref["root"])
    pin = read_pin(Path(ref["plan_pin_file"]))
    if retained:
        return _retained_model(ref, root, pin, at, load_checkpoint)
    if context_cache is None:
        plan, data = context(root, pin, code_check=False)
    else:
        key = (str(root), pin)
        if key not in context_cache:
            require(len(context_cache) < 3, "direction_model_context_bound")
            context_cache[key] = context(root, pin, code_check=False)
        plan, data = context_cache[key]
    require(
        plan["version"] in ("autonomous_plan_v4", "autonomous_plan_v5", "autonomous_plan_v6"),
        "direction_model_owner_version",
    )
    with closing(ReadOnlyAnalysisStore(root / DB)) as store:
        terminal = terminals(store).get(ref["experiment_id"])
        require(
            terminal is not None and terminal["state"] == "completed", "direction_model_not_ready"
        )
        assert terminal is not None
        result = store.get("autonomous_result", terminal["result_id"])
    cfg = terminal["config"]
    h = cfg["goal"]["horizon"]
    require(
        cfg["goal"] == trained_contract(h) and timestamp(terminal["finished_at"]) <= at,
        "direction_model_contract_or_clock",
    )
    if load_checkpoint:
        verify = verified_estimator(
            Path(terminal["checkpoint"]),
            terminal["model_sha256"],
            cfg["candidate"]["family"],
            plan["code"]["versions"],
        )
        del verify
    training = result["training"]
    _, access = _protocol(document(str(root / "request.json")))
    return dict(
        reference=ref,
        experiment_id=ref["experiment_id"],
        sha256=terminal["model_sha256"],
        created_at=terminal["finished_at"],
        config=cfg,
        versions=plan["code"]["versions"],
        checkpoint=terminal["checkpoint"],
        fit_end=data["historical"]["search_end"]
        if "historical" in data
        else data["split"]["training"]["end"],
        protected=access.final_test.to_dict(),
        baseline=training["positive"] / training["rows"],
    )


def _registration_model(
    ref: dict[str, Any],
    at: datetime,
    *,
    ensembles: bool,
    load_checkpoint: bool,
    context_cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    if ensembles and ref.get("version") == direction_ensemble.VERSION:
        return direction_ensemble.resolve(ref, at, load_checkpoint=load_checkpoint)
    return model_reference(ref, at, load_checkpoint=load_checkpoint, context_cache=context_cache)


def ensemble_pin_name(models: dict[str, Any]) -> str:
    identities = [m["ensemble_id"] for m in models.values() if "ensemble_id" in m]
    require(len(identities) == 1, "direction_ensemble_registration_count")
    return sha(identities[0]) + ".pin"


def register(request: dict[str, Any], *, at: datetime | None = None) -> FrozenJSON:
    fields(request, "version models tickers source_kind")
    require(
        request["version"]
        in ("direction_shadow_registration_request_v3", "direction_shadow_registration_request_v4")
        and request["source_kind"] in ("local_provider_capture", "offline_fixture"),
        "direction_registration_version",
    )
    require(
        request["tickers"] == sorted(set(request["tickers"]))
        and 1 <= len(request["tickers"]) <= 25,
        "direction_ticker_bound",
    )
    for ticker in request["tickers"]:
        validate_ticker(ticker)
    now = _at(at)
    ensembles = request["version"] == "direction_shadow_registration_request_v4"
    require(
        type(request["models"]) is list and len(request["models"]) == 3,
        "direction_all_horizons_required",
    )
    models = {}
    cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for ref in request["models"]:
        m = _registration_model(
            ref, now, ensembles=ensembles, load_checkpoint=True, context_cache=cache
        )
        h = str(m["config"]["goal"]["horizon"])
        require(h not in models, "direction_duplicate_horizon")
        models[h] = m
    require(set(models) == {"1", "5", "20"}, "direction_all_horizons_required")
    if ensembles:
        ensemble_pin_name(models)
    return FrozenJSON.freeze(
        dict(
            version="direction_shadow_registration_v4"
            if ensembles
            else "direction_shadow_registration_v3",
            registered_at=now.isoformat(),
            models=models,
            tickers=request["tickers"],
            source_kind=request["source_kind"],
            contracts={h: contract(int(h)) for h in models},
            pit_qualified=False,
            performance_approved=False,
            execution_authorized=False,
        )
    )


def registration(path: Path, pin_file: Path) -> FrozenJSON:
    value = FrozenJSON.freeze(document(str(path)))
    d = value.to_dict()
    require(
        value.identifier == read_pin(pin_file)
        and d["version"]
        in ("direction_shadow_registration_v3", "direction_shadow_registration_v4"),
        "direction_registration_pin",
    )
    require(
        all(
            d[k] is False for k in ("pit_qualified", "performance_approved", "execution_authorized")
        )
        and d["contracts"] == {h: contract(int(h)) for h in ("1", "5", "20")},
        "direction_registration_contract",
    )
    require(set(d["models"]) == {"1", "5", "20"}, "direction_all_horizons_required")
    ensembles = d["version"] == "direction_shadow_registration_v4"
    if ensembles:
        require(pin_file.name == ensemble_pin_name(d["models"]), "direction_ensemble_pin_name")
    cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for h, m in d["models"].items():
        require(
            h == str(m["config"]["goal"]["horizon"])
            and _registration_model(
                m["reference"],
                timestamp(d["registered_at"]),
                ensembles=ensembles,
                load_checkpoint=False,
                context_cache=cache,
            )
            == m,
            "direction_registered_model_changed",
        )
    return value


def _request(d: dict[str, Any], reg: dict[str, Any]) -> None:
    fields(
        d,
        "version registration registration_pin_file research protocol "
        "operational context_window input_trading_date calendars mode",
    )
    require(
        d["version"] == "direction_shadow_request_v3"
        and d["mode"] in ("research_forward_candidate", "historical_replay"),
        "direction_request_version",
    )
    _, access = _protocol(d)
    start = date.fromisoformat(d["context_window"]["start"])
    end = date.fromisoformat(d["input_trading_date"])
    require(
        start <= end
        and (end - start).days <= 200
        and not (start <= access.final_test.end and end >= access.final_test.start),
        "direction_protected_window",
    )
    require(
        all(m["protected"] == access.final_test.to_dict() for m in reg["models"].values()),
        "direction_protected_owner",
    )
    require(
        all(
            m["lineage"]["exposed_through"] < d["input_trading_date"]
            for m in reg["models"].values()
            if "ensemble_id" in m
        ),
        "direction_ensemble_pre_exposure",
    )
    if any("lineage" in m for m in reg["models"].values()):
        require(
            start >= date(2026, 10, 1) and date.fromisoformat(d["context_window"]["end"]) < start,
            "direction_retained_operational_context_required",
        )


def _schedule(d: dict[str, Any], h: int) -> tuple[str, list[str], dict[str, str]]:
    days, _ = _calendar(d, maximum_months=6)
    anchor = d["input_trading_date"]
    require(days.get(anchor) == "session", "direction_input_session_unverified")
    sessions = sorted(day for day, state in days.items() if state == "session")
    i = sessions.index(anchor)
    future = sessions[i + 1 : i + h + 2]
    require(len(future) == h + 1, "direction_calendar_horizon_missing")
    first = date.fromisoformat(d["context_window"]["start"])
    last = date.fromisoformat(future[-1])
    require(
        all(
            days.get((first + timedelta(days=n)).isoformat()) in ("session", "holiday", "weekend")
            for n in range((last - first).days + 1)
        ),
        "direction_calendar_coverage_gap",
    )
    return future[0], future[1:], days


def _calendar_pin(d: dict[str, Any], h: int) -> str:
    _, future, days = _schedule(d, h)
    return digest(
        {
            day: state
            for day, state in days.items()
            if d["context_window"]["start"] <= day <= future[-1]
        }
    )


def _inputs(
    d: dict[str, Any], reg: dict[str, Any], at: datetime
) -> tuple[dict[str, list[DailyBar]], dict[str, Any]]:
    protocol, access = _protocol(d)
    r = d["research"]
    start = date.fromisoformat(d["context_window"]["start"])
    split = date.fromisoformat(d["context_window"]["end"])
    end = date.fromisoformat(d["input_trading_date"])
    require(split <= end, "direction_context_boundary")
    req = RealMarketTrainingRequest(
        Path(r["database"]),
        r["pin"],
        access,
        access.identifier,
        None,
        None,
        tuple(reg["tickers"]),
        at,
        ResearchCalendarPolicy.from_dict(protocol["research_calendar"]),
        r["source_contract"],
    )
    rows: dict[str, list[DailyBar]] = {t: [] for t in reg["tickers"]}
    proof: dict[str, Any] = dict(research_sources=[], raw_bytes=0)
    _quiescent(req.database, req.database_pin)
    if start <= split:
        with closing(_VerifiedMarket(req, source_window=ResearchDateRange(start, split))) as market:
            rows = {t: market.read(t, start, split) for t in reg["tickers"]}
            proof = dict(research_sources=market.sources, raw_bytes=market.raw_bytes_verified)
    op, proofs = (
        verify_operational_window(
            d["operational"],
            tuple(reg["tickers"]),
            ResearchDateRange(max(start, split + timedelta(days=1)), end),
            strict_forward_membership=False,
            allow_synthetic_fixture=reg["source_kind"] == "offline_fixture",
        )
        if split < end
        else ([], [])
    )
    for b in op:
        rows[b.ticker].append(b)
    require(
        sum(len(v) for v in rows.values()) <= 5000
        and proof["raw_bytes"] + len(proofs) * 2 * 1024**2 <= 256 * 1024**2,
        "direction_source_bound",
    )
    _, _, calendar = _schedule(d, 20)
    for bars in rows.values():
        bars.sort(key=lambda b: b.trading_date)
        require(len({b.trading_date for b in bars}) == len(bars), "direction_duplicate_input")
        expected = sorted(
            day
            for day, state in calendar.items()
            if state == "session" and d["context_window"]["start"] <= day <= d["input_trading_date"]
        )
        require(
            [b.trading_date.isoformat() for b in bars] == expected,
            "direction_missing_input_session",
        )
        require(all(b.collected_at <= at for b in bars), "direction_future_input")
    proof["operational"] = proofs
    _quiescent(req.database, req.database_pin)
    return rows, proof


def _feature(d: dict[str, Any], bars: list[DailyBar], at: datetime) -> tuple[float, ...]:
    snapshot = capture(
        _Panel({bars[0].ticker: bars}),
        [bars[0].ticker],
        bars[0].trading_date,
        bars[-1].trading_date,
        at,
        "historical_research",
        acknowledge=True,
    )
    return vector(bars, snapshot, at)


def _sample(ticker: str, day: str, values: tuple[float, ...], at: datetime) -> Sample:
    return Sample(
        ticker,
        date.fromisoformat(day),
        at,
        values,
        "up",
        None,
        None,
        None,
        None,
        "pending",
        digest(values),
    )


def _score(
    m: dict[str, Any], ticker: str, day: str, values: tuple[float, ...], at: datetime
) -> float:
    if "ensemble_id" in m:
        return direction_ensemble.average(
            [item["weight_bps"] for item in m["config"]["members"]],
            [_score(part, ticker, day, values, at) for part in m["members"]],
        )
    model = verified_estimator(
        Path(m["checkpoint"]), m["sha256"], m["config"]["candidate"]["family"], m["versions"]
    )
    sample = _sample(ticker, day, values, at)
    return predict_candidate(
        Candidate(**m["config"]["candidate"]), model, masked([sample], m["config"]["mask"])
    )[0]


def _cutoff(first: str) -> datetime:
    return datetime.combine(date.fromisoformat(first), time(), SEOUL)


def preflight(d: dict[str, Any], *, at: datetime | None = None) -> dict[str, Any]:
    """Check a pending manual window without inference or journal writes."""
    now = _at(at)
    pinned = registration(Path(d["registration"]), Path(d["registration_pin_file"]))
    reg = pinned.to_dict()
    _request(d, reg)
    require(now >= timestamp(reg["registered_at"]), "direction_before_registration")
    horizons = {}
    for h, model in reg["models"].items():
        checks: dict[str, Any] = dict(
            experiment_id=model["experiment_id"],
            model_id=model["sha256"],
            base_session=None,
            target_session=None,
            cutoff=None,
            blocked_by=[],
        )
        reasons = checks["blocked_by"]
        horizons[h] = checks
        try:
            base, future, _ = _schedule(d, int(h))
        except ValueError as error:
            reasons.append(str(error))
            continue
        cutoff = _cutoff(future[0])
        checks.update(base_session=base, target_session=future[-1], cutoff=cutoff.isoformat())
        if now >= cutoff:
            reasons.append("direction_deadline_passed")
        if model["fit_end"] >= d["input_trading_date"]:
            reasons.append("direction_model_future_fit")
    ready = (
        reg["source_kind"] == "local_provider_capture" and d["mode"] == "research_forward_candidate"
    )
    source_issue = None
    if not ready:
        source_issue = "direction_not_real_forward_request"
    elif not any(v["blocked_by"] for v in horizons.values()):
        try:
            rows, _ = _inputs(d, reg, now)
            for bars in rows.values():
                _feature(d, bars, now)
        except ValueError as error:
            source_issue = str(error)
    return dict(
        registration_id=pinned.identifier,
        input_trading_date=d["input_trading_date"],
        checked_at=now.isoformat(),
        horizons=horizons,
        source_issue=source_issue,
        source_checked=ready and not any(v["blocked_by"] for v in horizons.values()),
        status="BLOCKED"
        if source_issue or any(v["blocked_by"] for v in horizons.values())
        else "READY",
        pit_qualified=False,
        performance_approved=False,
        execution_authorized=False,
    )


def _entries(journal: Path, head: str, reg: FrozenJSON) -> list[dict[str, Any]]:
    entries = events(journal, head)
    require(
        all(
            e["version"] == VERSION
            and (e["kind"] != "prediction" or e["payload"]["registration_id"] == reg.identifier)
            for e in entries
        ),
        "direction_separate_contract_journal",
    )
    return entries


def _write_paths(
    d: dict[str, Any], r: dict[str, Any], journal: Path, intents: Path, pins: Path
) -> None:
    protected = [Path(d["operational"]["root"]), Path(d["research"]["database"]).parent]
    for m in r["models"].values():
        protected.extend(direction_ensemble.source_paths(m["reference"]))
    require(
        all(
            p.is_absolute()
            and p.resolve() == p
            and all(not p.is_relative_to(root) for root in protected)
            for p in (journal, intents, pins)
        )
        and journal.suffix == ".sqlite3"
        and journal.parent.stat().st_mode & 0o077 == 0
        and not pins.is_relative_to(intents)
        and not intents.is_relative_to(pins),
        "direction_protected_output",
    )


def record(
    d: dict[str, Any],
    journal: Path,
    head: str,
    intents: Path,
    pins: Path,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    now = _at(at)
    reg = registration(Path(d["registration"]), Path(d["registration_pin_file"]))
    r = reg.to_dict()
    _request(d, r)
    _write_paths(d, r, journal, intents, pins)
    require(now >= timestamp(r["registered_at"]), "direction_before_registration")
    require(resource_reason(journal.parent, probe()) is None, "direction_resources_unavailable")
    for h in r["models"]:
        base, future, _ = _schedule(d, int(h))
        cutoff = _cutoff(future[0])
        keys = [
            identifier(
                "prediction",
                identifier(
                    "slot",
                    dict(
                        registration=reg.identifier,
                        ticker=t,
                        horizon=int(h),
                        input=d["input_trading_date"],
                        model=r["models"][h]["experiment_id"],
                    ),
                ),
            )
            for t in r["tickers"]
        ]
        prior = _entries(journal, head, reg)
        if not all(any(e["event_id"] == key for e in prior) for key in keys):
            require(d["mode"] == "historical_replay" or now < cutoff, "direction_deadline_passed")
    if _entries(journal, head, reg):
        verify(d, journal, head)
    rows, proof = _inputs(d, r, now)
    entries = _entries(journal, head, reg)
    new = 0
    duplicates = 0
    with lock(journal.parent, ".direction-record.lock"):
        require(_entries(journal, head, reg) == entries, "direction_concurrent_change")
        for h, m in r["models"].items():
            base, future, _ = _schedule(d, int(h))

            require(
                m["fit_end"] < d["input_trading_date"] and timestamp(m["created_at"]) <= now,
                "direction_model_future_fit",
            )
            for ticker in r["tickers"]:
                slot = identifier(
                    "slot",
                    dict(
                        registration=reg.identifier,
                        ticker=ticker,
                        horizon=int(h),
                        input=d["input_trading_date"],
                        model=m["experiment_id"],
                    ),
                )
                key = identifier("prediction", slot)
                if any(e["event_id"] == key for e in entries):
                    duplicates += 1
                    continue
                values = _feature(d, rows[ticker], now)
                source = dict(
                    bars=[b.to_dict() for b in rows[ticker]],
                    proof=proof,
                    reference=dict(
                        research=d["research"],
                        protocol=d["protocol"],
                        operational_root=d["operational"]["root"],
                        evidence=d["operational"]["evidence"],
                        calendars=d["calendars"],
                    ),
                )
                score = _score(m, ticker, d["input_trading_date"], values, now)
                recorded_at = _at(at)
                require(
                    d["mode"] == "historical_replay" or recorded_at < _cutoff(future[0]),
                    "direction_deadline_passed",
                )
                payload = dict(
                    prediction_id=key,
                    slot_id=slot,
                    registration_id=reg.identifier,
                    ticker=ticker,
                    horizon=int(h),
                    contract=contract(int(h)),
                    input_trading_date=d["input_trading_date"],
                    context_window=d["context_window"],
                    calendar_pin=_calendar_pin(d, int(h)),
                    base_session=base,
                    outcome_sessions=future,
                    outcome_not_before=datetime.combine(
                        date.fromisoformat(future[-1]), time(16), SEOUL
                    )
                    .astimezone(UTC)
                    .isoformat(),
                    generated_at=recorded_at.isoformat(),
                    model=m,
                    features=list(values),
                    feature_hash=digest(values),
                    feature_version=FEATURE_VERSION,
                    input_source=source,
                    input_source_pin=digest(source),
                    source_kind=r["source_kind"],
                    mode=d["mode"],
                    score=score,
                    predicted_class=int(score >= 0.5),
                    threshold=0.5,
                    actual_outcome=None,
                    baseline_score=m["baseline"],
                )
                result = _persist(
                    journal,
                    head,
                    event("prediction", payload, recorded_at.isoformat()),
                    intents,
                    pins,
                    resume_at=recorded_at,
                ).to_dict()
                head = result["head_pin"]
                new += 1
        return dict(new_predictions=new, duplicates=duplicates, head_pin=head)


def _future(
    d: dict[str, Any], r: dict[str, Any], p: dict[str, Any], at: datetime
) -> dict[str, Any] | None:
    require(
        d["operational"]["root"] == p["input_source"]["reference"]["operational_root"],
        "direction_original_archive",
    )
    rows, proof = verify_operational_window(
        d["operational"],
        tuple(r["tickers"]),
        ResearchDateRange(
            date.fromisoformat(p["base_session"]), date.fromisoformat(p["outcome_sessions"][-1])
        ),
        strict_forward_membership=False,
        allow_synthetic_fixture=r["source_kind"] == "offline_fixture",
    )
    selected = sorted([b for b in rows if b.ticker == p["ticker"]], key=lambda b: b.trading_date)
    if [b.trading_date.isoformat() for b in selected] != [p["base_session"]] + p[
        "outcome_sessions"
    ]:
        return None
    require(all(b.collected_at <= at for b in selected), "direction_future_result_clock")
    return dict(
        bars=[b.to_dict() for b in selected],
        proof=proof,
        evidence=d["operational"]["evidence"],
        root=d["operational"]["root"],
    )


def _label(future: dict[str, Any]) -> int | None:
    rows = [DailyBar.from_dict(b) for b in future["bars"]]
    base, last = rows[0].close, rows[-1].close
    return direction_label(base, last)


def verify(d: dict[str, Any], journal: Path, head: str) -> tuple[FrozenJSON, list[dict[str, Any]]]:
    reg = registration(Path(d["registration"]), Path(d["registration_pin_file"]))
    r = reg.to_dict()
    _request(d, r)
    entries = _entries(journal, head, reg)
    _validate_entries(d, reg, entries)
    require(_entries(journal, head, reg) == entries, "direction_journal_changed")
    return reg, entries


def _validate_entries(d: dict[str, Any], reg: FrozenJSON, entries: list[dict[str, Any]]) -> None:
    r = reg.to_dict()
    for e in entries:
        p = e["payload"]
        if e["kind"] == "prediction":
            original = {
                **d,
                "input_trading_date": p["input_trading_date"],
                "context_window": p["context_window"],
            }
            reference = p["input_source"]["reference"]
            require(
                reference["operational_root"] == d["operational"]["root"],
                "direction_original_archive",
            )
            original.update(
                calendars=reference["calendars"],
                research=reference["research"],
                protocol=reference["protocol"],
                operational=d["operational"] | dict(evidence=reference["evidence"]),
            )
            _request(original, r)
            base, future, _ = _schedule(original, p["horizon"])
            require(
                p["outcome_not_before"]
                == datetime.combine(date.fromisoformat(future[-1]), time(16), SEOUL)
                .astimezone(UTC)
                .isoformat(),
                "direction_outcome_guard_changed",
            )
            protected = r["models"][str(p["horizon"])]["protected"]
            require(
                not any(protected["start"] <= day <= protected["end"] for day in [base] + future),
                "direction_protected_future",
            )
            require(
                base == p["base_session"]
                and p["calendar_pin"] == _calendar_pin(original, p["horizon"])
                and future == p["outcome_sessions"]
                and p["model"] == r["models"][str(p["horizon"])]
                and p["ticker"] in r["tickers"]
                and p["source_kind"] == r["source_kind"],
                "direction_original_binding",
            )
            generated = timestamp(p["generated_at"])
            require(
                p["feature_version"] == FEATURE_VERSION
                and p["baseline_score"] == p["model"]["baseline"],
                "direction_frozen_score_conditions",
            )
            require(generated >= timestamp(r["registered_at"]), "direction_before_registration")
            rows, proof = _inputs(original, r, generated)
            source = dict(
                bars=[b.to_dict() for b in rows[p["ticker"]]], proof=proof, reference=reference
            )
            values = _feature(original, rows[p["ticker"]], generated)
            require(
                source == p["input_source"]
                and list(values) == p["features"]
                and _score(p["model"], p["ticker"], p["input_trading_date"], values, generated)
                == p["score"],
                "direction_prediction_replay_failed",
            )
        else:
            parent = next(
                x["payload"]
                for x in entries
                if x["kind"] == "prediction" and x["event_id"] == p["prediction_id"]
            )
            require(
                timestamp(p["checked_at"]) >= timestamp(parent["generated_at"]),
                "direction_outcome_clock_regression",
            )
            if p["future"] is None:
                continue
            original = next(
                x["payload"]
                for x in entries
                if x["kind"] == "prediction" and x["event_id"] == p["prediction_id"]
            )
            require(
                timestamp(p["checked_at"]) >= timestamp(original["outcome_not_before"]),
                "direction_outcome_too_early",
            )
            require(
                p["future"]["root"]
                == d["operational"]["root"]
                == original["input_source"]["reference"]["operational_root"],
                "direction_original_archive",
            )
            replay = d | dict(operational=d["operational"] | dict(evidence=p["future"]["evidence"]))
            actual = _future(replay, r, original, timestamp(p["checked_at"]))
            if actual is not None:
                label = _label(actual)
                require(
                    p["state"] == ("evaluated" if label is not None else "unavailable")
                    and p["correct"]
                    == ((int(original["score"] >= 0.5) == label) if label is not None else None),
                    "direction_outcome_consistency",
                )
            require(
                actual == p["future"] and _label(actual) == p["actual_label"]
                if actual is not None
                else False,
                "direction_outcome_replay_failed",
            )


def resolve(
    d: dict[str, Any],
    journal: Path,
    head: str,
    intents: Path,
    pins: Path,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    now = _at(at)
    reg, entries = verify(d, journal, head)
    r = reg.to_dict()
    _write_paths(d, r, journal, intents, pins)
    resolved = 0
    pending = 0
    terminal = {
        e["payload"]["prediction_id"]
        for e in entries
        if e["kind"] == "outcome" and e["payload"]["state"] != "pending"
    }
    for e in entries:
        if e["kind"] != "prediction" or e["event_id"] in terminal:
            continue
        p = e["payload"]
        future = None
        if now >= timestamp(p["outcome_not_before"]):
            future = _future(d, r, p, now)
        label = _label(future) if future else None
        state = "pending" if future is None else "evaluated" if label is not None else "unavailable"
        payload = dict(
            prediction_id=p["prediction_id"],
            state=state,
            reason="incomplete_sources_or_time_guard"
            if state == "pending"
            else "neutral_cost_band"
            if state == "unavailable"
            else "verified_original_outcome",
            actual_label=label,
            future=future,
            future_pin=digest(future) if future else None,
            checked_at=now.isoformat(),
            correct=(int(p["score"] >= 0.5) == label) if label is not None else None,
        )
        prior = next(
            (
                x
                for x in reversed(entries)
                if x["kind"] == "outcome" and x["payload"]["prediction_id"] == p["prediction_id"]
            ),
            None,
        )
        if prior and {k: v for k, v in prior["payload"].items() if k != "checked_at"} == {
            k: v for k, v in payload.items() if k != "checked_at"
        }:
            pending += state == "pending"
            continue
        result = _persist(
            journal, head, event("outcome", payload, now.isoformat()), intents, pins
        ).to_dict()
        head = result["head_pin"]
        resolved += state == "evaluated"
        pending += state == "pending"
    return dict(head_pin=head, evaluated_this_run=resolved, pending=pending)


def recover(
    d: dict[str, Any], journal: Path, head: str, intent: Path, pin_file: Path
) -> dict[str, Any]:
    value = FrozenJSON.freeze(document(str(intent)))
    require(value.identifier == read_pin(pin_file), "direction_recovery_pin")
    from donghak_stock_vision.storage.forward_signal_journal import recover_shadow_event

    result = recover_shadow_event(journal, value, head).to_dict()
    verify(d, journal, result["head_pin"])
    return result


def report(d: dict[str, Any], journal: Path, head: str) -> dict[str, Any]:
    from donghak_stock_vision.models.direction_evaluation import publication

    reg, entries = verify(d, journal, head)
    return publication(reg.to_dict(), entries)


def audit(d: dict[str, Any], journal: Path, head: str) -> dict[str, Any]:
    """Private verified evidence for analysis, with original source and journal lineage."""
    from donghak_stock_vision.models.direction_evaluation import publication, retained_evidence
    from donghak_stock_vision.models.failure_feedback import readout
    from donghak_stock_vision.models.stock_generalization import regime

    reg, entries = verify(d, journal, head)
    r = reg.to_dict()
    release = publication(r, entries)
    latest = {e["payload"]["prediction_id"]: e for e in entries if e["kind"] == "outcome"}
    rows = []
    for e in entries:
        p = e["payload"]
        if e["kind"] != "prediction" or "lineage" not in p["model"]:
            continue
        outcome = latest.get(p["prediction_id"], {})
        o = outcome.get("payload", {})
        source = retained_evidence(p, o)
        label = o.get("actual_label")
        predicted = p["predicted_class"]
        groups = regime(
            _sample(
                p["ticker"],
                p["input_trading_date"],
                tuple(p["features"]),
                timestamp(p["generated_at"]),
            )
        )
        rows.append(
            dict(
                forecast_id=p["prediction_id"],
                forecast_hash=digest(e),
                outcome_id=outcome.get("event_id"),
                registration_id=reg.identifier,
                experiment_id=p["model"]["experiment_id"],
                model_id=p["model"]["sha256"],
                ticker=p["ticker"],
                horizon=p["horizon"],
                mode=p["mode"],
                source_kind=p["source_kind"],
                generated_at=p["generated_at"],
                latest_input_available_at=max(b["collected_at"] for b in p["input_source"]["bars"]),
                input_source_pin=p["input_source_pin"],
                input_date=p["input_trading_date"],
                base_date=p["base_session"],
                target_date=p["outcome_sessions"][-1],
                state=o.get("state", "pending"),
                observation_window=source,
                outcome_sources=o.get("future"),
                checked_at=o.get("checked_at"),
                label=label,
                predicted_class=predicted,
                error_type=("false_positive" if predicted else "false_negative")
                if label is not None and label != predicted
                else None,
                scores=dict(
                    model=p["score"],
                    training_prior=p["baseline_score"],
                    training_majority=float(p["baseline_score"] >= 0.5),
                    momentum=source["momentumScore"],
                ),
                regimes=groups,
                contract=p["contract"],
                independent_use_eligible=False,
            )
        )
    diagnostics = []
    for m in release["models"]:
        h = m["horizon"]
        if "lineage" not in r["models"][str(h)]:
            continue
        group = [
            row
            for row in rows
            if (row["experiment_id"], row["horizon"], row["mode"], row["source_kind"])
            == (m["modelId"], h, m["mode"], m["sourceQuality"])
        ]
        paired = {
            (row["ticker"], row["input_date"], row["target_date"]): row
            for row in group
            if row["state"] == "evaluated"
        }
        evaluated = list(paired.values())
        minimum = r["contracts"][str(h)]["evaluation"]["minimum_calendar_anchors"]
        remaining = minimum - m["qualifiedAnchors"]
        panels: dict[str, dict[str, list[dict[str, Any]]]] = dict(ticker={}, month={}, regime={})
        for row in evaluated:
            panels["ticker"].setdefault(row["ticker"], []).append(row)
            panels["month"].setdefault(row["input_date"][:7], []).append(row)
            for key in row["regimes"]:
                panels["regime"].setdefault(key, []).append(row)
        diagnostics.append(
            dict(
                horizon=h,
                mode=m["mode"],
                total=len(group),
                evaluated=m["evaluated"],
                duplicate_evaluations=m["duplicateEvaluationRows"],
                pending=m["pending"],
                neutral=sum(row["state"] == "unavailable" for row in group),
                local_nonoverlapping_anchors=m["independentAnchors"],
                independently_qualified_anchors=m["qualifiedAnchors"],
                remaining_independent_anchors=remaining,
                theoretical_sessions=dict(
                    outcome_intervals_only=remaining * h + (2 if remaining else 0),
                    disjoint_input_to_target=remaining * (h + 2),
                ),
                missing_local_positive_rows=m["missingPositive"],
                missing_local_negative_rows=m["missingNegative"],
                metrics={
                    key: readout(evaluated, key)
                    for key in ("model", "training_prior", "training_majority", "momentum")
                },
                breakdown={
                    kind: {key: readout(panel, "model") for key, panel in groups.items()}
                    for kind, groups in panels.items()
                },
                blockers=m["blockers"],
            )
        )
    return dict(
        version="direction_shadow_audit_v1",
        registration_id=reg.identifier,
        head_pin=head,
        models=r["models"],
        diagnostics=diagnostics,
        forecasts=rows,
        independent_memory_eligible=0,
        pit_qualified=False,
        performance_approved=False,
    )


def publish_streams(d: dict[str, Any]) -> dict[str, Any]:
    from donghak_stock_vision.models.direction_evaluation import summarize

    fields(d, "version streams")
    require(
        d["version"] == "direction_shadow_collection_v1" and 1 <= len(d["streams"]) <= 10,
        "direction_publication_bound",
    )
    rows = []
    identities = {}
    cohorts: dict[str, int] = {}
    for item in d["streams"]:
        fields(item, "kind request request_pin_file journal head_pin_file")
        require(item["kind"] in ("direction_v3", "legacy_v1_v2"), "direction_stream_kind")
        request = document(item["request"])
        require(
            digest(request) == read_pin(Path(item["request_pin_file"])),
            "direction_stream_request_pin",
        )
        from donghak_stock_vision.models.direction_evaluation import publication

        if item["kind"] == "legacy_v1_v2":
            from donghak_stock_vision.models.direction_evaluation import legacy_publication
            from donghak_stock_vision.storage import shadow_prediction as legacy

            legacy.verify(request, Path(item["journal"]), read_pin(Path(item["head_pin_file"])))
            entries = legacy.events(Path(item["journal"]), read_pin(Path(item["head_pin_file"])))
            value = legacy_publication(entries)
        else:
            reg, entries = verify(
                request, Path(item["journal"]), read_pin(Path(item["head_pin_file"]))
            )
            value = publication(reg.to_dict(), entries)
        sources = {
            e["payload"]["prediction_id"]: e["payload"]["input_source_pin"]
            for e in entries
            if e["kind"] == "prediction"
        }
        for row in value["predictions"]:
            row["inputCohort"] = cohorts.setdefault(sources[row["predictionId"]], len(cohorts))
        rows.extend(value["predictions"])
        for m in value["models"]:
            identity = {
                k: m[k]
                for k in (
                    "modelId",
                    "modelSha256",
                    "horizon",
                    "mode",
                    "sourceQuality",
                    "labelContract",
                    "evaluationPolicyId",
                    "predictionType",
                    "featureVersion",
                )
            }
            identities[digest(identity)] = identity
    require(len(rows) <= 1000, "direction_publication_prediction_bound")
    return summarize(rows, list(identities.values()))
