from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import MarketEvent
from donghak_stock_vision.backtest.data import FrozenJSON, FrozenTape, capture_market
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.backtest.helpers import T
from tests.backtest.time_helpers import CORRECTION, LATER, daily_tape, source, tape, tape_parts
from tests.learning.helpers import CUTOFF, bars, quality, seed


def test_field_release_and_equal_time_sequence() -> None:
    fixed = tape()
    before = fixed.view(VirtualClock.at(T, 0)).to_dict()
    opened = fixed.view(VirtualClock.at(T, 1)).to_dict()
    assert before["items"] == []
    assert opened["items"][0]["public_fields"] == {"open": "100"}
    assert opened["usage_restriction"] == "synthetic_test_only"
    assert not opened["executable"] and not opened["operational_eligible"]
    just_before = fixed.view(VirtualClock.at("2026-01-05T00:59:59.999999Z", 1))
    assert just_before.to_dict()["items"][0]["public_fields"] == {"open": "100"}
    at = fixed.view(VirtualClock.at(LATER, 1)).to_dict()
    assert at["items"][0]["public_fields"]["volume"] == 100
    assert "future_labels" not in str(at)
    assert "source" not in at["items"][0] and "tape_id" not in at


def test_future_prices_volume_labels_and_new_event_do_not_change_prefix() -> None:
    a = source()
    b = source()
    b["fields"].update(high="999", low="1", close="500", volume=500, trading_value="99999")
    b["original"]["future_labels"] = [0, 1, 1]
    original, changed = tape([a]), tape([b, source("r2", CORRECTION)])
    cursor = VirtualClock.at(T, 1)
    assert original.identifier != changed.identifier
    assert original.view(cursor).identifier == changed.view(cursor).identifier
    assert original.view(cursor).to_dict() == changed.view(cursor).to_dict()
    assert original.view(cursor).lineage != changed.view(cursor).lineage
    assert (
        original.view(VirtualClock.at(LATER, 1)).identifier
        != changed.view(VirtualClock.at(LATER, 1)).identifier
    )


def test_correction_visible_only_after_receipt_and_sequence() -> None:
    correction = source("r2", CORRECTION)
    correction["fields"]["close"] = "109"
    fixed = tape([source(), correction], "point_in_time")
    assert len(fixed.view(VirtualClock.at(CORRECTION, 1)).to_dict()["items"]) == 1
    after = fixed.view(VirtualClock.at(CORRECTION, 2)).to_dict()
    assert len(after["items"]) == 2
    assert after["items"][1]["public_fields"]["close"] == "109"
    assert after["information_mode"] == "point_in_time"


@pytest.mark.parametrize("revision,received", [("r1", CORRECTION), ("r2", T)])
def test_conflicting_or_backdated_revisions(revision: str, received: str) -> None:
    with pytest.raises(ContractError, match="revision"):
        tape([source(), source(revision, received)])


def test_duplicates_sequences_and_time_reversal() -> None:
    parts = tape_parts([source(), source("r2", CORRECTION)])
    e = parts["events"][1].to_dict()
    e["sequence"] = 1
    parts["events"] = (parts["events"][0], MarketEvent.from_dict(e))
    with pytest.raises(ContractError, match="sequence"):
        FrozenTape(**parts)
    parts = tape_parts([source(), source("r2", CORRECTION)])
    parts["events"] = tuple(reversed(parts["events"]))
    with pytest.raises(ContractError):
        FrozenTape(**parts)


def test_missing_evidence_and_changed_source_are_rejected() -> None:
    parts = tape_parts()
    parts["sources"] = ()
    with pytest.raises(ContractError, match="source_evidence_missing"):
        FrozenTape(**parts)
    parts = tape_parts()
    e = parts["events"][0].to_dict()
    e["public_fields"]["open"] = "101"
    parts["events"] = (MarketEvent.from_dict(e),)
    with pytest.raises(ContractError, match="source_value_mismatch"):
        FrozenTape(**parts)


def test_late_receipt_never_automatically_falls_back() -> None:
    late = source(received="2026-02-01T00:00:00Z")
    historical = tape([late])
    assert historical.events[0].to_dict()["source_received_at"].startswith("2026-02")
    with pytest.raises(ContractError, match="pit_receipt_after_availability"):
        tape([late], "point_in_time")


def test_freezing_detaches_source_and_view() -> None:
    raw = source()
    fixed = tape([raw])
    view = fixed.view(VirtualClock.at(LATER, 1))
    raw["fields"]["open"] = "999"
    detached = view.to_dict()
    detached["items"][0]["public_fields"]["open"] = "999"
    assert fixed.view(VirtualClock.at(LATER, 1)) == view
    with pytest.raises(FrozenInstanceError):
        fixed.events = ()  # type: ignore[misc]


