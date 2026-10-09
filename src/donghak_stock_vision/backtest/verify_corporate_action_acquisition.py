"""Offline replay for corporate-action acquisition sources; no event approval."""

import argparse
import json
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.corporate_action_acquisition import (
    build_acquisition_manifest,
    build_document_plan,
    build_security_review_queue,
    inspect_document_sources,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    require(type(value) is dict, "metadata_object_required")
    return dict(value)


def wrapped(value: FrozenJSON) -> dict[str, Any]:
    artifact = value.to_dict()
    return {"artifact": artifact, "fingerprint": digest(artifact)}


def verify(root: Path, sources: Path) -> dict[str, dict[str, Any]]:
    corporate_actions = read(
        root / "docs/evidence/baseline-dart/artifacts/corporate-action-events.json"
    )
    mapping = read(root / "docs/evidence/baseline-dart/artifacts/dart-corporation-mapping.json")
    historical_identities = read(
        root / "docs/evidence/baseline-security-lifecycle/artifacts/"
        "historical-security-identities.json"
    )
    dart_manifest = read(root / "docs/evidence/baseline-dart/artifacts/dart-evidence-manifest.json")
    lifecycle_manifest = read(
        root / "docs/evidence/baseline-security-lifecycle/artifacts/"
        "security-lifecycle-manifest.json"
    )
    integrated_lifecycle = read(
        root / "docs/evidence/baseline-security-lifecycle/artifacts/"
        "integrated-security-evidence.json"
    )

    plan = build_document_plan(corporate_actions)
    source_inventory = inspect_document_sources(plan, sources)
    queue = build_security_review_queue(mapping, historical_identities, plan)
    manifest = build_acquisition_manifest(
        dart_manifest,
        lifecycle_manifest,
        integrated_lifecycle,
        plan,
        source_inventory,
        queue,
    )
    return {
        "document-plan": wrapped(plan),
        "document-sources": wrapped(source_inventory),
        "security-review-queue": wrapped(queue),
        "acquisition-manifest": wrapped(manifest),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "output_must_be_new_directory")
    result = verify(args.root.resolve(), args.sources.resolve())
    args.output.mkdir(parents=True)
    for name, value in result.items():
        (args.output / (name + ".json")).write_text(
            json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    print(
        json.dumps(
            {name: value["fingerprint"] for name, value in result.items()},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
