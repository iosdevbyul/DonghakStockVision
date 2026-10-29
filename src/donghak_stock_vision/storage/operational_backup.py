"""Offline, operator-invoked, fail-closed operational SQLite backup and restore.

No provider calls, running scheduler controls or eligibility modifications.
"""

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from donghak_stock_vision.storage.operational_market import _check_schema


class OperationalBackupError(ValueError):
    """An operational backup or restore safety check failed."""


@dataclass(frozen=True)
class BackupVerification:
    sha256: str
    bytes: int
    observation_count: int
    raw_page_count: int


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _source(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise OperationalBackupError("existing absolute regular source file required")
    resolved = path.resolve(strict=True)
    if resolved != path or path.stat().st_nlink != 1:
        raise OperationalBackupError("source aliases and hardlinks are forbidden")
    return resolved


def _destination(path: Path, source: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or path.exists():
        raise OperationalBackupError("new absolute destination path required")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink() or parent.resolve() != parent:
        raise OperationalBackupError("existing canonical destination directory required")
    if path == source:
        raise OperationalBackupError("source and destination must differ")
    return path


def verify_backup(path: Path, *, expected_sha256: str | None = None) -> BackupVerification:
    """Independently verify identity, schema, integrity and source-file fingerprint."""
    source = _source(path)
    if expected_sha256 is not None and (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise OperationalBackupError("lowercase SHA-256 fingerprint required")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            raise OperationalBackupError("backup must have no SQLite sidecars")
    before = _digest(source)
    if expected_sha256 is not None and before != expected_sha256:
        raise OperationalBackupError("backup fingerprint mismatch")
    try:
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise OperationalBackupError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise OperationalBackupError("SQLite foreign key check failed")
            _check_schema(connection)
            observations = connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()
            raw_pages = connection.execute("SELECT COUNT(*) FROM raw_pages").fetchone()
    except sqlite3.DatabaseError as error:
        raise OperationalBackupError("invalid operational SQLite backup") from error
    after = _digest(source)
    if before != after:
        raise OperationalBackupError("backup changed during verification")
    assert observations is not None and raw_pages is not None
    return BackupVerification(
        sha256=after,
        bytes=source.stat().st_size,
        observation_count=int(observations[0]),
        raw_page_count=int(raw_pages[0]),
    )


def _atomic_publish(temp: Path, destination: Path) -> None:
    # Linking, unlike rename/replace, cannot overwrite an existing destination.
    os.link(temp, destination)
    temp.unlink()


def backup_operational_database(source: Path, destination: Path) -> BackupVerification:
    """Create one consistent SQLite backup without opening the source for writing."""
    live = _source(source)
    target = _destination(destination, live)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=".operational-backup-", suffix=".sqlite3", dir=target.parent
        )
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(f"{live.as_uri()}?mode=ro", uri=True)) as origin:
            origin.execute("PRAGMA query_only=ON")
            _check_schema(origin)
            with closing(sqlite3.connect(temporary)) as copy:
                origin.backup(copy)
        result = verify_backup(temporary)
        _atomic_publish(temporary, target)
        temporary = None
        return result
    except (OSError, sqlite3.Error, ValueError) as error:
        if isinstance(error, OperationalBackupError):
            raise
        raise OperationalBackupError("operational backup failed") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_operational_backup(
    backup: Path, destination: Path, *, expected_sha256: str
) -> BackupVerification:
    """Restore to a NEW offline file, never to the running operational store."""
    source = _source(backup)
    target = _destination(destination, source)
    verified = verify_backup(source, expected_sha256=expected_sha256)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=".operational-restore-", suffix=".sqlite3", dir=target.parent
        )
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)
        ) as origin:
            with closing(sqlite3.connect(temporary)) as copy:
                origin.backup(copy)
        restored = verify_backup(temporary)
        if (restored.observation_count, restored.raw_page_count) != (
            verified.observation_count,
            verified.raw_page_count,
        ):
            raise OperationalBackupError("restored row count mismatch")
        # Pin source once more: source mutation while copying must not be accepted.
        verify_backup(source, expected_sha256=expected_sha256)
        _atomic_publish(temporary, target)
        temporary = None
        return restored
    except (OSError, sqlite3.Error, ValueError) as error:
        if isinstance(error, OperationalBackupError):
            raise
        raise OperationalBackupError("operational restore failed") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