def test_capture_read_only_boundary_and_db_deletion(tmp_path: Path) -> None:
    path = tmp_path / "market.db"
    store = SQLiteStore(path)
    rows = bars(count=2, tickers=1)
    seed(store, rows)
    q = FrozenJSON.freeze(quality(rows))
    captured = capture_market(
        store,
        ["000001"],
        rows[0].trading_date,
        rows[-1].trading_date,
        information_mode="historical_research",
        data_origin="synthetic",
        captured_at=CUTOFF,
        quality=q,
    )
    fixed = daily_tape(captured, q)
    wire = fixed.to_json()
    cursor = VirtualClock.at(fixed.manifest.to_dict()["start_at"], 1)
    view = fixed.view(cursor)
    original = tuple(s.identifier for s in captured)
    seed(
        store,
        [
            replace(
                rows[0],
                close=rows[0].close + 1,
                high=rows[0].high + 1,
                collected_at=rows[0].collected_at + timedelta(hours=1),
            )
        ],
    )
    store.close()
    path.unlink()
    assert tuple(s.identifier for s in captured) == original
    assert captured[0].to_dict()["original"] == rows[0].to_dict()
    assert tuple(FrozenJSON(s.payload_json) for s in captured) == captured
    replay = FrozenTape.from_json(wire)
    assert replay.identifier == fixed.identifier
    assert replay.view(cursor) == view


def test_pit_latest_store_rejected_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "market.db")

    def forbidden(*args: object) -> None:
        raise AssertionError("must not pretend latest rows reconstruct PIT history")

    monkeypatch.setattr(store, "read", forbidden)
    try:
        with pytest.raises(ContractError, match="pit_history_unavailable"):
            capture_market(
                store,
                ["000001"],
                date(2020, 1, 2),
                date(2020, 1, 3),
                information_mode="point_in_time",
                data_origin="synthetic",
                captured_at=CUTOFF,
                quality=FrozenJSON.freeze(quality(bars(2, 1))),
            )
    finally:
        store.close()


@pytest.mark.parametrize("bad", ["origin", "quality", "adjustment", "receipt"])
def test_capture_quality_and_provenance(tmp_path: Path, bad: str) -> None:
    store = SQLiteStore(tmp_path / "market.db")
    rows = bars(2, 1)
    if bad == "adjustment":
        rows = [replace(b, adjustment="adjusted") for b in rows]
    seed(store, rows)
    q = quality(rows)
    if bad == "quality":
        q["coverage"] = {}
    cutoff = datetime(2023, 1, 1, tzinfo=UTC) if bad == "receipt" else CUTOFF
    try:
        with pytest.raises(ValueError):
            capture_market(
                store,
                ["000001"],
                rows[0].trading_date,
                rows[-1].trading_date,
                information_mode="historical_research",
                data_origin="real" if bad == "origin" else "synthetic",
                captured_at=cutoff,
                quality=FrozenJSON.freeze(q),
            )
    finally:
        store.close()


def test_mixed_source_origin_and_unverified_adjustment() -> None:
    real = source()
    real["data_origin"] = "real"
    with pytest.raises(ContractError):
        tape([source(), real])
    adjusted = source()
    adjusted["adjustment"] = "adjusted"
    with pytest.raises(ContractError):
        tape([adjusted])


@pytest.mark.parametrize("case", ["missing", "naive", "early", "previous"])
def test_release_evidence_required_even_when_hashes_match(case: str) -> None:
    from donghak_stock_vision.backtest.contracts import ExecutionPolicy, RunManifest

    parts = tape_parts()
    p = parts["release_plan"].to_dict()
    release = p["releases"][0]
    if case == "missing":
        del release["field_times"]["close"]
    elif case == "naive":
        release["field_times"]["close"] = "2026-01-05T01:00:00"
    elif case == "early":
        release["field_times"]["close"] = "2026-01-04T23:59:59Z"
    else:
        release["previous_revision"] = "missing-revision"
    plan = FrozenJSON.freeze(p)
    policy = parts["policy"].to_dict()
    policy["availability_policy"]["content_hash"] = plan.identifier
    selected = ExecutionPolicy.from_dict(policy)
    m = parts["manifest"].to_dict()
    m.update(execution_policy_id=selected.identifier, availability_assumption_id=plan.identifier)
    run = RunManifest.from_dict(m)
    e = parts["events"][0].to_dict()
    e.update(run_id=run.identifier, availability_assumption_id=plan.identifier)
    with pytest.raises(ContractError):
        FrozenTape(
            run, selected, (MarketEvent.from_dict(e),), parts["sources"], plan, parts["quality"]
        )


def test_no_trade_daily_bar_never_becomes_tradable(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "market.db")
    row = replace(bars(1, 1)[0], open=0, high=0, low=0, volume=0, trading_value=0)
    seed(store, [row])
    q = FrozenJSON.freeze(quality([row]))
    try:
        captured = capture_market(
            store,
            [row.ticker],
            row.trading_date,
            row.trading_date,
            information_mode="historical_research",
            data_origin="synthetic",
            captured_at=CUTOFF,
            quality=q,
        )
    finally:
        store.close()
    fixed = daily_tape(captured, q)
    item = fixed.view(VirtualClock.at(fixed.manifest.to_dict()["start_at"], 1)).to_dict()["items"][
        0
    ]
    assert item["public_fields"]["volume"] == 0
    assert item["market_state"] == "not_execution_evidence"
    assert "tradable" not in item and "session" not in item


def test_evidence_duplicate_json_keys_rejected() -> None:
    with pytest.raises(ContractError, match="duplicate_evidence_key"):
        FrozenJSON('{"received_at":"past","received_at":"future"}')


def test_equivalent_offset_views_and_serialized_replay() -> None:
    fixed = tape()
    a = fixed.view(VirtualClock.at(T, 1))
    b = fixed.view(VirtualClock.at("2026-01-04T19:00:00-05:00", 1))
    assert a == b
    assert FrozenTape.from_json(fixed.to_json()).view(VirtualClock.at(T, 1)) == a
