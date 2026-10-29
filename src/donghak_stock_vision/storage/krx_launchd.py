"""Per-user LaunchAgent plans. No application code installs/enables on import."""

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.storage.krx_scheduler import (
    SchedulerConfig,
    control,
    host_seoul,
    set_enabled,
)

LABEL = "com.donghakstockvision.krx-daily"


def plist_bytes(config: SchedulerConfig, config_path: Path) -> bytes:
    d = config.document.to_dict()
    require(config_path.is_absolute(), "absolute_scheduler_config_path_required")
    return plistlib.dumps(
        dict(
            Label=LABEL,
            ProgramArguments=[
                d["python_executable"],
                "-m",
                "donghak_stock_vision",
                "krx-scheduler",
                "run-due",
                "--execute",
                "--scheduled",
                "--config",
                str(config_path),
                "--config-pin",
                config.document.identifier,
            ],
            WorkingDirectory=d["working_directory"],
            RunAtLoad=False,
            KeepAlive=False,
            Disabled=True,
            StartCalendarInterval=[
                dict(Weekday=day, Hour=d["hour"], Minute=d["minute"]) for day in range(1, 6)
            ],
            EnvironmentVariables=dict(TZ="Asia/Seoul", PYTHONUNBUFFERED="1"),
            StandardOutPath=str(config.path("runner_root") / "launchd.stdout.log"),
            StandardErrorPath=str(config.path("runner_root") / "launchd.stderr.log"),
        ),
        sort_keys=True,
    )


def _agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def _owned(path: Path, expected: bytes) -> bool:
    if not path.exists():
        return False
    require(
        not path.is_symlink()
        and path.is_file()
        and path.stat().st_uid == os.getuid()
        and path.stat().st_size <= 32768
        and path.read_bytes() == expected,
        "existing_launchagent_conflict",
    )
    return True


def _launchctl(*args: str, required: bool = True) -> bool:
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, timeout=10, check=False)
    require(not required or result.returncode == 0, "launchctl_operation_failed")
    return result.returncode == 0


def status(config: SchedulerConfig, config_path: Path) -> dict[str, Any]:
    installed = _owned(_agent_path(), plist_bytes(config, config_path))
    loaded = (
        installed
        and sys.platform == "darwin"
        and _launchctl("print", f"gui/{os.getuid()}/{LABEL}", required=False)
    )
    launchd_enabled: bool | None = False
    if installed and sys.platform == "darwin":
        result = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"gui/{os.getuid()}"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            launchd_enabled = None
        else:
            match = re.search(
                r'"' + re.escape(LABEL) + r'"\s*=>\s*(true|false)',
                result.stdout.decode("utf-8", errors="replace"),
            )
            launchd_enabled = bool(match and match.group(1) == "false")
    application_enabled = control(config)["enabled"]
    return dict(
        installed=installed,
        loaded=loaded,
        enabled=bool(application_enabled and launchd_enabled and loaded),
        application_enabled=application_enabled,
        launchd_enabled=launchd_enabled,
        host_timezone_verified=host_seoul(),
        label=LABEL,
    )


def manage(
    config: SchedulerConfig,
    config_path: Path,
    action: str,
    *,
    apply: bool = False,
    allow_network: bool = False,
    operator_confirmed: bool = False,
) -> FrozenJSON:
    require(action in ("install", "pause", "resume", "uninstall"), "invalid_launchagent_action")
    expected = plist_bytes(config, config_path)
    path = _agent_path()
    if not apply:
        return FrozenJSON.freeze(
            dict(
                version="krx_launchagent_plan_v1",
                action=action,
                applied=False,
                label=LABEL,
                hour=config.document.to_dict()["hour"],
                minute=config.document.to_dict()["minute"],
                timezone="Asia/Seoul",
                disabled_by_default=True,
                host_timezone_verified=host_seoul(),
                plist=plistlib.loads(expected),
                network_requests=0,
            )
        )
    require(sys.platform == "darwin", "launchagent_requires_macos")
    if action in ("install", "resume"):
        require(host_seoul(), "launchagent_requires_host_timezone_asia_seoul")
    installed = _owned(path, expected)
    domain = f"gui/{os.getuid()}"
    target = domain + "/" + LABEL
    if action == "install":
        require(not control(config)["enabled"], "pause_before_launchagent_install")
        set_enabled(config, False)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not installed:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(expected)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                # Preserve an interrupted plist for explicit operator inspection.
                raise
        # Deliberately not bootstrapped until explicit resume.
    elif action == "resume":
        require(installed, "launchagent_not_installed")
        require(
            allow_network is True and operator_confirmed is True,
            "explicit_scheduler_network_and_operator_enablement_required",
        )
        _launchctl("enable", target)
        if not _launchctl("print", target, required=False):
            _launchctl("bootstrap", domain, str(path))
        set_enabled(config, True, allow_network=True, operator_confirmed=True)
    else:
        set_enabled(config, False)  # Stop future application work before OS operations.
        if installed:
            _launchctl("disable", target)
            if action == "uninstall":
                if _launchctl("print", target, required=False):
                    _launchctl("bootout", target)
                require(_owned(path, expected), "launchagent_changed_during_uninstall")
                path.unlink()
    return FrozenJSON.freeze(
        dict(
            version="krx_launchagent_management_v1",
            action=action,
            applied=True,
            enabled=control(config)["enabled"],
            network_requests=0,
        )
    )
