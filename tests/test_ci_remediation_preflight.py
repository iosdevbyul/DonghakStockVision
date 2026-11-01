"""Offline tests for the bounded remediation preflight entry point."""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-remediation-preflight.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_remediation_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_only_preflight_runs_checks_in_order(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "src").mkdir()
    module = load_script()
    observed: list[list[str]] = []

    def command(args: list[str], root: Path) -> int:
        assert root == tmp_path
        observed.append(args)
        return 0

    with patch.object(module.shutil, "which", return_value="/usr/bin/tool"):
        with patch.object(module, "run", side_effect=command):
            result = module.main(["--root", str(tmp_path), "--targeted-test", "tests/test_a.py"])

    assert result == 0
    assert observed == [
        ["ruff", "check", "src", "tests"],
        ["ruff", "format", "--check", "src", "tests"],
        ["mypy"],
        ["pytest", "-m", "not live", "tests/test_a.py"],
    ]


def test_fix_mode_stops_before_checks_on_fix_failure(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "src").mkdir()
    module = load_script()
    observed: list[list[str]] = []

    def fail_first(args: list[str], root: Path) -> int:
        observed.append(args)
        return 1

    with patch.object(module.shutil, "which", return_value="/usr/bin/tool"):
        with patch.object(module, "run", side_effect=fail_first):
            result = module.main(["--root", str(tmp_path), "--apply-ruff"])

    assert result == 1
    assert observed == [["ruff", "check", "--fix", "src", "tests"]]


def test_missing_tools_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "src").mkdir()
    module = load_script()
    with patch.object(module.shutil, "which", return_value=None):
        assert module.main(["--root", str(tmp_path)]) == 2
