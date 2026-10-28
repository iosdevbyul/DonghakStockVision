"""Synthetic full-market pages only. Never import the operator's pilot or recovery files."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_market_collection import row

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.cli import main
from donghak_stock_vision.providers.base import RawPage
from donghak_stock_vision.storage.capture_evidence_sink import (
    export_capture_receipt,
    verify_exported_capture_receipt,
)
from donghak_stock_vision.storage.operational_market import (
    DB,
    INELIGIBLE_FIELDS,
    OperationalMarketStore,
    StorageLimits,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore, archive_raw_page

DAY = date(2024, 1, 2)
NOW = datetime(2024, 1, 3, tzinfo=UTC)


def page(n: int = 3, *, market: str = "KOSPI", zipped: bool = False, **changes: Any) -> RawPage:
    rows = [
        row(
            f"{i + 1:06d}",
            ISU_NM=f"Synthetic instrument {i}",
            MKT_NM=market,
            ORIGINAL_EXTRA={"text": "원본", "value": "000001"},
            **changes,
        )
        for i in range(n)
    ]
    body = json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode()
    return RawPage(gzip.compress(body, mtime=0) if zipped else body, NOW, DAY)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "operational"
    OperationalMarketStore.initialize(path, StorageLimits(min_free_bytes=0))
    return path


def ingest(
    root: Path, batch: str = "batch", source: RawPage | None = None, **kwargs: Any
) -> FrozenJSON:
    with closing(OperationalMarketStore(root, writable=True)) as store:
        return store.ingest(
            batch,
            kwargs.pop("market", "KOSPI"),
            source or page(),
            source_kind="synthetic_fixture",
            **kwargs,
        )


def status(root: Path) -> dict[str, Any]:
    with closing(OperationalMarketStore(root)) as store:
        return store.status().to_dict()


def test_initialize_status_restart_and_private_layout(root: Path) -> None:
    assert {p.name for p in root.iterdir()} == {"database", "backups", "logs"}
    assert not (root / DB).stat().st_mode & 0o077
    for p in (root, root / "database", root / "logs", root / "backups"):
        assert not p.stat().st_mode & 0o077
    before = (root / DB).read_bytes()
    assert status(root)["observation_count"] == 0
    assert OperationalMarketStore.initialize(root).to_dict()["latest_trading_date"] is None
    assert (root / DB).read_bytes() == before
    result = ingest(root)
    assert result.to_dict()["eligibility"] == dict.fromkeys(INELIGIBLE_FIELDS, False)
    assert status(root)["observation_count"] == 3
    assert status(root)["latest_trading_date"] == DAY.isoformat()
    with closing(OperationalMarketStore(root)) as store:
        assert store.validate().to_dict()["valid"]
        assert store.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            store.connection.execute("DELETE FROM ingestion_batches")


def test_full_kospi_gzip_exact_raw_receipt_values_names_and_extra_fields(
    root: Path, tmp_path: Path
) -> None:
    raw = page(940, zipped=True)
    owner = ingest(root, source=raw, content_encoding="gzip")
    with closing(OperationalMarketStore(root)) as store:
        assert store.status().to_dict()["observation_count"] == 940
        assert store.validate().to_dict()["valid"]
        assert store.connection.execute("SELECT body FROM raw_pages").fetchone()[0] == raw.body
        assert owner.to_dict()["raw_receipt"]["rawSha256"] == hashlib.sha256(raw.body).hexdigest()
        payload, name = store.connection.execute(
            "SELECT payload,reported_name FROM market_observations WHERE ticker='000001'"
        ).fetchone()
        assert json.loads(payload)["close"] == 105
        assert name == "Synthetic instrument 0"
        original = json.loads(
            store.connection.execute(
                "SELECT source_row FROM observation_sources LIMIT 1"
            ).fetchone()[0]
        )
        assert original["ORIGINAL_EXTRA"] == {"text": "원본", "value": "000001"}
        exported = tmp_path / "exports"
        exported.mkdir(mode=0o700)
        result = export_capture_receipt(store.connection, 1, exported)
        assert result.raw_id == 1
        assert verify_exported_capture_receipt(store.connection, 1, exported)


def test_exact_idempotency_and_repeated_source_links(root: Path) -> None:
    first = ingest(root)
    before = (root / DB).read_bytes()
    assert ingest(root) == first
    assert (root / DB).read_bytes() == before
    second = ingest(root, "second", replace(page(), collected_at=NOW + timedelta(seconds=1)))
    assert second != first
    s = status(root)
    assert (
        s["observation_count"],
        s["batch_count"],
        s["additional_source_links"],
        s["duplicate_records"],
    ) == (3, 2, 3, 0)
    with closing(OperationalMarketStore(root)) as store:
        assert store.validate().to_dict()["valid"]
        assert (
            json.loads(
                store.connection.execute(
                    "SELECT payload FROM market_observations LIMIT 1"
                ).fetchone()[0]
            )["collected_at"]
            == NOW.isoformat()
        )
    with pytest.raises(ValueError, match="idempotency_conflict"):
        ingest(root, source=replace(page(), collected_at=NOW + timedelta(seconds=2)))


def test_market_identity_and_alpha_code_no_name_invention(root: Path) -> None:
    ingest(root)
    ingest(root, "kosdaq", page(market="KOSDAQ"), market="KOSDAQ")
    source = RawPage(json.dumps({"OutBlock_1": [row("00088K")]}).encode(), NOW, DAY)
    ingest(root, "alpha", source)
    with closing(OperationalMarketStore(root)) as store:
        assert store.status().to_dict()["observation_count"] == 7
        assert store.connection.execute(
            "SELECT reported_name FROM market_observations WHERE ticker='00088K'"
        ).fetchone() == (None,)
        assert store.validate().to_dict()["valid"]


@pytest.mark.parametrize(
    "damage", ["duplicate", "price", "market", "date", "name", "gzip", "recovery", "empty_code"]
)
def test_invalid_or_recovery_input_rejected_without_any_write(root: Path, damage: str) -> None:
    body = json.loads(page().body)
    if damage == "duplicate":
        body["OutBlock_1"].append(body["OutBlock_1"][0].copy())
    elif damage == "price":
        body["OutBlock_1"][0]["TDD_CLSPRC"] = "0"
    elif damage == "market":
        body["OutBlock_1"][0]["MKT_NM"] = "KOSDAQ"
    elif damage == "date":
        body["OutBlock_1"][0]["BAS_DD"] = "20240103"
    elif damage == "name":
        body["OutBlock_1"][0]["ISU_NM"] = 123
    elif damage == "recovery":
        body = {"version": "krx_capture_recovery_v1", "observations": []}
    elif damage == "empty_code":
        body["OutBlock_1"][0]["ISU_CD"] = ""
    source = replace(page(), body=b"bad gzip" if damage == "gzip" else json.dumps(body).encode())
    before = (root / DB).read_bytes()
    from donghak_stock_vision.providers.base import ProviderError

    with pytest.raises((ValueError, ProviderError)):
        ingest(root, source=source, content_encoding="gzip" if damage == "gzip" else "identity")
    assert (root / DB).read_bytes() == before
    assert status(root)["batch_count"] == 0


def test_conflict_rolls_back_entire_batch_and_keeps_originals(root: Path) -> None:
    ingest(root)
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="reviewed_revision"):
        ingest(root, "conflict", page(TDD_CLSPRC="106"))
    assert (root / DB).read_bytes() == before
    assert status(root)["batch_count"] == 1


def test_storage_is_never_a_research_or_backtest_db(root: Path) -> None:
    ingest(root)
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="not_a_research"):
        SQLiteStore(root / DB)
    with closing(ReadOnlyMarketStore(root / DB)) as research:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            research.read("000001", DAY, DAY)
    assert (root / DB).read_bytes() == before
    assert not any(status(root)["eligibility"].values())


@pytest.mark.parametrize("target", ["root", "database", "symlink"])
def test_permissions_and_aliases_fail_closed(root: Path, tmp_path: Path, target: str) -> None:
    if target == "symlink":
        link = tmp_path / "alias"
        link.symlink_to(root, target_is_directory=True)
        with pytest.raises(ValueError):
            OperationalMarketStore(link)
    else:
        path = root if target == "root" else root / DB
        path.chmod(0o755 if target == "root" else 0o644)
        with pytest.raises(ValueError, match="permissions"):
            OperationalMarketStore(root)


def test_unknown_existing_root_and_incomplete_initialization_never_overwritten(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unknown"
    root.mkdir(mode=0o700)
    sentinel = root / "original-evidence"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ValueError):
        OperationalMarketStore.initialize(root)
    assert sentinel.read_bytes() == b"keep"
    assert list(root.iterdir()) == [sentinel]


@pytest.mark.parametrize("limit", ["space", "database", "rows", "bytes"])
def test_capacity_limits_do_not_leave_partial_state(root: Path, tmp_path: Path, limit: str) -> None:
    if limit == "database":
        root = tmp_path / "small"
        OperationalMarketStore.initialize(
            root, StorageLimits(max_database_bytes=131072, min_free_bytes=0)
        )
    if limit == "rows":
        root = tmp_path / "rows"
        OperationalMarketStore.initialize(
            root, StorageLimits(min_free_bytes=0, max_rows_per_batch=2)
        )
    source = replace(page(), body=b"a" * (2 * 1024**2 + 1)) if limit == "bytes" else page()
    before = (root / DB).read_bytes()
    from donghak_stock_vision.providers.base import ProviderError

    with patch(
        "donghak_stock_vision.storage.operational_market.shutil.disk_usage",
        return_value=SimpleNamespace(free=0 if limit == "space" else 1024**4),
    ):
        with pytest.raises((ValueError, ProviderError)):
            ingest(root, source=source)
    assert (root / DB).read_bytes() == before


def test_concurrent_connections_serialize_duplicates_and_preserve_snapshot(root: Path) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ingest(root), range(4)))
    assert all(r == results[0] for r in results)
    assert status(root)["observation_count"] == 3
    with closing(OperationalMarketStore(root)) as store:
        assert store.validate().to_dict()["valid"]


@pytest.mark.parametrize("stage", ["raw", "observation"])
def test_process_crash_rolls_back_raw_and_normalized_writes(root: Path, stage: str) -> None:
    ingest(root, "original")
    code = """
