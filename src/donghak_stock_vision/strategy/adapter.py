"""Read-only Phase 2 adapter. Never constructs or updates a market/model artifact."""

import sqlite3
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import AnalysisError, digest, event_contract, timestamp
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.strategy.contracts import DecisionInput


class ReadOnlyAnalysisStore(AnalysisStore):
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.connection.execute("PRAGMA query_only=ON")


def assemble(
    request: dict[str, Any],
    analysis_path: Path,
    account: dict[str, Any] | None,
    orders: dict[str, Any] | None,
    market: dict[str, Any] | None,
) -> DecisionInput:
    bundle: dict[str, Any] = {
        "request": request,
        "account": account,
        "orders": orders,
        "market": market,
    }
    DecisionInput.from_dict(bundle)  # Validate identity even when operational is blocked.
    if request["scope"] == "operational":
        # No registry/approval path, including when the supplied DB contains forged approvals.
        return DecisionInput.from_dict(bundle)
    store = None
    try:
        store = ReadOnlyAnalysisStore(analysis_path)
        row = store.get("signal", request["analysis_id"])
        model = load_model(store, row["model_version"], row["mode"])
        snapshot = store.get("snapshot", row["snapshot_id"])
        for key in ("mode", "data_origin", "usage_restriction", "anchor_policy", "session_basis"):
            if row[key] != model[key] or row[key] != snapshot[key]:
                raise AnalysisError("analysis_provenance_mismatch")
        for key, value in event_contract(snapshot["session_basis"]).items():
            if row[key] != value:
                raise AnalysisError("analysis_contract_mismatch")
        if row["mode"] == "historical_research" and row["snapshot_id"] != model["snapshot_id"]:
            raise AnalysisError("snapshot_mismatch")
        if timestamp(row["model_created_at"]) != store.created_at("model", row["model_version"]):
            raise AnalysisError("model_timestamp_mismatch")
        if row["snapshot_as_of"] != snapshot["cutoff"]:
            raise AnalysisError("snapshot_cutoff_mismatch")
        ticker_rows = snapshot["rows"].get(row["ticker"], [])
        bases = {b["adjustment"] for b in ticker_rows}
        if len(bases) != 1:
            raise AnalysisError("price_basis_unavailable")
        bundle.update(
            analysis=row,
            price_basis=next(iter(bases)),
            provenance={
                "analysis_id": request["analysis_id"],
                "artifact_available_at": store.created_at(
                    "signal", request["analysis_id"]
                ).isoformat(),
                "model_version": row["model_version"],
                "snapshot_id": row["snapshot_id"],
                "dataset_id": model["dataset_id"],
                "model_hash": digest(model),
                "quality_manifest_hash": digest(snapshot["quality"]),
                "source": "phase2_read_only",
                "virtual_context": True,
            },
        )
    except AnalysisError as error:
        bundle["assembly_error"] = str(error)
    except (OSError, sqlite3.Error):
        bundle["assembly_error"] = "analysis_storage_unavailable"
    except (KeyError, TypeError, ValueError):
        bundle["assembly_error"] = "invalid_analysis_artifact"
    finally:
        if store is not None:
            store.close()
    return DecisionInput.from_dict(bundle)
