"""Explicit offline six-study publication export; never enables the HTTP server.

Operators review/retain the printed index hash independently of the output root.
The exported database is a quiescent SQLite backup, never the active research DB.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.storage.autonomous_research import DB, context
from donghak_stock_vision.storage.research_lab_publication import (
    VERSION,
    VISIBILITY_INDEX,
    ResearchLabPublication,
)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="dsv-research-lab-export")
    command.add_argument(
        "--study",
        action="append",
        default=[],
        help="absolute study root followed by colon and existing 64-character plan ID",
    )
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--visibility-request", type=Path)
    command.add_argument("--visibility-request-pin-file", type=Path)
    return command


def export(
    studies: list[str], output: Path, *, visibility_data: bytes | None = None
) -> dict[str, Any]:
    if (
        not 0 <= len(studies) <= 24
        or (not studies and visibility_data is None)
        or output.resolve() != output
        or not output.is_absolute()
        or output.exists()
        or output.is_symlink()
        or not output.parent.is_dir()
    ):
        raise ValueError("invalid_publication_output")
    verified = []
    for item in studies:
        try:
            name, study_id = item.rsplit(":", 1)
        except ValueError as error:
            raise ValueError("invalid_study_selector") from error
        root = Path(name)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("invalid_study_root")
        plan, _ = context(root, study_id, code_check=False)
        if plan["version"] != "autonomous_plan_v6":
            raise ValueError("unsupported_publication_study")
        source = root / DB
        report = root / "final-research-report.json"
        if not report.is_file() or report.is_symlink() or source.is_symlink():
            raise ValueError("missing_private_research_report")
        verified.append((source, report, study_id))
    if len({item[2] for item in verified}) != len(verified):
        raise ValueError("duplicate_publication_study")
    output.mkdir(mode=0o700)
    entries = []
    for i, (source, report, study_id) in enumerate(verified):
        snapshot_name = f"study-{i:02d}.sqlite3"
        report_name = f"report-{i:02d}.json"
        destination = output / snapshot_name
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(destination)) as dest,
        ):
            src.backup(dest)
        os.chmod(destination, 0o600)
        report_content = report.read_bytes()
        report_destination = output / report_name
        with report_destination.open("xb") as stream:
            os.chmod(report_destination, 0o600)
            stream.write(report_content)
        entries.append(
            {
                "studyId": study_id,
                "snapshot": snapshot_name,
                "snapshotSha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "report": report_name,
                "reportSha256": hashlib.sha256(report_content).hexdigest(),
            }
        )
    index: dict[str, Any] = {"version": VERSION, "entries": entries}
    if visibility_data is not None:
        from donghak_stock_vision.storage.research_lab_publication import MAX_VISIBILITY

        if len(visibility_data) > MAX_VISIBILITY:
            raise ValueError("visibility_publication_bound")
        view_file = output / "visibility.json"
        with view_file.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(visibility_data)
        index.update(
            version=VISIBILITY_INDEX,
            visibility=dict(
                name=view_file.name, sha256=hashlib.sha256(visibility_data).hexdigest()
            ),
        )
    manifest = output / "index.json"
    with manifest.open("x", encoding="utf-8") as stream:
        os.chmod(manifest, 0o600)
        json.dump(index, stream, sort_keys=True)
    index_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    ResearchLabPublication(output, index_sha256)
    return {
        "status": "OFFLINE_EXPORT_READY_FOR_INDEPENDENT_OPERATOR_APPROVAL",
        "indexSha256": index_sha256,
        "studyCount": len(entries),
        "httpServingEnabled": False,
        "researchExecutionTriggered": False,
        "protectedFinalTestRead": False,
    }


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if bool(args.visibility_request) != bool(args.visibility_request_pin_file):
        parser().error("visibility request and independent PIN must be supplied together")
    data = None
    if args.visibility_request is not None:
        from donghak_stock_vision.storage.research_lab_evidence import _pinned, visibility

        request = _pinned(str(args.visibility_request), str(args.visibility_request_pin_file))

        def paths(value: Any) -> list[Path]:
            if isinstance(value, dict):
                return [p for child in value.values() for p in paths(child)]
            if isinstance(value, list):
                return [p for child in value for p in paths(child)]
            return [Path(value)] if isinstance(value, str) and value.startswith("/") else []

        if any(args.output.resolve().is_relative_to(p.parent.resolve()) for p in paths(request)):
            parser().error("visibility output must be separate from source owners")
        data = visibility(request).model_dump_json(exclude_none=True).encode()
    print(json.dumps(export(args.study, args.output, visibility_data=data), sort_keys=True))


if __name__ == "__main__":
    main()
