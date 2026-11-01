#!/usr/bin/env python3
"""Local, bounded CI-remediation preflight; no GitHub tokens or branch writes.

This is an execution component, NOT a CI webhook or an auto-merge bot.
Run on a trusted checkout, never on an untrusted PR with privileged credentials.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], root: Path) -> int:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=root, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--apply-ruff", action="store_true",
                        help="Explicitly apply safe Ruff lint and format fixes")
    parser.add_argument("--targeted-test", action="append", default=[],
                        help="Explicit offline pytest node ID; may be repeated")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "src").is_dir():
        parser.error("root must be the checked-out project root")
    for executable in ("ruff", "mypy", "pytest"):
        if shutil.which(executable) is None:
            print(f"Missing executable: {executable}", file=sys.stderr)
            return 2

    # Do not use shells, credentials, network calls, commits or pushes.
    if args.apply_ruff:
        for cmd in (["ruff", "check", "--fix", "src", "tests"],
                    ["ruff", "format", "src", "tests"]):
            if run(cmd, root) != 0:
                return 1

    commands = [
        ["ruff", "check", "src", "tests"],
        ["ruff", "format", "--check", "src", "tests"],
        ["mypy"],
    ]
    if args.targeted_test:
        # pytest.ini excludes live tests by default; force it here as well.
        commands.append(["pytest", "-m", "not live", *args.targeted_test])
    for command in commands:
        if run(command, root) != 0:
            return 1
    print("Preflight passed. Full GitHub CI is still required.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
