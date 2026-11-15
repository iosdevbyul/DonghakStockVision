"""One bounded background worker at a time; render-only macOS scheduling."""

import fcntl
import os
import plistlib
import resource
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
from donghak_stock_vision.storage.research_resources import (
    _command as _command,
)
from donghak_stock_vision.storage.research_resources import (
    probe as probe,
)
from donghak_stock_vision.storage.research_resources import (
    resource_reason as resource_reason,
)


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
    root: Path,
    pin: str,
    steps: int,
    *,
    resource_probe: Callable[[], dict[str, Any]] = probe,
    agent: str = "rule",
    reuse: tuple[Path, str] | None = None,
) -> FrozenJSON:
    require(type(steps) is int and 1 <= steps <= 36, "autonomous_step_bound")
    plan, data = context(root, pin)
    require(
        agent in ("rule", "gemma4")
        and (agent == "rule" or plan["version"] == "autonomous_plan_v2"),
        "gemma_explicit_versioned_activation",
    )
    completed = 0
    trained = 0
    cache_hits = 0
    selections: list[dict[str, Any]] = []
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
            from donghak_stock_vision.storage.gemma_memory import adopt, memory_view

            view: dict[str, Any] = (
                memory_view(root, plan, data, done, choice)
                if plan["version"] == "autonomous_plan_v2"
                else dict(
                    version="research_agent_view_v1",
                    candidates=[choice],
                    history=[dict(experiment_id=k, state=v["state"]) for k, v in done.items()],
                )
            )
            selected = select_proposal(RuleBasedAgent(), view)
            decision: dict[str, Any] = dict(
                agent=agent, at=clock(), state="rule", history_count=len(view["history"])
            )
            parent_experiment = choice["parent_experiment"]
            if agent == "gemma4":
                from donghak_stock_vision.storage.gemma_research import Gemma4ResearchAgent, recover

                if not recover(root, plan["agent_model_digest"]):
                    paused = "gemma_release_unverified"
                    break
                gemma = Gemma4ResearchAgent(root, plan["agent_model_digest"])
                try:
                    selected = select_proposal(gemma, view)
                    parent_experiment = gemma.audit["proposal"]["parentExperimentId"]
                    decision.update(state="accepted", request_id=gemma.audit["request_id"])
                except ValueError as error:
                    decision.update(
                        state="fallback",
                        reason=str(error),
                        request_id=gemma.audit.get("request_id"),
                    )
                if not gemma.unloaded:
                    paused = "gemma_release_unverified"
                    with closing(AnalysisStore(root / DB)) as store:
                        store.put("autonomous_selection", decision | dict(state="blocked_unload"))
                    break
                # Inference has ended and model release was verified before this
                # fresh training-resource check. No worker exists during inference.
                paused = resource_reason(root, resource_probe())
                if paused:
                    break
            if plan["version"] == "autonomous_plan_v2":
                # Covers rule mode after an orphaned Gemma process, including a
                # different study. Never start a learner alongside a resident LLM.
                from donghak_stock_vision.storage.gemma_research import process_resources

                resident = process_resources()
                if not resident["verified"] or resident["rss_kb"] >= 256 * 1024:
                    paused = "ollama_release_resources_unverified"
                    break
            chosen = next(c for c in view["candidates"] if c["experiment_id"] == selected)
            choice = chosen | dict(
                selection_reason="gemma_validated_proposal"
                if decision["state"] == "accepted"
                else choice["selection_reason"],
                parent_experiment=parent_experiment,
            )
            decision.update(experiment_id=selected, training_started_after_unload=True)
            with closing(AnalysisStore(root / DB)) as store:
                store.put("autonomous_selection", decision)
            selections.append(decision)
            if reuse is not None and adopt(root, plan, *reuse, choice):
                completed += 1
                cache_hits += 1
                continue
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
            trained += 1
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
        output.update(
            executed_this_run=completed,
            trained_this_run=trained,
            cache_hits_this_run=cache_hits,
            agent=agent,
            selections_this_run=selections,
            pause_reason=paused,
            resource_audits=resources,
        )
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
