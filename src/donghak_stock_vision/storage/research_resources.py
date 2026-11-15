"""Shared read-only macOS resource probes for sequential research owners."""

import fcntl
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from donghak_stock_vision.storage.autonomous_research import LIMITS


def _command(args: list[str]) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def probe() -> dict[str, Any]:
    if sys.platform != "darwin":
        return dict(verified=False, reason="macos_native_resource_probe_required")
    power = _command(["/usr/bin/pmset", "-g", "batt"])
    thermal = _command(
        [
            "/usr/bin/osascript",
            "-l",
            "JavaScript",
            "-e",
            'ObjC.import("Foundation"); Number($.NSProcessInfo.processInfo.thermalState)',
        ]
    )
    memory = _command(["/usr/bin/memory_pressure", "-Q"])
    physical = _command(["/usr/sbin/sysctl", "-n", "hw.memsize"])
    percent = re.search(r"System-wide memory free percentage:\s*(\d+)%", memory)
    return dict(
        verified=thermal in ("0", "1", "2", "3")
        and percent is not None
        and ("AC Power" in power or "Battery Power" in power),
        physical_memory_mb=int(physical) // 1024**2 if physical.isdigit() else None,
        ac_power="AC Power" in power,
        thermal_state=int(thermal) if thermal.isdigit() else None,
        memory_free_percent=int(percent.group(1)) if percent else None,
    )


def resource_reason(root: Path, state: dict[str, Any]) -> str | None:
    if not state.get("verified"):
        return "resource_probe_unknown"
    if not state["ac_power"]:
        return "on_battery"
    if state["thermal_state"] not in (0, 1):
        return "thermal_pressure"
    if state["memory_free_percent"] < LIMITS["minimum_memory_free_percent"]:
        return "memory_pressure"
    if shutil.disk_usage(root).free < LIMITS["minimum_free_mb"] * 1024**2:
        return "disk_free_reserve"
    if (
        sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        > LIMITS["maximum_study_mb"] * 1024**2
    ):
        return "study_disk_limit"
    return None


@contextmanager
def lock(root: Path, name: str) -> Iterator[int]:
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("autonomous_runner_already_active") from None
        yield fd
    finally:
        os.close(fd)
