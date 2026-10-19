"""Bounded exact archive measurements on 1–4 existing synthetic result files.

No collection/training. Full economic replay runs once per supplied result.
"""

import argparse
import hashlib
import json
import platform
import time
import tracemalloc
from pathlib import Path
from typing import Any
from unittest.mock import patch

from donghak_stock_vision.backtest import receipt_validation
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.receipt_archive import (
    ArchiveLimits,
    ReceiptArchive,
    encode_result,
)
from donghak_stock_vision.data.learning import canonical


def measure(path: Path, *, trace: bool = False) -> dict[str, Any]:
    if path.stat().st_size > ArchiveLimits().expanded_bytes:
        raise ValueError("benchmark_input_limit")
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    original = FrozenJSON(raw.decode())
    d = original.to_dict()
    if trace and len(raw) > 8 * 1024 * 1024:
        raise ValueError("trace_measurement_limited_to_eight_megabytes")
    if trace:
        tracemalloc.start()
    start = time.perf_counter()
    archive = encode_result(original)
    encoding = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    body = archive.document.to_dict()
    start = time.perf_counter()
    serialized = canonical(body)
    serialization = time.perf_counter() - start
    start = time.perf_counter()
    loaded = ReceiptArchive.from_json(serialized)
    deserialization = time.perf_counter() - start
    start = time.perf_counter()
    loaded.verify_transport()
    transport_validation = time.perf_counter() - start
    start = time.perf_counter()
    expanded = "".join(loaded.iter_legacy_json())
    expansion = time.perf_counter() - start
    assert expanded == original.payload_json
    replay_seconds = []
    real_validator = receipt_validation.validate_result

    def timed_validation(value: FrozenJSON) -> None:
        before = time.perf_counter()
        real_validator(value)
        replay_seconds.append(time.perf_counter() - before)

    start = time.perf_counter()
    with patch.object(receipt_validation, "validate_result", timed_validation):
        restored = loaded.expand_validated()
    validation = time.perf_counter() - start
    assert restored == original and len(replay_seconds) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_sha
    report = d["detailed_report"]
    return {
        "tickers": len(report.get("tickers", [report.get("ticker")])),
        "decisions": sum(len(e.get("decisions", [])) for e in d["run"]["events"]),
        "accepted_orders": sum(
            a["status"] == "accepted" for e in d["run"]["events"] for a in e.get("admissions", [])
        ),
        "fills": d["performance"]["fill_count"],
        "original_bytes": len(original.payload_json.encode()),
        "compact_bytes": len(serialized.encode()),
        "report_projection_bytes": len(canonical(report).encode()),
        "artifacts": len(body["artifacts"]),
        "encoding_and_transport_verification_seconds": encoding,
        "serialization_seconds": serialization,
        "deserialization_seconds": deserialization,
        "cold_transport_validation_seconds": transport_validation,
        "legacy_stream_expansion_seconds": expansion,
        "validated_expansion_seconds": validation,
        "full_replay_seconds": replay_seconds[0],
        "python_encoding_peak_bytes": peak,
        "encoding_instrumented": trace,
        "memory_note": (
            "Encoding peak excludes pre-existing input; not full replay/native/RSS peak."
        ),
        "legacy_hash": original.identifier,
        "transport_hash": archive.transport_hash,
        "run_hash": d["run_hash"],
        "performance_hash": d["performance_hash"],
        "report_hash": d["detailed_report_hash"],
        "exact_full_result_equal": True,
        "source_file_unchanged": True,
        "scope": report.get("scope", "synthetic_fixture"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(args.results) <= 4:
        parser.error("provide 1–4 bounded result files")
    if args.output.resolve() in {p.resolve() for p in args.results}:
        parser.error("output must not overwrite source evidence")
    rows = []
    for path in args.results:
        rows.append(measure(path, trace=args.trace))
        args.output.write_text(
            json.dumps(
                {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "runs": rows,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
