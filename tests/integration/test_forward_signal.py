"""Offline synthetic original-capture -> crossover -> immutable local journal tests."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import getcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.cli import main
from donghak_stock_vision.forward_signal_demo_cli import prepare
from donghak_stock_vision.storage.forward_capture import DB
from donghak_stock_vision.storage.forward_signal import generate_signal, verify_signal
from donghak_stock_vision.storage.forward_signal_journal import GENESIS, append_signal, history
from donghak_stock_vision.strategy.forward_reference import calculate

NOW = datetime(2025, 7, 10, tzinfo=UTC)
CALCULATED = NOW + timedelta(seconds=2)


class CaptureClock:
    """Explicit synthetic clock; never an authentic publication or custody fixture."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return NOW


class IngestionClock:
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return NOW + timedelta(seconds=1)


def make(root: Path, closes: tuple[int, ...] = (100, 90, 80, 120)) -> FrozenJSON:
    # Original capture receives its explicit clock; only the test audit clock is frozen.
    from donghak_stock_vision.storage.forward_capture import run_capture

    def fixed(*args: Any, **kwargs: Any) -> FrozenJSON:
        return run_capture(*args, **kwargs, clock=lambda: NOW, sleep=lambda _: None)

    with (
        patch("donghak_stock_vision.forward_signal_demo_cli.run_capture", side_effect=fixed),
        patch("donghak_stock_vision.storage.sqlite.datetime", IngestionClock),
        patch("donghak_stock_vision.forward_signal_demo_cli.datetime", CaptureClock),
    ):
        request = prepare(root, closes).to_dict()
    request["calculated_at"] = CALCULATED.isoformat()
    return FrozenJSON.freeze(request)


def changed(request: FrozenJSON, **updates: Any) -> FrozenJSON:
    return FrozenJSON.freeze(request.to_dict() | updates)


def repin(root: Path, request: FrozenJSON) -> FrozenJSON:
    return changed(request, database_pin=hashlib.sha256((root / DB).read_bytes()).hexdigest())


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, FrozenJSON]:
    root = tmp_path / "capture"
    return root, make(root)


@pytest.mark.parametrize(
    "closes,outcome",
    [((100, 90, 80, 120), "BUY"), ((100, 110, 120, 80), "SELL"), ((100, 100, 100, 100), "HOLD")],
)
def test_actual_capture_crossover_and_semantic_replay(
    tmp_path: Path, closes: tuple[int, ...], outcome: str
) -> None:
    root = tmp_path / "capture"
    request = make(root, closes)
    decision = generate_signal(root, request, request.identifier)
    doc = decision.to_dict()
    assert doc["calculation"]["signal"] == outcome
    assert len(doc["ordered_observation_ids"]) == 4
    assert doc["origin"] == "offline_fixture"
    assert not doc["eligibility"]["historical_pit_qualified"]
    assert not doc["eligibility"]["execution_authorized"]
    assert doc["inputs"]["observations"][0]["bar"]["close"] == closes[0]
    assert doc["inputs"]["observations"][0]["exchange_publication_at"] is None
    assert (
        doc["inputs"]["observations"][0]["local_ingested_at"]
        == (NOW + timedelta(seconds=1)).isoformat()
    )
    verify_signal(root, request, request.identifier, decision, decision.identifier)
    if outcome == "BUY":
        indicators = doc["calculation"]["indicators"]
        assert indicators["previous_fast"] == {
            "sum": 170,
            "count": 2,
            "mean": "85.000000000000000000",
        }
        assert indicators["previous_slow"]["sum"] == 270
        assert indicators["current_fast"]["sum"] == 200
        assert indicators["current_slow"]["sum"] == 290


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_insufficient_data(inputs: tuple[Path, FrozenJSON], count: int) -> None:
    root, req = inputs
    req = changed(req, sources=req.to_dict()["sources"][:count])
    calc = generate_signal(root, req, req.identifier).to_dict()["calculation"]
    assert calc == {
        "signal": "INSUFFICIENT_DATA",
        "reason": "slow_plus_one_observations_required",
        "required_observations": 4,
        "indicators": None,
    }


