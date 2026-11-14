"""Manual bounded research continuation over existing collection/journal owners.

Never collects, schedules, repins historical inputs or owns a new journal schema.
A separately retained report PIN permits explicitly resuming acknowledged heads.
"""

import re
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.learning import digest, timestamp
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.providers.base import krx_market_scope
from donghak_stock_vision.storage.historical_readiness import _file_hash
from donghak_stock_vision.storage.krx_availability import assess_availability
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.krx_backlog import _records, _verify
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_market import DB, OperationalMarketStore
from donghak_stock_vision.storage.prospective_operational_readiness import CaptureSource
from donghak_stock_vision.storage.real_market_dataset import document
from donghak_stock_vision.storage.real_market_training import _quiescent
from donghak_stock_vision.storage.shadow_handoff import _derived_request, _retain, after_collection
from donghak_stock_vision.storage.shadow_prediction import (
    Clock,
    _calendar,
    _horizon,
    _model,
    _paths,
    _request,
    evaluation_readiness,
    events,
    now,
    resolve,
    status,
    verify,
)

VERSION = "research_cycle_request_v1"


def _streams(d: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    fields(d, "version streams active_v2")
    require(
        d["version"] == VERSION and type(d["streams"]) is list and 0 < len(d["streams"]) <= 8,
        "cycle_version_or_stream_bound",
    )
    result = []
    for item in d["streams"]:
        ref = fields(item, "name request request_pin_file journal head_pin_file intents pins_root")
        require(
            type(ref["name"]) is str
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", ref["name"]) is not None,
            "cycle_stream_name",
        )
        template = document(ref["request"])
        require(
            digest(template) == read_pin(Path(ref["request_pin_file"])),
            "cycle_template_pin_mismatch",
        )
        _request(template)
        require(template["mode"] == "research_forward_candidate", "cycle_forward_only")
        _paths(template, Path(ref["journal"]), Path(ref["intents"]), Path(ref["pins_root"]))
        result.append((ref, template))
    require(
        len({r["name"] for r, _ in result}) == len(result)
        and len({r["journal"] for r, _ in result}) == len(result),
        "cycle_duplicate_stream",
    )
    require(
        any(
            r["name"] == d["active_v2"] and t["version"] == "research_shadow_request_v2"
            for r, t in result
        ),
        "cycle_active_v2_required",
    )
    return result


def _resume(d: dict[str, Any], path: Path | None, pin: Path | None) -> dict[str, Any]:
    require((path is None) == (pin is None), "cycle_complete_resume_reference_required")
    if path is None:
        return {}
    assert pin is not None
    previous = document(str(path), 8 * 1024**2)
    require(
        digest(previous) == read_pin(pin)
        and previous["version"] == "research_cycle_report_v1"
        and previous["request_pin"] == digest(d),
        "cycle_resume_binding",
    )
    require(type(previous["streams"]) is dict, "cycle_resume_streams_required")
    return cast(dict[str, Any], previous["streams"])


def _head(ref: dict[str, Any], resumed: dict[str, Any]) -> tuple[Path, str]:
    name = ref["name"]
    path = Path(resumed[name]["head_pin_file"] if name in resumed else ref["head_pin_file"])
    require(
        path.resolve() == path
        and (path == Path(ref["head_pin_file"]) or path.is_relative_to(Path(ref["pins_root"]))),
        "cycle_head_owner",
    )
    return path, read_pin(path)


def _freeze(d: dict[str, Any], root: Path, pins: Path) -> tuple[Path, Path]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    pins.mkdir(mode=0o700, parents=True, exist_ok=True)
    request, pin = root / "request.json", pins / "request.pin"
    _retain(request, FrozenJSON.freeze(d).payload_json)
    _retain(pin, digest(d))
    return request, pin


def run_cycle(
    config: SchedulerConfig,
    d: dict[str, Any],
    report: Path,
    pins: Path,
    *,
    resume_report: Path | None = None,
    resume_pin: Path | None = None,
    clock: Clock = now,
) -> FrozenJSON:
    streams = _streams(d)
    resumed = _resume(d, resume_report, resume_pin)
    require(
        report.is_absolute()
        and report.resolve() == report
        and not report.exists()
        and pins.is_absolute()
        and pins.resolve() == pins
        and not pins.exists()
        and not report.is_relative_to(pins)
        and not pins.is_relative_to(report.parent),
        "cycle_new_separate_output_required",
    )
    protected = [
        config.path(n) for n in ("operational_root", "capture_root", "policy_root", "runner_root")
    ]
    protected += [Path(t["model"]["database"]).parent for _, t in streams]
    require(
        all(not p.is_relative_to(root) for p in (report, pins) for root in protected),
        "cycle_output_inside_protected_source",
    )
    report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(report.parent.stat().st_mode & 0o077 == 0, "cycle_private_report_directory")
    pins.mkdir(mode=0o700, parents=True)
    started = timestamp(clock())
    database = config.path("operational_root") / DB
    database_pin = _file_hash(database)
    _quiescent(database, database_pin)
    with closing(OperationalMarketStore(config.path("operational_root"))) as store:
        market = store.status().to_dict()  # Metadata only; never replay future result bodies here.
        metadata = store.connection.execute(
            "SELECT source_date,collected_at FROM raw_pages WHERE provider='krx' AND ticker=? "
            "AND source_date IN (SELECT DISTINCT trading_date FROM market_observations "
            "WHERE market='KOSPI') ORDER BY id LIMIT 4097",
            (krx_market_scope("KOSPI"),),
        ).fetchall()
    require(len(metadata) <= 4096, "cycle_source_metadata_bound")
    latest = max((day for day, at in metadata if timestamp(at) <= started), default=None)
    completions = _records(config)
    verified: dict[str, dict[str, Any]] = {}

    def fresh(
        template: dict[str, Any],
        ref: dict[str, Any],
        model: dict[str, Any],
        *,
        target: str | None = None,
    ) -> dict[str, Any]:
        require(
            template["operational"]["root"] == str(config.path("operational_root")),
            "cycle_operational_owner",
        )
        value = template | {"target_trading_date": target or template["target_trading_date"]}
        days, held_calendar_pin = _calendar(value)
        history, _ = _horizon(value, model, days)  # Guard sealed windows before source replay.
        old = events(Path(ref["journal"]), _head(ref, resumed)[1])
        predictions = [e["payload"] for e in old if e["kind"] == "prediction"]
        for prediction in predictions:
            expected_history, expected_future = _horizon(
                value | {"target_trading_date": prediction["target_trading_date"]}, model, days
            )
            require(
                prediction["calendar_pin"] == held_calendar_pin
                and prediction["outcome_sessions"] == expected_future
                and [b["trading_date"] for b in prediction["inputs"]["bars"]] == expected_history,
                "cycle_original_horizon_mismatch",
            )

        first = min([history[0]] + [p["inputs"]["bars"][0]["trading_date"] for p in predictions])
        last = max(
            [history[-1]]
            + [
                p["outcome_sessions"][-1]
                if started >= timestamp(p["outcome_not_before"])
                else p["input_latest_trading_date"]
                for p in predictions
            ]
        )
        # A refreshed whole-file reference is separate local evidence, never a replacement PIN.
        value = value | {"operational": value["operational"] | {"pin": database_pin}}
        for completion in completions:
            if first <= completion["day"] <= last:
                require(timestamp(completion["at"]) <= started, "cycle_completion_after_execution")
                if completion["day"] not in verified:
                    verified[completion["day"]] = _verify(config, completion)
                value, _ = _derived_request(
                    config,
                    value,
                    completion,
                    report.parent / "inputs" / ref["name"],
                    pins / "inputs" / ref["name"],
                )
        _quiescent(database, database_pin)
        return value

    heads = {ref["name"]: _head(ref, resumed) for ref, _ in streams}
    summaries = {
        ref["name"]: status(Path(ref["journal"]), heads[ref["name"]][1]).to_dict()
        for ref, _ in streams
    }
    results: dict[str, Any] = {}
    for ref, template in streams:
        name = ref["name"]
        head_file, head = heads[name]
        row: dict[str, Any] = dict(
            contract=template["version"],
            journal=ref["journal"],
            model_version=template["model"]["model_version"],
            source_kind=template["source_kind"],
            head_pin_file=str(head_file),
            status="blocked",
            stage="source_verification",
            new_predictions=0,
            duplicates=0,
            blocked=0,
            reasons=[],
            source_pin_verified=False,
            summary=summaries[name],
            counts_from_journal_only=True,
            journal_verified=False,
            journal_write_outcome="not_attempted",
        )
        results[name] = row
        try:
            _, model_info = _model(template)
            row.update(
                model_dataset_version=model_info["dataset_version"],
                model_created_at=model_info["created_at"],
            )
            current = fresh(template, ref, model_info)
            request_path, request_pin = _freeze(
                current,
                report.parent / "inputs" / name / "resolution",
                pins / "inputs" / name / "resolution",
            )
            row.update(request=str(request_path), request_pin_file=str(request_pin))
            initial_proof = verify(current, Path(ref["journal"]), head).to_dict()
            row.update(
                source_pin_verified=initial_proof["predictions_verified"] > 0,
                journal_verified=True,
                stage="record",
            )
            if name == d["active_v2"]:
                # A new-input block must not suppress older Pending resolution.
                try:
                    require(isinstance(latest, str), "cycle_no_available_input")
                    assert isinstance(latest, str)
                    days, calendar_pin = _calendar(template)
                    sessions = sorted(day for day, state in days.items() if state == "session")
                    require(
                        latest in sessions and sessions.index(latest) + 2 < len(sessions),
                        "cycle_next_target_calendar_missing",
                    )
                    target = sessions[sessions.index(latest) + 2]
                    candidate = fresh(template, ref, model_info, target=target)
                    history, _ = _horizon(candidate, model_info, days)
                    require(history[-1] == latest, "cycle_latest_input_mismatch")
                    previous = events(Path(ref["journal"]), head)
                    done = set()
                    for e in previous:
                        p = e["payload"]
                        if (
                            e["kind"] == "prediction"
                            and p["input_latest_trading_date"] == latest
                            and p["target_trading_date"] == target
                            and p["model"]["identifier"] == model_info["identifier"]
                            and p["calendar_pin"] == calendar_pin
                        ):
                            done.add(p["ticker"])
                            row["duplicates"] += 1
                        elif (
                            e["kind"] == "failure"
                            and p["target_trading_date"] == target
                            and p["model_identifier"] == model_info["identifier"]
                            and p["required_input_dates"] == history
                            and p["reason"]
                            in (
                                "shadow_no_directional_context",
                                "shadow_model_direction_unavailable",
                            )
                        ):
                            done.add(p["ticker"])
                            row["blocked"] += 1
                            row["reasons"].append(p["reason"])
                    if set(candidate["tickers"]) <= done:
                        row["recording"] = "input_already_processed"
                    elif timestamp(clock()).astimezone(SEOUL).date().isoformat() >= target:
                        row["blocked"] += 1
                        row["reasons"].append("shadow_target_not_future")
                    else:
                        cp = next((r for r in completions if r["day"] == latest), None)
                        require(
                            cp is not None or candidate["source_kind"] == "offline_fixture",
                            "cycle_verified_collection_missing",
                        )
                        candidate_path, candidate_pin = _freeze(
                            candidate,
                            report.parent / "inputs" / name / "recording",
                            pins / "inputs" / name / "recording",
                        )
                        collection = FrozenJSON.freeze(
                            dict(version="krx_scheduler_run_v1", outcome="succeeded", day=latest)
                        )
                        row["journal_write_outcome"] = "inspect_retained_checkpoints_and_intents"
                        handoff = after_collection(
                            config,
                            collection,
                            candidate_path,
                            candidate_pin,
                            Path(ref["journal"]),
                            head_file,
                            Path(ref["intents"]),
                            Path(ref["pins_root"]),
                            clock=clock,
                        ).to_dict()
                        row["handoff"] = handoff
                        recording = handoff["research"].get("recording")
                        if recording is None:
                            row.update(
                                journal_write_outcome=handoff["research"].get(
                                    "journal_write_outcome", "not_attempted"
                                )
                            )
                            require(
                                row["journal_write_outcome"] == "not_attempted",
                                "cycle_record_checkpoint_unknown",
                            )
                            raise ValueError(
                                handoff["research"].get("reason", "cycle_record_failed")
                            )
                        head = recording["head_pin"]
                        head_file = Path(ref["pins_root"]) / ("head-" + head + ".pin")
                        require(read_pin(head_file) == head, "cycle_acknowledged_head_missing")
                        row.update(
                            head_pin_file=str(head_file),
                            journal_write_outcome="acknowledged",
                            new_predictions=sum(
                                r["status"] == "recorded" and not r["duplicate"]
                                for r in recording["results"]
                            ),
                            blocked=recording["blocked"],
                            duplicates=sum(r.get("duplicate", False) for r in recording["results"]),
                        )
                        row["summary"] = status(Path(ref["journal"]), head).to_dict()
                        row["reasons"] += [
                            r["reason"] for r in recording["results"] if r["status"] == "blocked"
                        ]
                        derived = handoff["research"].get("derived_input")
                        actual = document(derived["request"]) if derived else candidate
                        current = current | {"operational": actual["operational"]}
                        request_path, request_pin = _freeze(
                            current,
                            report.parent / "inputs" / name / "resolution-after-record",
                            pins / "inputs" / name / "resolution-after-record",
                        )
                        row.update(request=str(request_path), request_pin_file=str(request_pin))
                except Exception as error:
                    if row["journal_write_outcome"] in (
                        "inspect_retained_checkpoints_and_intents",
                        "acknowledged",
                    ):
                        raise
                    row["blocked"] += 1
                    row["reasons"].append(_reason(error))
            row["stage"] = "resolve"
            # Current references cover all original Pending windows; target stays original.
            resolution = resolve(
                current,
                Path(ref["journal"]),
                head,
                Path(ref["intents"]),
                Path(ref["pins_root"]),
                clock=clock,
            ).to_dict()
            next_head = resolution["head_pin"]
            if next_head != head:
                head_file = Path(ref["pins_root"]) / ("head-" + next_head + ".pin")
                require(read_pin(head_file) == next_head, "cycle_acknowledged_head_missing")
            row.update(
                head_pin_file=str(head_file),
                journal_write_outcome="acknowledged",
                stage="verify",
                summary=resolution,
            )
            readiness = evaluation_readiness(
                current, Path(ref["journal"]), next_head, config=config, clock=clock
            ).to_dict()
            row.update(
                status="completed_with_blocks" if row["blocked"] else "completed",
                stage="complete",
                journal_verified=True,
                source_pin_verified=readiness["predictions_verified"] > 0,
                verified_at=readiness["checked_at"],
                readiness=readiness,
                summary=resolution,
                counts_from_journal_only=False,
            )
        except Exception as error:
            row.update(
                status="blocked",
                blocked=row["blocked"] + 1,
                journal_verified=False,
                source_pin_verified=False,
            )
            row["reasons"].append(_reason(error))
            if row["stage"] in ("record", "resolve"):
                row["journal_write_outcome"] = "inspect_retained_checkpoints_and_intents"
            if row["journal_write_outcome"] == "acknowledged":
                row["summary_is_last_acknowledged_checkpoint"] = True
            if row["journal_write_outcome"] == "inspect_retained_checkpoints_and_intents":
                row["new_predictions"] = None
                row["summary_is_last_acknowledged_checkpoint"] = True
    availability = None
    availability_error = None
    if latest in verified:
        try:
            completion = next(c for c in completions if c["day"] == latest)
            source = CaptureSource(
                config.path("capture_root"),
                ForwardCapturePlan.from_dict(completion["plan"]),
                completion["plan_pin"],
                completion["attempt"],
                completion["capture_pin"],
            )
            availability = assess_availability(
                config, source, timestamp(clock()), batch_pin=completion["batch_pin"]
            ).to_dict()
        except Exception as error:
            availability_error = _reason(error)
            for row in results.values():
                row.update(status="blocked", source_pin_verified=False)
                row["blocked"] += 1
                row["reasons"].append(availability_error)
    finished = timestamp(clock())
    require(started <= finished, "cycle_clock_order")
    stable = True
    try:
        _quiescent(database, database_pin)
    except ValueError:
        stable = False
        for row in results.values():
            row.update(status="blocked", source_pin_verified=False)
            row["blocked"] += 1
            row["reasons"].append("cycle_source_changed_during_execution")
    counts = {}
    for version in ("research_shadow_request_v1", "research_shadow_request_v2"):
        selected = [r for r in results.values() if r["contract"] == version]
        counts[version] = {
            group: {
                key: None
                if any(r.get("summary_is_last_acknowledged_checkpoint") for r in selected)
                else sum(r["summary"]["groups"][group][key] for r in selected)
                for key in ("predictions", "pending", "evaluated", "unavailable")
            }
            for group in ("research_forward_candidate", "offline_fixture_rehearsal")
        }
    result = FrozenJSON.freeze(
        dict(
            version="research_cycle_report_v1",
            request_pin=digest(d),
            started_at=started.isoformat(),
            checked_at=finished.isoformat(),
            operational=market,
            availability=availability,
            availability_error=availability_error,
            latest_available_source_date=latest,
            verified_collections=verified,
            streams=results,
            totals_by_contract=counts,
            new_actual_forward_candidates=None
            if any(
                r["new_predictions"] is None and r["source_kind"] == "local_provider_capture"
                for r in results.values()
            )
            else sum(
                r["new_predictions"]
                for r in results.values()
                if r["source_kind"] == "local_provider_capture"
            ),
            new_fixture_predictions=None
            if any(
                r["new_predictions"] is None and r["source_kind"] == "offline_fixture"
                for r in results.values()
            )
            else sum(
                r["new_predictions"]
                for r in results.values()
                if r["source_kind"] == "offline_fixture"
            ),
            last_original_verified_at=max(
                [r["verified_at"] for r in results.values() if r.get("verified_at")]
                + ([availability["verified_at"]] if availability else []),
                default=None,
            ),
            new_predictions=None
            if any(r["new_predictions"] is None for r in results.values())
            else sum(r["new_predictions"] for r in results.values()),
            blocked=sum(r["blocked"] for r in results.values()),
            collection_changed=False,
            source_unchanged_during_cycle=stable,
            source_verification_scope="owned_prediction_and_outcome_windows_not_global_database",
            automatic_execution_enabled=False,
            official_first_publication_time="UNKNOWN",
            pit_qualified=False,
            genuine_forward_evidence=False,
            independent_publication_time_verified=False,
            performance_approved=False,
            performance_metrics=None,
            execution_authorized=False,
            network_requests=0,
            source_consistency="separate_read_transactions_and_retained_pins_not_atomic",
        )
    )
    write_new(report, result.payload_json)
    write_new(pins / "report.pin", result.identifier + "\n")
    return result


def _reason(error: Exception) -> str:
    text = str(error)
    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", text) else "cycle_stage_failed"
