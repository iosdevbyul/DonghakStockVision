"""Opt-in local benchmark, never collected by pytest. One isolated process per sample.

Example: PYTHONPATH=src:. python tests/benchmarks/mass_simulation_checkpoint.py --runs 100
For the before case, save the original mass_simulation.py and pass --legacy-module PATH.
No network, persistence, real data or timing assertions. stdout is a JSON observation.
"""

import argparse
import importlib.util
import json
import resource
import sys
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.backtest.test_paper_simulation import budget, experiment, scenario

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import BatchScheduler
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.data.learning import canonical


def measure(count: int, trace: bool, legacy_module: Path | None) -> dict[str, Any]:
    scheduler_class: Any = BatchScheduler
    if legacy_module is not None:
        spec = importlib.util.spec_from_file_location("checkpoint_baseline", legacy_module)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        scheduler_class = module.BatchScheduler

    times = {name: 0.0 for name in ("adapter", "construction", "validation", "results")}
    calls = 0
    adapter = PaperSimulationAdapter((scenario(),))
    exp = experiment(adapter, min(count, 100))
    if count > 100:
        exp = replace(
            exp,
            jobs=tuple(
                adapter.job(f"paper-{i:03}", adapter.scenarios[0].scenario_id) for i in range(count)
            ),
            limits=replace(exp.limits, max_runs=count),
        )

    class TimedScheduler(scheduler_class):  # type: ignore[misc]
        def _document(self, records: Any) -> Any:
            start = time.perf_counter()
            try:
                return super()._document(records)
            finally:
                times["construction"] += time.perf_counter() - start

        def _validate(self, checkpoint: Any) -> Any:
            start = time.perf_counter()
            try:
                return super()._validate(checkpoint)
            finally:
                times["validation"] += time.perf_counter() - start

        @property
        def results(self) -> Any:
            start = time.perf_counter()
            try:
                return super().results
            finally:
                times["results"] += time.perf_counter() - start

    class TimedAdapter:
        reference = adapter.reference

        def run(self, *args: Any) -> FrozenJSON:
            nonlocal calls
            calls += 1
            start = time.perf_counter()
            try:
                return adapter.run(*args)
            finally:
                times["adapter"] += time.perf_counter() - start

    if trace:
        tracemalloc.start()
    start = time.perf_counter()
    scheduler = TimedScheduler(exp)
    initialization = time.perf_counter() - start
    initialization_components = dict(times)
    start = time.perf_counter()
    results = scheduler.run(TimedAdapter())
    execution = time.perf_counter() - start
    execution_components = {k: times[k] - initialization_components[k] for k in times}
    start = time.perf_counter()
    checkpoint = scheduler.checkpoint()
    export = time.perf_counter() - start
    assert len(results) == calls == count
    assert all(r.to_dict()["status"] == "succeeded" for r in results)
    result_bytes = len(canonical([r.to_dict() for r in results]).encode())
    checkpoint_bytes = len(checkpoint.payload_json.encode())
    start = time.perf_counter()
    checkpoint_pin = checkpoint.identifier
    hashing = time.perf_counter() - start
    start = time.perf_counter()
    detached = FrozenJSON(checkpoint.payload_json)
    decode = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    execution_times = dict(times)
    start = time.perf_counter()
    restored = TimedScheduler(exp, checkpoint=detached, expected_checkpoint_hash=checkpoint_pin)
    restore = time.perf_counter() - start
    before = calls
    start = time.perf_counter()
    assert restored.run(TimedAdapter()) == results
    resume = time.perf_counter() - start
    assert calls == before
    assert restored.checkpoint() == checkpoint
    start = time.perf_counter()
    adapter.replay_validate(
        exp, exp.jobs[0], FrozenJSON.freeze(results[0].to_dict()["output"]), budget(exp)
    )
    replay = time.perf_counter() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "runs": count,
        "traced": trace,
        "legacy": legacy_module is not None,
        "initialization_s": initialization,
        "execution_s": execution,
        "export_s": export,
        "total_s": initialization + execution + export,
        "components_s": execution_times,
        "execution_components_s": execution_components,
        "scheduling_other_s": execution - sum(execution_components.values()),
        "checkpoint_hash_s": hashing,
        "checkpoint_decode_s": decode,
        "checkpoint_bytes": checkpoint_bytes,
        "result_bytes": result_bytes,
        "python_peak_bytes": peak,
        "process_high_water_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "rss_scope": "entire_fresh_process_including_fixtures_restore_and_one_replay",
        "restore_s": restore,
        "completed_resume_s": resume,
        "adapter_calls": calls,
        "resume_adapter_calls": calls - before,
        "one_job_explicit_replay_s": replay,
        "checkpoint_hash": checkpoint_pin,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, choices=(10, 100, 300), required=True)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--legacy-module", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(measure(args.runs, args.trace, args.legacy_module), sort_keys=True), flush=True
    )


if __name__ == "__main__":
    main()