@pytest.mark.parametrize("fast,slow", [(0, 3), (3, 3), (2, 31), (True, 3), (1, 2.0)])
def test_parameter_bounds(fast: int, slow: int) -> None:
    with pytest.raises(ValueError, match="sma_parameters"):
        calculate((1, 2, 3), fast, slow)


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"calculated_at": NOW.isoformat()}, "ingestion_time"),
        ({"calculated_at": (NOW - timedelta(seconds=1)).isoformat()}, "capture_time"),
        ({"calculated_at": (NOW + timedelta(days=8)).isoformat()}, "stale"),
        ({"calculated_at": "2025-07-10T09:00:02+09:00"}, "canonical_utc"),
        ({"calculated_at": "2025-07-10T00:00:02"}, "timezone_required"),
        ({"end": "2025-07-10"}, "future_observation_window"),
        ({"start": "2025-07-02"}, "source_outside_requested_window"),
        ({"max_latest_age_seconds": 604801}, "age_budget"),
    ],
)
def test_temporal_and_window_guards(
    inputs: tuple[Path, FrozenJSON], updates: dict[str, Any], reason: str
) -> None:
    root, req = inputs
    req = changed(req, **updates)
    with pytest.raises(ValueError, match=reason):
        generate_signal(root, req, req.identifier)


def test_source_ordering_and_duplicates(inputs: tuple[Path, FrozenJSON]) -> None:
    root, req = inputs
    sources = req.to_dict()["sources"]
    for bad in (list(reversed(sources)), sources + [sources[0]]):
        bad_req = changed(req, sources=bad)
        with pytest.raises(ValueError, match="unordered_source_dates"):
            generate_signal(root, bad_req, bad_req.identifier)


@pytest.mark.parametrize("partition", ["final_test", "final_holdout", "oos"])
def test_partition_rejected_before_any_source_loading(
    inputs: tuple[Path, FrozenJSON], partition: str
) -> None:
    _, req = inputs
    d = req.to_dict()
    d["sources"][-1]["plan"]["partition"] = partition
    req = FrozenJSON.freeze(d)
    with patch(
        "donghak_stock_vision.storage.forward_signal._file_hash",
        side_effect=AssertionError("disk read"),
    ):
        with pytest.raises(ValueError, match="forbidden_partition"):
            generate_signal(Path("/absent"), req, req.identifier)


def test_authorized_provider_not_faked_by_local_reference(inputs: tuple[Path, FrozenJSON]) -> None:
    root, req = inputs
    d = req.to_dict()
    d["mode"] = "authorized_provider"
    from donghak_stock_vision.data.forward_capture import ForwardCapturePlan

    for source in d["sources"]:
        source["plan"].update(
            mode="authorized_provider",
            authorization_reference="not-independent-proof",
            terms_reference="not-independent-proof",
        )
        source["plan_pin"] = ForwardCapturePlan.from_dict(source["plan"]).document.identifier
    req = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="authorization_not_independently_verified"):
        generate_signal(root, req, req.identifier)
    d["mode"] = "offline_fixture"
    req = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="capture_origin"):
        generate_signal(root, req, req.identifier)


@pytest.mark.parametrize(
    "sql,args",
    [
        ("UPDATE raw_pages SET body=?", (b"tampered",)),
        ("DELETE FROM raw_capture_receipts", ()),
        ("DELETE FROM forward_manifests", ()),
        ("UPDATE bars SET payload=?", ("{}",)),
        ("UPDATE collection_runs SET finished_at=?", ((NOW - timedelta(days=1)).isoformat(),)),
        ("UPDATE forward_responses SET raw_id=9999 WHERE raw_id=1", ()),
    ],
)
def test_original_source_tampering(
    inputs: tuple[Path, FrozenJSON], sql: str, args: tuple[Any, ...]
) -> None:
    root, req = inputs
    with closing(sqlite3.connect(root / DB)) as c, c:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.execute(sql, args)
    with pytest.raises(ValueError, match="database_pin"):
        generate_signal(root, req, req.identifier)
    # A new self-computed DB hash cannot bypass original capture semantic verification.
    bad = repin(root, req)
    with pytest.raises((ValueError, KeyError, TypeError)):
        generate_signal(root, bad, bad.identifier)


def test_independent_pins_and_request_identity(inputs: tuple[Path, FrozenJSON]) -> None:
    root, req = inputs
    with pytest.raises(ValueError, match="request_pin"):
        generate_signal(root, changed(req, fast=1), req.identifier)
    d = req.to_dict()
    d["sources"][0]["capture_pin"] = "0" * 64
    req = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="capture_pin"):
        generate_signal(root, req, req.identifier)


def test_rehashed_forged_decision(inputs: tuple[Path, FrozenJSON]) -> None:
    root, req = inputs
    good = generate_signal(root, req, req.identifier)
    d = good.to_dict()
    d["calculation"]["signal"] = "SELL"
    forged = FrozenJSON.freeze(d)
    with pytest.raises(ValueError, match="semantic_mismatch"):
        verify_signal(root, req, req.identifier, forged, forged.identifier)


