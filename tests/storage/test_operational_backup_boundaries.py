"""Offline-only boundaries for existing operational SQLite backup/restore."""

import hashlib
import os
from pathlib import Path

import pytest
from tests.storage.test_operational_backup import make_operational_database

from donghak_stock_vision.storage.operational_backup import (
    OperationalBackupError,
    backup_operational_database,
    restore_operational_backup,
    verify_backup,
)


def test_backup_restore_do_not_modify_source_or_overwrite_target(tmp_path: Path) -> None:
    source = tmp_path / "operational.sqlite3"
    backup = tmp_path / "backup.snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    make_operational_database(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    receipt = backup_operational_database(source, backup)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert backup.stat().st_mode & 0o777 == 0o600

    restored_receipt = restore_operational_backup(
        backup, restored, expected_sha256=receipt.sha256
    )
    assert restored_receipt.observation_count == receipt.observation_count
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert restored.stat().st_mode & 0o777 == 0o600

    with pytest.raises(OperationalBackupError, match="destination"):
        restore_operational_backup(backup, restored, expected_sha256=receipt.sha256)
    assert verify_backup(backup, expected_sha256=receipt.sha256) == receipt


def test_backup_rejects_hardlinked_source_and_symlink_destination(tmp_path: Path) -> None:
    source = tmp_path / "operational.sqlite3"
    hardlink = tmp_path / "linked.sqlite3"
    target = tmp_path / "backup.snapshot.sqlite3"
    make_operational_database(source)
    os.link(source, hardlink)

    with pytest.raises(OperationalBackupError, match="hardlinks"):
        backup_operational_database(source, target)
    assert not target.exists()

    hardlink.unlink()
    target.symlink_to(source)
    with pytest.raises(OperationalBackupError, match="destination"):
        backup_operational_database(source, target)


def test_restore_rejects_sqlite_sidecar_and_preserves_target_absence(tmp_path: Path) -> None:
    source = tmp_path / "operational.sqlite3"
    backup = tmp_path / "backup.snapshot.sqlite3"
    target = tmp_path / "restored.sqlite3"
    make_operational_database(source)
    receipt = backup_operational_database(source, backup)
    wal = Path(str(backup) + "-wal")
    wal.write_bytes(b"unexpected WAL sidecar")

    with pytest.raises(OperationalBackupError, match="sidecars"):
        restore_operational_backup(backup, target, expected_sha256=receipt.sha256)
    assert not target.exists()
    assert verify_backup(source).observation_count == 0