import os,sys
from pathlib import Path
from tests.integration.test_operational_market_storage import page
from donghak_stock_vision.storage.operational_market import OperationalMarketStore
import donghak_stock_vision.storage.operational_market as module
original=module.archive_raw_page
def crash(*args):
 result=original(*args)
 os._exit(23)
store=OperationalMarketStore(Path(sys.argv[1]),writable=True)
if sys.argv[2]=='raw':
 module.archive_raw_page=crash
else:
 store.connection.create_function('crash_now',0,lambda: os._exit(23))
 store.connection.execute('CREATE TEMP TRIGGER crash_insert AFTER INSERT ON market_observations '
                          'BEGIN SELECT crash_now(); END')
store.ingest('interrupted','KOSPI',page(4),source_kind='synthetic_fixture')
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), stage], timeout=30)
    assert result.returncode == 23
    # Explicit writable initialization/open permits SQLite's own hot-journal recovery.
    OperationalMarketStore.initialize(root)
    assert status(root)["batch_count"] == 1
    with closing(OperationalMarketStore(root)) as store:
        assert store.validate().to_dict()["valid"]


@pytest.mark.parametrize("damage", ["raw", "payload", "manifest", "source_row", "schema"])
def test_integrity_detects_modified_or_self_rehashed_records(root: Path, damage: str) -> None:
    ingest(root)
    with closing(OperationalMarketStore(root, writable=True)) as store:
        table = {
            "raw": "raw_pages",
            "payload": "market_observations",
            "manifest": "ingestion_batches",
            "source_row": "observation_sources",
            "schema": "raw_pages",
        }[damage]
        sql = store.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table + "_immutable_update",)
        ).fetchone()[0]
        store.connection.execute(f"DROP TRIGGER {table}_immutable_update")
        if damage == "schema":
            store.connection.commit()
            with pytest.raises(ValueError, match="schema_mismatch"):
                store.validate()
            return
        if damage == "raw":
            store.connection.execute("UPDATE raw_pages SET body=?", (b"changed",))
        elif damage == "payload":
            d = json.loads(
                store.connection.execute(
                    "SELECT payload FROM market_observations LIMIT 1"
                ).fetchone()[0]
            )
            d["close"] += 1
            store.connection.execute("UPDATE market_observations SET payload=?", (json.dumps(d),))
        elif damage == "source_row":
            store.connection.execute("UPDATE observation_sources SET source_row=?", ("{}",))
        else:
            d = json.loads(
                store.connection.execute("SELECT document FROM ingestion_batches").fetchone()[0]
            )
            d["eligibility"]["training_ready"] = True
            forged = FrozenJSON.freeze(d)
            store.connection.execute(
                "UPDATE ingestion_batches SET document=?,fingerprint=?",
                (forged.payload_json, forged.identifier),
            )
        store.connection.execute(sql)
        store.connection.commit()
        assert not store.validate().to_dict()["valid"]