def test_deterministic_json_and_decimal_context(inputs: tuple[Path, FrozenJSON]) -> None:
    root, req = inputs
    good = generate_signal(root, req, req.identifier)
    previous = getcontext().prec
    try:
        getcontext().prec = 6
        assert generate_signal(root, req, req.identifier) == good
    finally:
        getcontext().prec = previous
    assert good.to_dict()["version"] == "forward_signal_decision_v1"
    assert FrozenJSON(good.payload_json) == good


def test_append_readback_idempotency_and_source_readonly(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    before = (root / DB).read_bytes()
    path = tmp_path / "signals.sqlite3"
    first = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED)
    head = first.to_dict()["head_pin"]
    duplicate = append_signal(path, root, req, req.identifier, head, CALCULATED + timedelta(days=1))
    assert duplicate.to_dict()["duplicate"]
    assert duplicate.to_dict()["head_pin"] == head
    records = history(path, head).to_dict()
    assert records["total"] == 1
    assert (
        records["entries"][0]["entry"]["decision_pin"]
        == generate_signal(root, req, req.identifier).identifier
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert (root / DB).read_bytes() == before
    with closing(sqlite3.connect(path)) as c:
        for command in ("DELETE FROM signal_entries", "UPDATE signal_entries SET signal_id='x'"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(command)


def test_same_identity_conflict_is_rejected(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    head = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED).to_dict()["head_pin"]
    forged = generate_signal(root, req, req.identifier).to_dict()
    forged["calculation"]["signal"] = "SELL"
    # Simulate a same-ID producer defect; never overwrite the earlier decision.
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal.generate_signal",
        return_value=FrozenJSON.freeze(forged),
    ):
        with pytest.raises(ValueError, match="same_signal_id_different_content"):
            append_signal(path, root, req, req.identifier, head, CALCULATED)
    assert history(path, head).to_dict()["total"] == 1


def test_interrupted_append_rolls_back_and_retries(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    with patch(
        "donghak_stock_vision.storage.forward_signal_journal._commit", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            append_signal(path, root, req, req.identifier, GENESIS, CALCULATED)
    assert history(path, GENESIS).to_dict()["total"] == 0
    head = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED).to_dict()["head_pin"]
    assert history(path, head).to_dict()["total"] == 1


def test_journal_chain_truncation_and_stale_pin(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    head = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED).to_dict()["head_pin"]
    other = changed(req, fast=1)
    head2 = append_signal(path, root, other, other.identifier, head, CALCULATED).to_dict()[
        "head_pin"
    ]
    assert history(path, head2).to_dict()["total"] == 2
    with pytest.raises(ValueError, match="head_pin"):
        append_signal(path, root, req, req.identifier, head, CALCULATED)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("DROP TRIGGER signal_entries_no_delete")
        c.execute("DELETE FROM signal_entries WHERE sequence=2")
        c.execute(
            "CREATE TRIGGER signal_entries_no_delete BEFORE DELETE ON signal_entries "
            "BEGIN SELECT RAISE(ABORT,'signal journal immutable'); END"
        )
    with pytest.raises(ValueError, match="head_pin"):
        history(path, head2)


def test_journal_budget_and_wrong_database(inputs: tuple[Path, FrozenJSON], tmp_path: Path) -> None:
    root, req = inputs
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="not_a_signal_journal"):
        append_signal(root / DB, root, req, req.identifier, GENESIS, CALCULATED)
    assert (root / DB).read_bytes() == before
    path = tmp_path / "signals"
    with patch("donghak_stock_vision.storage.forward_signal_journal.MAX_ENTRIES", 0):
        with pytest.raises(ValueError, match="capacity_exhausted"):
            append_signal(path, root, req, req.identifier, GENESIS, CALCULATED)
    assert history(path, GENESIS).to_dict()["total"] == 0


def test_cli_workflow_no_implicit_network_or_orders(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, req = inputs
    file = tmp_path / "request.json"
    file.write_text(req.payload_json)
    journal = tmp_path / "signals"
    args = ["--capture-root", str(root), "--request", str(file), "--request-pin", req.identifier]
    with (
        patch("httpx.Client.__init__", side_effect=AssertionError("implicit HTTP client")),
        patch(
            "donghak_stock_vision.trading.paper.DeterministicPaperBroker.submit",
            side_effect=AssertionError("order"),
        ),
    ):
        assert main(["forward-signal", "dry-run", *args]) == 0
        dry = json.loads(capsys.readouterr().out)
        assert not dry["journal_written"] and not journal.exists()
        journal_args = ["--journal", str(journal), "--journal-pin", GENESIS]
        assert main(["forward-signal", "evaluate", *args, *journal_args]) == 0
        first = json.loads(capsys.readouterr().out)
        journal_args[-1] = first["head_pin"]
        assert main(["forward-signal", "history", *journal_args]) == 0
        assert json.loads(capsys.readouterr().out)["total"] == 1
        assert (
            main(
                [
                    "forward-signal",
                    "verify",
                    *args,
                    *journal_args,
                    "--signal-id",
                    dry["decision"]["signal_id"],
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["semantic_replay_verified"]


def test_missing_archive_and_empty_interrupted_journal(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    _, req = inputs
    with pytest.raises(ValueError, match="pilot_archive_missing"):
        generate_signal(tmp_path / "absent", req, req.identifier)
    path = tmp_path / "interrupted"
    path.touch()
    with pytest.raises(ValueError, match="interrupted_initialization"):
        history(path, GENESIS)
    assert path.stat().st_size == 0


def test_old_decisions_preserved_and_history_bounded(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    first = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED).to_dict()
    second_req = changed(req, fast=1)
    second = append_signal(
        path, root, second_req, second_req.identifier, first["head_pin"], CALCULATED
    ).to_dict()
    all_records = history(path, second["head_pin"]).to_dict()
    assert all_records["entries"][0] == first["entry"]
    limited = history(path, second["head_pin"], limit=1).to_dict()
    assert limited["total"] == 2 and limited["entries"] == [second["entry"]]
    with pytest.raises(ValueError, match="history_limit"):
        history(path, second["head_pin"], limit=257)


def test_journal_payload_tamper_detected(inputs: tuple[Path, FrozenJSON], tmp_path: Path) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    appended = append_signal(path, root, req, req.identifier, GENESIS, CALCULATED).to_dict()
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("DROP TRIGGER signal_entries_no_update")
        d = appended["entry"]["entry"]
        d["decision"]["calculation"]["signal"] = "SELL"
        c.execute("UPDATE signal_entries SET document=?", (FrozenJSON.freeze(d).payload_json,))
        c.execute(
            "CREATE TRIGGER signal_entries_no_update BEFORE UPDATE ON signal_entries "
            "BEGIN SELECT RAISE(ABORT,'signal journal immutable'); END"
        )
    with pytest.raises(ValueError, match="journal_integrity"):
        history(path, appended["head_pin"])


def test_completed_commit_unknown_response_recovers_without_duplicate(
    inputs: tuple[Path, FrozenJSON], tmp_path: Path
) -> None:
    root, req = inputs
    path = tmp_path / "signals"
    from donghak_stock_vision.storage.forward_signal_journal import _commit

    def commit_then_interrupt(c: sqlite3.Connection) -> None:
        _commit(c)
        raise KeyboardInterrupt

    with patch(
        "donghak_stock_vision.storage.forward_signal_journal._commit",
        side_effect=commit_then_interrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            append_signal(path, root, req, req.identifier, GENESIS, CALCULATED)
    # Unknown outcome fails closed with the old trusted pin. Operator recovery uses
    # original entry bytes and a separate semantic replay before adopting any new pin.
    with pytest.raises(ValueError, match="head_pin"):
        append_signal(path, root, req, req.identifier, GENESIS, CALCULATED)
    with closing(sqlite3.connect(path)) as c:
        document, head = c.execute("SELECT document,entry_pin FROM signal_entries").fetchone()
    entry = FrozenJSON(document).to_dict()
    verify_signal(
        root, req, req.identifier, FrozenJSON.freeze(entry["decision"]), entry["decision_pin"]
    )
    duplicate = append_signal(path, root, req, req.identifier, head, CALCULATED)
    assert duplicate.to_dict()["duplicate"]
    assert history(path, head).to_dict()["total"] == 1


def test_fresh_archive_reproduction_with_controlled_synthetic_clocks(tmp_path: Path) -> None:
    root1, root2 = tmp_path / "one", tmp_path / "two"
    a, b = make(root1), make(root2)
    assert a == b
    assert generate_signal(root1, a, a.identifier) == generate_signal(root2, b, b.identifier)


@pytest.mark.parametrize("updates", [{"start": None}, {"calculated_at": None}, {"sources": [None]}])
def test_malformed_request_fails_before_disk(
    inputs: tuple[Path, FrozenJSON], updates: dict[str, Any]
) -> None:
    _, req = inputs
    req = changed(req, **updates)
    with patch(
        "donghak_stock_vision.storage.forward_signal._file_hash", side_effect=AssertionError("disk")
    ):
        with pytest.raises(ValueError):
            generate_signal(Path("/absent"), req, req.identifier)
