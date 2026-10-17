"""Offline evidence-only benchmark; full trading equivalence is tested separately.

Run: PYTHONPATH=src python -m tests.backtest.shared_evidence_benchmark
No protected sources, network, future-performance tuning or timing assertions.
"""

import gc
import json
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as midnight
from functools import partial
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.availability import FrozenAnalysis
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.hgb_provider import HGBHistoricalProvider
from donghak_stock_vision.backtest.shared_evidence import EvidenceBuilder
from donghak_stock_vision.backtest.validation import instant, require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.models.inference import ResearchPolicy
from donghak_stock_vision.signals.domain import SignalPolicy
from donghak_stock_vision.signals.service import SignalService
from donghak_stock_vision.storage.analysis import (
    AnalysisStore,
    ReadOnlyAnalysisStore,
    ResearchRunStore,
)
from donghak_stock_vision.storage.sqlite import SQLiteStore
from tests.backtest.test_hgb_trading import genuine_resolver
from tests.backtest.test_public_inference import scored_runner
from tests.learning.helpers import CUTOFF


def generated(root: Path, count: int) -> dict[str, list[dict[str, Any]]]:
    require(type(count) is int and 1 <= count <= 100, "bounded_benchmark_required")
    root.mkdir(parents=True)
    base = scored_runner(root)
    template = base.analyses[0].bundle.to_dict()
    days = [date.fromisoformat(r["trading_date"]) for r in template["snapshot"]["rows"]["005930"]][
        50 : 50 + count
    ]
    source = ReadOnlyAnalysisStore(root / "source.db")
    output = AnalysisStore(root / "benchmark-output.db")
    store = ResearchRunStore(source, output)
    market = SQLiteStore(root / "market.db")
    provider = HGBHistoricalProvider(
        genuine_resolver(root / "trusted"),
        SignalPolicy(
            "benchmark", ResearchPolicy("benchmark", frozenset(template["signal"]["quality_flags"]))
        ),
        CUTOFF,
    )
    linear, hgb = [], []
    for day in days:
        signal = SignalService(store).research(
            template["model_id"], template["snapshot_id"], ["005930"], day
        )[0]
        aid = signal["analysis_id"]
        linear.append(
            {
                **template,
                "signal": store.get("signal", aid),
                "analysis_id": aid,
                "created_at": {
                    **template["created_at"],
                    "signal": instant(store.created_at("signal", aid)),
                },
            }
        )
        hgb.append(
            provider.capture(
                market,
                ticker="005930",
                as_of=datetime.combine(day + timedelta(days=1), midnight(), SEOUL),
                history_start=date.fromisoformat(template["snapshot"]["start"]),
                source_cutoff=CUTOFF,
                quality=base.tape.quality.to_dict(),
                calendar=None,
                synthetic=True,
            )
        )
    source.close()
    output.close()
    market.close()
    (root / "manifest.json").write_text(base.tape.manifest.payload_json)
    return {"linear": linear, "hgb": hgb}


def context(
    template: dict[str, Any], bundles: list[dict[str, Any]]
) -> tuple[RunManifest, FrozenJSON]:
    m = json.loads(json.dumps(template))
    provenance = {k: m[k] for k in ("information_mode", "data_origin", "usage_restriction")}

    def ref(identity: str) -> dict[str, Any]:
        return {
            **provenance,
            "artifact_id": identity,
            "content_hash": identity,
            "version": "synthetic-benchmark-v1",
        }

    releases = [
        {
            "model_id": b["model_id"],
            "snapshot_id": b["snapshot_id"],
            "analysis_id": b["analysis_id"],
            "available_at": b["signal"]["anchor_at"],
            "sequence": i + 1,
        }
        for i, b in enumerate(bundles)
    ]
    plan = FrozenJSON.freeze({**provenance, "analysis_releases": releases, "releases": []})
    m.update(
        availability_assumption_id=plan.identifier,
        models=[ref(x) for x in sorted({b["model_id"] for b in bundles})],
        analyses=[ref(b["analysis_id"]) for b in bundles],
        market_snapshots=[ref(x) for x in sorted({b["snapshot_id"] for b in bundles})],
        start_at=releases[0]["available_at"],
        end_at=releases[-1]["available_at"],
    )
    m["initial_account"].update(observed_at=m["start_at"], received_at=m["start_at"])
    return RunManifest.from_dict(m), plan


