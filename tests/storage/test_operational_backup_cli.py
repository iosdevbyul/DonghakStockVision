"""Offline CLI contract: synthetic SQLite only, no network or operator database."""

import json
import sqlite3
from pathlib import Path

import pytest

from donghak_stock_vision.cli import main
from donghak_stock_vision.storage.operational_market import SCHEMA
from donghak_stock_vision.storage.sqlite import OPERATIONAL_APPLICATION_ID


def make_store(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA application_id={OPERATIONAL_APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.executescript(SCHEMA)


def test_backup_verify_restore_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "operational.sqlite3"
    snapshot = tmp_path / "operational.snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    make_store(source)
    assert (
        main(
            [
                "market-storage", "backup", "--source", str(source),
                "--destination", str(snapshot),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    sha = report["sha256"]
    assert report["action"] == "backup"
    assert snapshot.is_file()
    assert (
        main(
            [
                "market-storage", "verify-backup", "--source", str(snapshot),
                "--expected-sha256", sha,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["sha256"] == sha
    assert (
        main(
            [
                "market-storage", "restore-backup", "--source", str(snapshot),
                "--destination", str(restored), "--expected-sha256", sha,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["action"] == "restore-backup"
    assert restored.is_file()


@pytest.mark.parametrize(
    "args",
    [
        ["backup", "--source", "relative.sqlite3", "--destination", "relative2.sqlite3"],
        ["verify-backup", "--source", "relative.sqlite3"],
        ["restore-backup", "--source", "relative.sqlite3", "--expected-sha256", "a" * 64],
    ],
)
def test_explicit_cli_arguments_required(args: list[str]) -> None:
    assert main(["market-storage", *args]) == 2