def test_cli_offline_status_operations_and_no_implicit_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "cli"
    with patch("socket.socket.connect", side_effect=AssertionError("no network")):
        assert main(["market-storage", "status", "--root", str(root)]) == 2
        assert not root.exists()
        assert main(["market-storage", "init", "--root", str(root), "--min-free-bytes", "0"]) == 0
        initial = json.loads(capsys.readouterr().out)
        assert initial["latest_trading_date"] is None
        ingest(root)
        before = (root / DB).read_bytes()
        for action in ("status", "validate", "duplicates", "usage", "latest"):
            assert main(["market-storage", action, "--root", str(root)]) == 0
            result = json.loads(capsys.readouterr().out)
            assert not any(result["eligibility"].values())
        assert (root / DB).read_bytes() == before
        assert (
            main(["market-storage", "status", "--root", str(root), "--max-database-bytes", "123"])
            == 2
        )


def test_live_reader_sees_only_committed_batch(root: Path) -> None:
    from threading import Event

    import donghak_stock_vision.storage.operational_market as module

    ingest(root, "original")
    ready, release = Event(), Event()
    original = archive_raw_page

    def hold(*args: Any) -> int:
        raw_id = original(*args)
        ready.set()
        assert release.wait(10)
        return raw_id

    with patch.object(module, "archive_raw_page", hold), ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(ingest, root, "pending")
        try:
            assert ready.wait(5)
            s = status(root)
            assert s["observation_count"] == 3 and s["batch_count"] == 1
            with closing(OperationalMarketStore(root)) as reader:
                assert reader.validate().to_dict()["valid"]
        finally:
            release.set()
        task.result(timeout=10)
    assert status(root)["batch_count"] == 2


