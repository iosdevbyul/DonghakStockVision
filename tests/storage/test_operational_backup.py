"""No real operator files, KRX calls or running operational DB writes."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from donghak_stock_vision.storage.operational_backup import (
    OperationalBackupError,
    backup_operational_database,
    restore_operational_backup,
    verify_backup,
)
from donghak_stock_vision.storage.operational_market import SCHEMA
from donghak_stock_vision.storage.sqlite import OPERATIONAL_APPLICATION_ID


def make_operational_database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA application_id={OPERATIONAL_APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.executescript(SCHEMA)


def test_consistent_backup_and_offline_restore(tmp_path: Path) -> None:
    source = tmp_path / "operational.sqlite3"
    backup = tmp_path / "operational.snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    make_operational_database(source)

    result = backup_operational_database(source, backup)
    assert result.observation_count == 0
    assert result.raw_page_count == 0
    assert result.sha256 == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert verify_backup(backup, expected_sha256=result.sha256) == result
    check = restore_operational_backup(backup, restored, expected_sha256=result.sha256)
    assert check.observation_count == result.observation_count
    assert check.raw_page_count == result.raw_page_count
    assert not Path(str(restored) + "-wal").exists()


def test_no_overwrite_and_tamper_rejection(tmp_path: Path) -> None:
    source = tmp_path / "operational.sqlite3"
    target = tmp_path / "backup.sqlite3"
    make_operational_database(source)
    result = backup_operational_database(source, target)
    with pytest.raises(OperationalBackupError, match="destination"):
        backup_operational_database(source, target)
    with pytest.raises(OperationalBackupError, match="fingerprint"):
        restore_operational_backup(
            target, tmp_path / "restored.sqlite3", expected_sha256="0" * 64
        )
    with target.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(OperationalBackupError, match="fingerprint"):
        verify_backup(target, expected_sha256=result.sha256)


def test_reject_invalid_source_and_relative_destination(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(OperationalBackupError, match="source"):
        backup_operational_database(missing, tmp_path / "backup.sqlite3")
    source = tmp_path / "operational.sqlite3"
    make_operational_database(source)
    with pytest.raises(OperationalBackupError, match="destination"):
        backup_operational_database(source, Path("relative.sqlite3"))
    with pytest.raises(OperationalBackupError, match="source"):
        backup_operational_database(source, source)
