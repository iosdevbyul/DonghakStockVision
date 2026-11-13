"""Offline dataset composition using pinned inputs and the existing training store."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore
from donghak_stock_vision.storage.krx_backfill import read_pin, write_new
from donghak_stock_vision.storage.real_market_dataset import (
    document,
    prepare_dataset,
    train_dataset,
)


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser("real-market-dataset")
    command.add_argument("action", choices=("prepare", "train", "verify"))
    command.add_argument("--request", type=Path, required=True)
    command.add_argument("--request-pin-file", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pins-root", type=Path)


def run(args: argparse.Namespace) -> int:
    d = document(str(args.request))
    require(digest(d) == read_pin(args.request_pin_file), "dataset_request_pin_mismatch")
    require(args.pins_root is not None, "dataset_independent_pins_required")
    if args.action == "verify":
        snapshot, manifest = prepare_dataset(d)
        if args.output.is_dir():
            require(
                document(str(args.output / "snapshot.json"), 16 * 1024**2) == snapshot.to_dict()
                and document(str(args.output / "manifest.json"), 8 * 1024**2) == manifest.to_dict(),
                "dataset_reconstruction_mismatch",
            )
        else:
            with closing(ReadOnlyAnalysisStore(args.output)) as store:
                require(
                    store.get("snapshot", snapshot.identifier) == snapshot.to_dict(),
                    "dataset_snapshot_reconstruction_mismatch",
                )
                require(
                    store.get("real_market_dataset", manifest.identifier) == manifest.to_dict(),
                    "dataset_manifest_reconstruction_mismatch",
                )
        for name, expected in (
            ("source", digest(manifest.to_dict()["source"])),
            ("manifest", manifest.identifier),
            ("snapshot", snapshot.identifier),
        ):
            require(
                read_pin(args.pins_root / (name + ".pin")) == expected,
                "dataset_retained_pin_mismatch",
            )
        print('{"verified":true,"network_requests":0}')
        return 0
    require(args.pins_root is not None, "dataset_independent_pins_required")
    pins: Path = args.pins_root
    require(
        pins.is_absolute()
        and pins.resolve() == pins
        and not pins.exists()
        and not args.output.is_relative_to(pins)
        and not pins.is_relative_to(args.output),
        "dataset_separate_new_pin_directory_required",
    )
    if args.action == "train":
        result = train_dataset(d, args.output)
        with closing(ReadOnlyAnalysisStore(args.output)) as store:
            manifest_value = store.get("real_market_dataset", result.to_dict()["manifest_id"])
        source_pin = digest(manifest_value["source"])
        manifest_pin = result.to_dict()["manifest_id"]
        snapshot_pin = result.to_dict()["snapshot_id"]
        value = result.to_dict()
        value["can_train"] = manifest_value["can_train"]
    else:
        snapshot, manifest = prepare_dataset(d)
        require(not args.output.exists(), "new_dataset_output_required")
        args.output.mkdir(mode=0o700, parents=True)
        write_new(args.output / "snapshot.json", snapshot.payload_json)
        write_new(args.output / "manifest.json", manifest.payload_json)
        source_pin = digest(manifest.to_dict()["source"])
        manifest_pin, snapshot_pin = manifest.identifier, snapshot.identifier
        value = dict(
            dataset_prepared=True,
            can_train=manifest.to_dict()["can_train"],
            observations=manifest.to_dict()["observations"],
            network_requests=0,
        )
    pins.mkdir(mode=0o700, parents=True)
    for name, pin in (
        ("source", source_pin),
        ("manifest", manifest_pin),
        ("snapshot", snapshot_pin),
    ):
        write_new(pins / (name + ".pin"), pin + "\n")
    # Identifiers/pins and private source paths are retained in artifacts, not stdout.
    print(
        json.dumps(
            dict(
                dataset_prepared=True,
                training_status=value.get("training", {}).get("status"),
                model_trained=bool(value.get("training", {}).get("model_version")),
                can_train=value.get("can_train"),
                performance_approved=False,
                pit_qualified=False,
                network_requests=0,
            )
        )
    )
    return int(args.action == "train" and not value.get("training", {}).get("model_version"))