def test_bounded_writer_lock_and_sqlite_full_roll_back(root: Path) -> None:
    with (
        closing(OperationalMarketStore(root, writable=True)) as first,
        closing(OperationalMarketStore(root, writable=True)) as second,
    ):
        first.connection.execute("BEGIN IMMEDIATE")
        second.connection.execute("PRAGMA busy_timeout=1")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.ingest("locked", "KOSPI", page(), source_kind="synthetic_fixture")
        first.connection.rollback()
        pages = first.connection.execute("PRAGMA page_count").fetchone()[0]
        first.connection.execute(f"PRAGMA max_page_count={pages}")
        with pytest.raises(sqlite3.DatabaseError, match="full"):
            first.ingest("full", "KOSPI", page(940), source_kind="synthetic_fixture")
    assert status(root)["observation_count"] == 0
    assert status(root)["batch_count"] == 0


def test_empty_response_is_not_a_verified_holiday_or_trading_session(root: Path) -> None:
    owner = ingest(root, source=page(0))
    assert owner.to_dict()["observation_count"] == 0
    s = status(root)
    assert s["latest_trading_date"] is None and s["latest_response_date"] == DAY.isoformat()
    assert not any(s["eligibility"].values())
    with closing(OperationalMarketStore(root)) as store:
        assert store.validate().to_dict()["valid"]


def test_exact_integer_precision_and_original_text(root: Path) -> None:
    value = 9007199254740993
    source = page(
        1,
        TDD_OPNPRC=str(value),
        TDD_HGPRC=str(value),
        TDD_LWPRC=str(value),
        TDD_CLSPRC=str(value),
        ACC_TRDVOL="1",
        ACC_TRDVAL=str(value),
    )
    ingest(root, source=source)
    with closing(OperationalMarketStore(root)) as store:
        raw, normalized = store.connection.execute(
            "SELECT source_row,payload FROM observation_sources JOIN market_observations "
            "USING(market,trading_date,ticker)"
        ).fetchone()
        assert json.loads(raw)["TDD_CLSPRC"] == str(value)
        assert json.loads(normalized)["close"] == value
        assert store.validate().to_dict()["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_database_bytes", 0),
        ("max_database_bytes", True),
        ("min_free_bytes", -1),
        ("max_rows_per_batch", 10001),
    ],
)
def test_invalid_limits_before_disk_writes(tmp_path: Path, field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        OperationalMarketStore.initialize(tmp_path / "invalid", StorageLimits(**{field: value}))
    assert not (tmp_path / "invalid").exists()


def test_space_check_precedes_creation_and_settings_are_not_silently_changed(
    root: Path, tmp_path: Path
) -> None:
    with patch(
        "donghak_stock_vision.storage.operational_market.shutil.disk_usage",
        return_value=SimpleNamespace(free=0),
    ):
        with pytest.raises(ValueError, match="free_disk_space"):
            OperationalMarketStore.initialize(tmp_path / "no-space")
    assert not (tmp_path / "no-space").exists()
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="config_conflict"):
        OperationalMarketStore.initialize(root, StorageLimits(min_free_bytes=1))
    assert (root / DB).read_bytes() == before


