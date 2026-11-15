"""One bounded background worker at a time; render-only macOS scheduling."""

import fcntl
import os
import plistlib
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.research_agent import RuleBasedAgent, select_proposal
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import (
    DB,
    LIMITS,
    clock,
    confirm,
    context,
    next_experiment,
    status,
    terminals,
    worker,
)


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
    percent = re.search(r"System-wide memory free percentage:\s*(\d+)%", memory)
    return dict(
        verified=thermal in ("0", "1", "2", "3")
        and percent is not None
        and ("AC Power" in power or "Battery Power" in power),
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


def worker_entry(root: Path, pin: str, attempt: str, gate: bool = False) -> None:
    def deny(event: str, args: tuple[Any, ...]) -> None:
        if event in ("socket.connect", "socket.getaddrinfo"):
            raise RuntimeError("autonomous_network_forbidden")

    sys.addaudithook(deny)
    resource.setrlimit(
        resource.RLIMIT_CPU, (LIMITS["timeout_seconds"], LIMITS["timeout_seconds"] + 5)
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMITS["maximum_study_mb"] * 1024**2,) * 2)
    os.nice(10)
    with lock(root, ".worker.lock"):
        if gate:
            confirm(root, pin)
        else:
            worker(root, pin, attempt)


def _stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)


def launch(
    root: Path,
    pin: str,
    attempt: str,
    *,
    gate: bool = False,
    resource_probe: Callable[[], dict[str, Any]] = probe,
    reservation_fd: int | None = None,
) -> dict[str, Any]:
    logs = root / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        env[name] = "1"
    env["LOKY_MAX_CPU_COUNT"] = "1"
    env.pop("KRX_API_KEY", None)
    args = [
        sys.executable,
        "-m",
        "donghak_stock_vision",
        "autonomous-research",
        "worker",
        "--root",
        str(root),
        "--plan",
        pin,
        "--attempt",
        attempt,
    ]
    if gate:
        args.append("--gate")
    began = time.monotonic()
    reason = None
    maximum_rss = 0
    last_probe = began
    with (logs / (attempt + ".log")).open("xb") as log:
        os.chmod(log.name, 0o600)
        child = subprocess.Popen(
            args,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=() if reservation_fd is None else (reservation_fd,),
        )
        try:
            while child.poll() is None:
                if time.monotonic() - began > LIMITS["timeout_seconds"]:
                    reason = "worker_timeout"
                    break
                output = _command(["/bin/ps", "-o", "rss=", "-p", str(child.pid)])
                if output.isdigit():
                    maximum_rss = max(maximum_rss, int(output))
                    if int(output) > LIMITS["memory_mb"] * 1024:
                        reason = "worker_memory_limit"
                        break
                elif child.poll() is None:
                    reason = "worker_memory_probe_unknown"
                    break
                if time.monotonic() - last_probe >= 5:
                    reason = resource_reason(root, resource_probe())
                    last_probe = time.monotonic()
                    if reason:
                        break
                time.sleep(0.25)
        except BaseException:
            _stop(child)
            raise
        if reason:
            _stop(child)
        code = child.wait(timeout=5)
    return dict(
        returncode=code,
        reason=reason,
        maximum_rss_kb=maximum_rss,
        wall_seconds=time.monotonic() - began,
    )