def encode(bundles: list[dict[str, Any]], manifest: RunManifest, *, shared: bool) -> FrozenJSON:
    if not shared:
        return FrozenJSON.freeze({"version": 1, "analyses": bundles})
    builder = EvidenceBuilder()
    for bundle in bundles:
        builder.add(bundle)
    return FrozenJSON.freeze(
        {
            "version": 2,
            "analyses": builder.records,
            "evidence_table": builder.table(manifest.identifier),
        }
    )


def validate(
    document: FrozenJSON, manifest: RunManifest, plan: FrozenJSON
) -> tuple[FrozenAnalysis, ...]:
    if document.to_dict()["version"] == 2:
        return document.shared_evidence.analyses(manifest, plan)
    return tuple(
        FrozenAnalysis(manifest, plan, FrozenJSON.freeze(b)) for b in document.to_dict()["analyses"]
    )


def measured(function: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    result = function()
    return result, time.perf_counter() - start


def peak(function: Callable[[], Any]) -> int:
    gc.collect()
    tracemalloc.start()
    result = function()
    _, value = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del result
    return value


def benchmark(root: Path, counts: tuple[int, ...] = (1, 10, 50, 100)) -> list[dict[str, Any]]:
    require(
        bool(counts) and all(type(n) is int and 1 <= n <= 100 for n in counts),
        "bounded_benchmark_required",
    )
    families = generated(root, max(counts))
    template = json.loads((root / "manifest.json").read_text())
    result = []
    for family, all_bundles in families.items():
        for n in counts:
            bundles = all_bundles[:n]
            assert len({b["analysis_id"] for b in bundles}) == n
            manifest, plan = context(template, bundles)
            row: dict[str, Any] = {
                "family": family,
                "decision_dates": n,
                "distinct_model_references": len({b["model_id"] for b in bundles}),
                "distinct_model_artifacts": len(
                    {
                        mid
                        for b in bundles
                        for mid in (
                            b["signal"]["hgb_provenance"]["model_pair"].values()
                            if family == "hgb"
                            else [b["model_id"]]
                        )
                    }
                ),
                "distinct_snapshots": len({b["snapshot_id"] for b in bundles}),
            }
            hashes = []
            for shared in (False, True):
                label = "shared" if shared else "legacy"
                document, serialize = measured(partial(encode, bundles, manifest, shared=shared))
                parsed, deserialize = measured(partial(FrozenJSON, document.payload_json))
                analyses, replay = measured(partial(validate, parsed, manifest, plan))
                identities = [a.bundle.identifier for a in analyses]
                hashes.append(identities)
                assert identities == [digest(b) for b in bundles]

                record_count = len(analyses)
                del analyses, parsed

                def load(
                    payload: str = document.payload_json,
                    run_manifest: RunManifest = manifest,
                    release_plan: FrozenJSON = plan,
                ) -> tuple[FrozenAnalysis, ...]:
                    return validate(FrozenJSON(payload), run_manifest, release_plan)

                row[label] = {
                    "evidence_bytes": len(document.payload_json.encode()),
                    "artifact_count": len(
                        document.to_dict().get("evidence_table", {}).get("artifacts", [])
                    ),
                    "record_count": record_count,
                    "serialize_seconds": serialize,
                    "deserialize_seconds": deserialize,
                    "validate_seconds": replay,
                    "python_peak_load_validate_bytes": peak(load) if n <= 50 else None,
                    "memory_scope": "Python allocations only; profiling capped at 50 decisions",
                }
                del document
            assert hashes[0] == hashes[1]
            result.append(row)
            print(json.dumps(row), flush=True)
    return result


if __name__ == "__main__":
    target = Path(tempfile.mkdtemp(prefix="dsv-shared-benchmark-")) / "fixtures"
    benchmark(target)