def test_readonly_ingest_and_unknown_origin_cannot_write(root: Path) -> None:
    with closing(OperationalMarketStore(root)) as reader:
        with pytest.raises(ValueError, match="writable"):
            reader.ingest("readonly", "KOSPI", page(), source_kind="synthetic_fixture")
    with closing(OperationalMarketStore(root, writable=True)) as writer:
        with pytest.raises(ValueError, match="origin"):
            writer.ingest("claim", "KOSPI", page(), source_kind="independently_authenticated")
    assert status(root)["batch_count"] == 0


@pytest.mark.parametrize("version", [0, 2])
def test_unknown_versions_do_not_migrate(root: Path, version: int) -> None:
    with closing(sqlite3.connect(root / DB)) as con:
        con.execute(f"PRAGMA user_version={version}")
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="version"):
        OperationalMarketStore.initialize(root)
    assert (root / DB).read_bytes() == before


def test_normal_writes_cannot_overwrite_evidence(root: Path) -> None:
    ingest(root)
    with closing(OperationalMarketStore(root, writable=True)) as store:
        for table in (
            "raw_pages",
            "raw_capture_receipts",
            "ingestion_batches",
            "market_observations",
            "observation_sources",
            "operational_settings",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                store.connection.execute(f"DELETE FROM {table}")
            store.connection.rollback()
        assert store.validate().to_dict()["valid"]


def test_storage_cli_never_uses_research_database_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "research.sqlite3"
    SQLiteStore(legacy).close()
    before = legacy.read_bytes()
    monkeypatch.setenv("DSV_DB_PATH", str(legacy))
    root = tmp_path / "separate"
    assert main(["market-storage", "init", "--root", str(root), "--min-free-bytes", "0"]) == 0
    assert legacy.read_bytes() == before
    rejected = tmp_path / "rejected"
    assert main(["--db", str(legacy), "market-storage", "init", "--root", str(rejected)]) == 2
    assert not rejected.exists() and legacy.read_bytes() == before


def test_existing_historical_file_cannot_be_migrated_as_operational(root: Path) -> None:
    (root / DB).unlink()  # Synthetic fixture only: put a separate legacy schema in its place.
    SQLiteStore(root / DB).close()
    (root / DB).chmod(0o600)
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="version"):
        OperationalMarketStore.initialize(root)
    assert (root / DB).read_bytes() == before


def test_committed_batch_survives_lost_acknowledgement_without_duplicate(root: Path) -> None:
    code = """
import os,sys
from pathlib import Path
from tests.integration.test_operational_market_storage import page
from donghak_stock_vision.storage.operational_market import OperationalMarketStore
store=OperationalMarketStore(Path(sys.argv[1]),writable=True)
store.ingest('committed','KOSPI',page(),source_kind='synthetic_fixture')
os._exit(23)
"""
    assert subprocess.run([sys.executable, "-c", code, str(root)], timeout=30).returncode == 23
    first = ingest(root, "committed")
    assert ingest(root, "committed") == first
    s = status(root)
    assert s["batch_count"] == 1 and s["observation_count"] == 3


@pytest.mark.parametrize("where", ["price", "unknown_extra"])
def test_unsupported_fractional_json_never_silently_rounds(root: Path, where: str) -> None:
    d = json.loads(page(1).body)
    key = "TDD_CLSPRC" if where == "price" else "UNKNOWN_DECIMAL"
    d["OutBlock_1"][0][key] = 100.125
    before = (root / DB).read_bytes()
    with pytest.raises(ValueError, match="fractional_json"):
        ingest(root, source=replace(page(), body=json.dumps(d).encode()))
    assert (root / DB).read_bytes() == before