def run(
    root: Path, pin: str, steps: int, *, resource_probe: Callable[[], dict[str, Any]] = probe
) -> FrozenJSON:
    require(type(steps) is int and 1 <= steps <= 36, "autonomous_step_bound")
    plan, data = context(root, pin)
    completed = 0
    paused = None
    resources = []
    with (
        lock(root, ".runner.lock"),
        lock(root.parent, ".autonomous-heavy-work.lock") as reservation_fd,
    ):
        # An orphan child may still be finishing after its original parent died.
        # Never classify or retry its attempt until the worker lock is free.
        with lock(root, ".worker.lock"):
            pass
        for _ in range(steps):
            with closing(ReadOnlyAnalysisStore(root / DB)) as store:
                done = terminals(store)
                choice = next_experiment(plan, data, done)
                attempts = [v for _, v in store.all("autonomous_attempt")]
                failures = [v for _, v in store.all("autonomous_backoff")]
            if choice is None:
                break
            # Future local agents receive only the bounded, frozen proposal view.
            # The shared reservation covers proposal and training sequentially.
            view = dict(
                version="research_agent_view_v1",
                candidates=[choice],
                history=[dict(experiment_id=k, state=v["state"]) for k, v in done.items()],
            )
            require(
                select_proposal(RuleBasedAgent(), view) == choice["experiment_id"],
                "research_agent_choice",
            )
            if failures:
                from datetime import datetime

                newest = max(datetime.fromisoformat(f["at"]) for f in failures)
                if (datetime.now(UTC) - newest).total_seconds() < LIMITS["backoff_seconds"]:
                    paused = "error_backoff"
                    break
            state = resource_probe()
            paused = resource_reason(root, state)
            if paused:
                break
            prior = [a for a in attempts if a["experiment_id"] == choice["experiment_id"]]
            if len(prior) >= LIMITS["maximum_attempts"]:
                with closing(AnalysisStore(root / DB)) as store:
                    store.put(
                        "autonomous_terminal",
                        dict(
                            experiment_id=choice["experiment_id"],
                            config=choice["config"],
                            state="failed",
                            reason="interrupted_attempt_limit",
                        ),
                    )
                continue
            attempt = dict(
                plan_id=pin,
                experiment_id=choice["experiment_id"],
                started_at=clock(),
                selection_reason=choice["selection_reason"],
                parent_experiment=choice["parent_experiment"],
                attempt_number=len(prior) + 1,
                resources=state,
            )
            with closing(AnalysisStore(root / DB)) as store:
                if prior:
                    store.put(
                        "autonomous_interruption",
                        dict(
                            experiment_id=choice["experiment_id"],
                            previous=prior[-1],
                            recovered_at=clock(),
                        ),
                    )
                attempt_id = store.put("autonomous_attempt", attempt)
            result = launch(
                root, pin, attempt_id, resource_probe=resource_probe, reservation_fd=reservation_fd
            )
            resources.append(result)
            with closing(AnalysisStore(root / DB)) as store:
                done = terminals(store)
                if choice["experiment_id"] not in done:
                    store.put(
                        "autonomous_terminal",
                        dict(
                            experiment_id=choice["experiment_id"],
                            config=choice["config"],
                            state="failed",
                            reason=result["reason"] or "worker_failed",
                            attempt_id=attempt_id,
                        ),
                    )
                store.put("autonomous_resource_audit", dict(attempt_id=attempt_id, **result))
                latest = terminals(store)[choice["experiment_id"]]
                if latest["state"] != "completed":
                    store.put(
                        "autonomous_backoff",
                        dict(at=clock(), experiment_id=choice["experiment_id"]),
                    )
                    paused = "error_backoff"
                    break
            completed += 1
        current = status(root, pin).to_dict()
        if current["remaining"] == 0:
            paused = resource_reason(root, resource_probe())
            if not paused:
                # Confirmation has a distinct bounded process and the same limits.
                with closing(ReadOnlyAnalysisStore(root / DB)) as store:
                    consumed = len(store.all("autonomous_gate_consumed"))
                if consumed < len(data["goals"]):
                    result = launch(
                        root,
                        pin,
                        "confirmation-" + str(time.time_ns()),
                        gate=True,
                        resource_probe=resource_probe,
                        reservation_fd=reservation_fd,
                    )
                    resources.append(result)
                    with closing(AnalysisStore(root / DB)) as store:
                        store.put("autonomous_confirmation_process", dict(at=clock(), **result))
        output = status(root, pin).to_dict()
        output.update(executed_this_run=completed, pause_reason=paused, resource_audits=resources)
        with closing(AnalysisStore(root / DB)) as store:
            store.put("autonomous_run", output | dict(recorded_at=clock()))
        return FrozenJSON.freeze(output)


def render_agent(root: Path, pin_file: Path, output: Path) -> None:
    require(output.is_absolute() and not output.exists(), "autonomous_new_agent_file")
    from donghak_stock_vision.storage.krx_backfill import read_pin

    context(root, read_pin(pin_file))
    data = dict(
        Label="com.donghakstockvision.autonomous-research",
        ProgramArguments=[
            sys.executable,
            "-m",
            "donghak_stock_vision",
            "autonomous-research",
            "run",
            "--root",
            str(root),
            "--plan-pin-file",
            str(pin_file),
            "--steps",
            "1",
        ],
        EnvironmentVariables={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
        },
        StartInterval=LIMITS["interval_seconds"],
        RunAtLoad=True,
        ProcessType="Background",
        LowPriorityIO=True,
        Nice=10,
        StandardOutPath=str(root / "runner.stdout.log"),
        StandardErrorPath=str(root / "runner.stderr.log"),
    )
    # Render only. There is deliberately no bootstrap/load/enable command.
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(plistlib.dumps(data, sort_keys=True))
