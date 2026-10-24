"""Offline synthetic archived KRX fixtures: no authentic PIT or replay approval exists."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import (
    CalendarMonthSource,
    HistoricalReadScope,
)
from donghak_stock_vision.backtest.official_evidence import normalize_month
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.providers.base import ProviderError, RawPage
from donghak_stock_vision.storage.historical_readiness import (
    _OfflineKRXParser,
    controlled_historical_replay,
    inspect_historical_market,
    verify_historical_readiness,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.backtest.test_official_evidence import month
from tests.trading.test_risk import KR, US


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fixture(tmp_path: Path) -> tuple[Path, HistoricalReadScope, tuple[CalendarMonthSource, ...]]:
    path = tmp_path / "market.sqlite3"
    store = SQLiteStore(path)
    try:
        for day in (1, 2, 3):
            raw = json.dumps(
                {
                    "OutBlock_1": [
                        {
                            "ISU_CD": KR.symbol,
                            "BAS_DD": f"2025070{day}",
                            "MKT_NM": "KOSPI",
                            "TDD_OPNPRC": "100",
                            "TDD_HGPRC": "110",
                            "TDD_LWPRC": "90",
                            "TDD_CLSPRC": "105",
                            "ACC_TRDVOL": "10",
                            "ACC_TRDVAL": "1050",
                        }
                    ]
                }
            ).encode()
            page = RawPage(raw, datetime(2026, 1, 5, tzinfo=UTC), date(2025, 7, day))
            raw_id = store.archive(
                "fixture-only-not-authentic-krx", "krx", "@krx_market:KOSPI", page
            )
            store.save(_OfflineKRXParser("KOSPI").parse_market(page), [raw_id])
    finally:
        store.close()
    split = ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 3)),
        ResearchDateRange(date(2025, 7, 4), date(2025, 7, 4)),
        ResearchDateRange(date(2025, 7, 7), date(2025, 7, 7)),
    )
    scope = HistoricalReadScope(
        KR, split.training, "training", split, split.identifier, file_hash(path)
    )
    raw, source = month(2025, 7)
    normalized = normalize_month(raw, source)
    calendar = CalendarMonthSource(raw, FrozenJSON.freeze(source), normalized.identifier)
    return path, scope, (calendar,)


def inspect(
    path: Path, scope: HistoricalReadScope, calendars: tuple[CalendarMonthSource, ...] = ()
) -> Any:
    return inspect_historical_market(path, scope, scope.document.identifier, calendars)


def mutate(
    path: Path, scope: HistoricalReadScope, sql: str, args: tuple[Any, ...] = ()
) -> HistoricalReadScope:
    with sqlite3.connect(path) as c:
        c.execute(sql, args)
    return replace(scope, trusted_database_hash=file_hash(path))


def test_inventory_provenance_projection_and_blocked_replay(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    before = file_hash(path)
    result = inspect(path, scope, calendars)
    d = result.report.to_dict()
    assert d["classification"] == "provenance_replayed_missing_independent_qualification"
    assert d["scoped_rows"] == d["inventory"]["bars"]["count"] == 3
    assert d["missing_expected_sessions"] == []
    assert d["price_semantics"] == ["unadjusted"]
    assert "corporate_action_coverage_not_verified" in d["blockers"]
    assert "strict_pit_qualification_unavailable" in d["blockers"]
    assert all(
        r["observation_at"] is None and r["available_at"] is None
        for r in result.observations.to_dict()["records"]
    )
    assert result.observations.to_dict()["canonical_market_quotes"] is None
    verify_historical_readiness(
        result, d["content_fingerprint"], path, scope, scope.document.identifier, calendars
    )
    blocked = controlled_historical_replay(
        result, d["content_fingerprint"], path, scope, scope.document.identifier, calendars
    ).to_dict()
    assert blocked["status"] == "blocked" and blocked["orders"] == blocked["fills"] == 0
    assert file_hash(path) == before
    assert not Path(str(path) + "-wal").exists()
    assert inspect(path, scope, calendars) == result


def test_missing_provenance(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    scope = mutate(path, scope, "DELETE FROM bar_sources")
    value = inspect(path, scope, calendars).report.to_dict()
    assert value["classification"] == "structurally_valid_missing_original_provenance"
    assert value["missing_provenance"] == ["original_raw_source_missing"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("close", 106, "cleaned_bar_not_reproduced_from_original"),
        ("adjustment", "adjusted", "cleaned_bar_not_reproduced_from_original"),
        ("adjustment", "unknown", "cleaned_bar_not_reproduced_from_original"),
        ("market", "KOSDAQ", "invalid_bar_or_source_identity"),
        ("provider", "fake", "invalid_bar_or_source_identity"),
        ("ticker", "000660", "invalid_bar_or_source_identity"),
        ("available_at", "2024-01-01T00:00:00Z", "invalid_bar_or_source_identity"),
        ("available_at", "2099-01-01T00:00:00Z", "invalid_bar_or_source_identity"),
    ],
)
def test_tampered_values_do_not_become_pit_inputs(
    tmp_path: Path, field: str, value: Any, reason: str
) -> None:
    path, scope, calendars = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        doc = json.loads(c.execute("SELECT payload FROM bars LIMIT 1").fetchone()[0])
    doc[field] = value
    scope = mutate(
        path,
        scope,
        "UPDATE bars SET payload=? WHERE trading_date=?",
        (json.dumps(doc), "2025-07-01"),
    )
    d = inspect(path, scope, calendars).report.to_dict()
    assert d["classification"] == "structurally_invalid" and reason in d["invalid_reasons"]
    assert not d["eligible_for_historical_replay"]
    if field == "adjustment":
        assert "adjustment_information_time_and_action_evidence_missing" in d["blockers"]


def test_wrong_raw_source_and_hash(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    scope = mutate(path, scope, "UPDATE raw_pages SET sha256=?", ("0" * 64,))
    d = inspect(path, scope, calendars).report.to_dict()
    assert "raw_source_hash_mismatch" in d["invalid_reasons"]


def test_source_cohort_mismatch(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    scope = mutate(path, scope, "UPDATE raw_pages SET ticker='@krx_market:KOSDAQ'")
    assert (
        "raw_source_scope_mismatch"
        in inspect(path, scope, calendars).report.to_dict()["invalid_reasons"]
    )


def test_missing_expected_session(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    scope = mutate(path, scope, "DELETE FROM bars WHERE trading_date='2025-07-02'")
    value = inspect(path, scope, calendars).report.to_dict()
    assert value["missing_expected_sessions"] == ["2025-07-02"]
    assert "missing_expected_sessions" in value["invalid_reasons"]


def test_duplicate_sessions(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE bars RENAME TO old_bars")
        c.execute("CREATE TABLE bars AS SELECT * FROM old_bars")
        c.execute("INSERT INTO bars SELECT * FROM old_bars LIMIT 1")
    scope = replace(scope, trusted_database_hash=file_hash(path))
    assert (
        "duplicate_observation_sessions"
        in inspect(path, scope, calendars).report.to_dict()["invalid_reasons"]
    )


def test_calendar_pin_coverage_and_duplicates(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    with pytest.raises(ContractError, match="calendar_pin_mismatch"):
        inspect(path, scope, (replace(calendars[0], trusted_normalized_hash="0" * 64),))
    with pytest.raises(ContractError, match="duplicate_calendar_sessions"):
        inspect(path, scope, calendars * 2)
    raw, source = month(2025, 8)
    august = CalendarMonthSource(
        raw, FrozenJSON.freeze(source), normalize_month(raw, source).identifier
    )
    assert (
        "calendar_scope_incomplete"
        in inspect(path, scope, (august,)).report.to_dict()["invalid_reasons"]
    )
    assert "official_session_calendar_missing" in inspect(path, scope).report.to_dict()["blockers"]


@pytest.mark.parametrize("role", ["oos", "holdout", "final_test"])
def test_forbidden_access_before_open(tmp_path: Path, role: str) -> None:
    _, scope, _ = fixture(tmp_path)
    with pytest.raises(ContractError, match="historical_partition_forbidden"):
        replace(scope, partition=role)
    with pytest.raises(ContractError, match="outside_authorized_partition"):
        replace(scope, window=scope.split.final_test)


def test_substitution_bounds_and_live_journal(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    with pytest.raises(ContractError, match="database_pin_mismatch"):
        inspect(path, replace(scope, trusted_database_hash="0" * 64), calendars)
    with pytest.raises(ContractError, match="scoped_row_budget_exceeded"):
        inspect(path, replace(scope, max_rows=1), calendars)
    with pytest.raises(ContractError, match="raw_byte_budget_exceeded"):
        inspect(path, replace(scope, max_raw_bytes=1), calendars)
    Path(str(path) + "-wal").write_bytes(b"active")
    with pytest.raises(ContractError, match="active_database_journal_unsupported"):
        inspect(path, scope, calendars)


@pytest.mark.parametrize(
    "field", ["classification", "eligible_for_historical_replay", "raw_sources"]
)
def test_rehashed_report_fails_semantic_replay(tmp_path: Path, field: str) -> None:
    path, scope, calendars = fixture(tmp_path)
    result = inspect(path, scope, calendars)
    value = result.report.to_dict()
    value[field] = True
    value["content_fingerprint"] = digest(
        {k: v for k, v in value.items() if k != "content_fingerprint"}
    )
    with pytest.raises(ContractError, match="readiness_semantic_replay_mismatch"):
        controlled_historical_replay(
            replace(result, report=FrozenJSON.freeze(value)),
            value["content_fingerprint"],
            path,
            scope,
            scope.document.identifier,
            calendars,
        )


def test_no_network_no_broker_and_us_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not allowed during readiness")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr(
        "donghak_stock_vision.trading.paper.DeterministicPaperBroker.submit", forbidden
    )
    path, scope, calendars = fixture(tmp_path)
    with pytest.raises(ContractError, match="historical_us_source_unavailable"):
        replace(scope, instrument=US)
    result = inspect(path, scope, calendars)
    controlled_historical_replay(
        result,
        result.report.to_dict()["content_fingerprint"],
        path,
        scope,
        scope.document.identifier,
        calendars,
    )
    with pytest.raises(ProviderError, match="readiness_network_forbidden"):
        _OfflineKRXParser("KOSPI").fetch_market(date(2025, 7, 1))


def test_duplicate_raw_observations_are_not_provenance(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        raw_id, body = c.execute("SELECT id,body FROM raw_pages ORDER BY id LIMIT 1").fetchone()
    value = json.loads(body)
    value["OutBlock_1"] *= 2
    body = json.dumps(value).encode()
    scope = mutate(
        path,
        scope,
        "UPDATE raw_pages SET body=?,sha256=? WHERE id=?",
        (body, hashlib.sha256(body).hexdigest(), raw_id),
    )
    assert (
        "duplicate_raw_observations"
        in inspect(path, scope, calendars).report.to_dict()["invalid_reasons"]
    )


def test_calendar_wrong_market_and_raw_original_modified(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    kosdaq = replace(scope.instrument, exchange=type(scope.instrument.exchange).KOSDAQ)
    wrong = replace(scope, instrument=kosdaq)
    assert (
        "calendar_market_mismatch"
        in inspect(path, wrong, calendars).report.to_dict()["invalid_reasons"]
    )
    raw = calendars[0].raw + b"altered"
    with pytest.raises(ContractError, match="raw_hash_mismatch"):
        inspect(path, scope, (replace(calendars[0], raw=raw),))


def test_forged_observation_time_and_prohibited_window_before_io(tmp_path: Path) -> None:
    path, scope, calendars = fixture(tmp_path)
    result = inspect(path, scope, calendars)
    d = result.observations.to_dict()
    d["records"][0]["available_at"] = "2025-07-01T00:00:00Z"
    forged = replace(result, observations=FrozenJSON.freeze(d))
    with pytest.raises(ContractError, match="readiness_semantic_replay_mismatch"):
        verify_historical_readiness(
            forged,
            result.report.to_dict()["content_fingerprint"],
            path,
            scope,
            scope.document.identifier,
            calendars,
        )
    object.__setattr__(scope, "window", scope.split.final_test)
    with pytest.raises(ContractError, match="outside_authorized_partition"):
        inspect(Path("/does/not/exist"), scope, calendars)


def test_cross_partition_raw_body_is_never_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, scope, calendars = fixture(tmp_path)
    scope = mutate(path, scope, "UPDATE raw_pages SET source_date='2025-07-07'")
    queries: list[str] = []
    original = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = original(*args, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    result = inspect(path, scope, calendars)
    assert "raw_source_outside_authorized_observation" in result.report.to_dict()["invalid_reasons"]
    assert not any("SELECT body" in q or "length(body)" in q for q in queries)
