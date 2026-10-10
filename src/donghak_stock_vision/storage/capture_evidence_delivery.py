"""Offline, explicit reconciliation of local collection receipts to an export directory."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from donghak_stock_vision.storage.capture_evidence_sink import (
    export_capture_receipt,
    verify_exported_capture_receipt,
)


@dataclass(frozen=True)
class ReceiptDelivery:
    raw_id: int
    status: Literal["delivered", "recovered", "blocked"]
    reason: str | None = None


def reconcile_capture_receipts(
    connection: sqlite3.Connection,
    destination: Path,
    *,
    limit: int = 100,
    after_raw_id: int = 0,
) -> tuple[ReceiptDelivery, ...]:
    """Retry missing exports, verify existing ones, and refuse conflicts.

    Use the last returned raw_id as after_raw_id for the next page. A blocked
    receipt is still included in the result and must not be treated as delivered.
    Delivery state is reconstructed from saved receipts and exported files.
    Missing legacy receipts are never generated retroactively. This function
    does not authenticate provenance or issue operational authority.
    """
    if not 1 <= limit <= 10000:
        raise ValueError("limit out of range")
    if after_raw_id < 0:
        raise ValueError("after_raw_id must be nonnegative")
    if not destination.is_dir() or destination.is_symlink():
        raise ValueError("capture evidence destination must be an existing real directory")

    raw_ids = [
        row[0]
        for row in connection.execute(
            "SELECT raw_id FROM raw_capture_receipts WHERE raw_id > ? "
            "ORDER BY raw_id LIMIT ?",
            (after_raw_id, limit),
        )
    ]
    results: list[ReceiptDelivery] = []
    for raw_id in raw_ids:
        exported = destination / f"raw-{raw_id}.json"
        if exported.is_symlink():
            results.append(ReceiptDelivery(raw_id, "blocked", "unsafe_export_path"))
        elif exported.exists():
            status = (
                ReceiptDelivery(raw_id, "delivered")
                if verify_exported_capture_receipt(connection, raw_id, destination)
                else ReceiptDelivery(raw_id, "blocked", "export_conflict")
            )
            results.append(status)
        else:
            try:
                export_capture_receipt(connection, raw_id, destination)
            except (OSError, ValueError, sqlite3.Error):
                results.append(ReceiptDelivery(raw_id, "blocked", "export_failed"))
            else:
                results.append(ReceiptDelivery(raw_id, "recovered"))
    return tuple(results)
