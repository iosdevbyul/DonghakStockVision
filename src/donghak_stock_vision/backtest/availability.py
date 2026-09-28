"""Freeze public Phase 2 artifacts; reject unavailable PIT evidence, never infer."""

from dataclasses import dataclass
from typing import Any

from donghak_stock_vision.backtest.clock import VirtualClock
from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import PROVENANCE, FrozenJSON, PublicView
from donghak_stock_vision.backtest.validation import fields, instant, integer, require, utc
from donghak_stock_vision.data.learning import digest, event_contract
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.storage.analysis import AnalysisStore


@dataclass(frozen=True)
class FrozenAnalysis:
    """All input metadata lives in the bundle; replay performs no store access."""

    manifest: RunManifest
    release_plan: FrozenJSON
    bundle: FrozenJSON

    def __post_init__(self) -> None:
        m, b = self.manifest.to_dict(), self.bundle.to_dict()
        fields(
            b,
            "model_id snapshot_id analysis_id model snapshot training_snapshot dataset signal "
            "created_at",
        )
        for kind, key in (
            ("model", "model_id"),
            ("snapshot", "snapshot_id"),
            ("signal", "analysis_id"),
        ):
            require(digest(b[kind]) == b[key], "artifact_checksum_mismatch")
        require(
            digest(b["training_snapshot"]) == b["model"]["snapshot_id"],
            "training_snapshot_mismatch",
        )
        require(digest(b["dataset"]) == b["model"]["dataset_id"], "dataset_mismatch")
        require(
            b["dataset"]["snapshot_id"] == b["model"]["snapshot_id"], "dataset_snapshot_mismatch"
        )
        require(digest(b["dataset"]["split"]) == b["model"]["split_hash"], "split_mismatch")
        for list_key, id_key in (
            ("models", "model_id"),
            ("analyses", "analysis_id"),
            ("market_snapshots", "snapshot_id"),
        ):
            require(
                any(
                    r["artifact_id"] == b[id_key] and r["content_hash"] == b[id_key]
                    for r in m[list_key]
                ),
                "undeclared_artifact",
            )
        for record in (b["model"], b["snapshot"], b["training_snapshot"], b["signal"]):
            for key in PROVENANCE:
                require(
                    record["mode" if key == "information_mode" else key] == m[key],
                    "artifact_provenance_mismatch",
                )
        s = b["signal"]
        require(
            s["model_version"] == b["model_id"] and s["snapshot_id"] == b["snapshot_id"],
            "analysis_identity_mismatch",
        )
        require(s["snapshot_as_of"] == b["snapshot"]["cutoff"], "snapshot_cutoff_mismatch")
        for key, value in event_contract(b["snapshot"]["session_basis"]).items():
            require(s[key] == value, "analysis_contract_mismatch")
        if m["information_mode"] == "historical_research":
            require(b["model"]["snapshot_id"] == b["snapshot_id"], "snapshot_mismatch")
            require(
                self.release_plan.identifier == m["availability_assumption_id"],
                "assumption_mismatch",
            )
        fields(b["created_at"], "model snapshot training_snapshot dataset signal")
        for value in b["created_at"].values():
            utc(value)
        require(
            utc(s["model_created_at"]) == utc(b["created_at"]["model"]), "model_timestamp_mismatch"
        )
        plan = self.release_plan.to_dict()
        for key in PROVENANCE:
            require(plan[key] == m[key], "plan_provenance_mismatch")
        selected = [
            r for r in plan["analysis_releases"] if r.get("analysis_id") == b["analysis_id"]
        ]
        require(len(selected) == 1, "analysis_release_evidence_missing")
        r = fields(selected[0], "model_id snapshot_id analysis_id available_at sequence")
        for key in ("model_id", "snapshot_id", "analysis_id"):
            require(r[key] == b[key], "analysis_release_identity_mismatch")
        integer(r["sequence"])
        require(utc(s["anchor_at"]) <= utc(r["available_at"]), "analysis_release_before_anchor")

    @classmethod
    def capture(
        cls,
        store: AnalysisStore,
        manifest: RunManifest,
        release_plan: FrozenJSON,
        *,
        model_id: str,
        snapshot_id: str,
        analysis_id: str,
    ) -> "FrozenAnalysis":
        model = load_model(store, model_id, manifest.to_dict()["information_mode"])
        targets = {
            "model": ("model", model_id),
            "snapshot": ("snapshot", snapshot_id),
            "training_snapshot": ("snapshot", model["snapshot_id"]),
            "dataset": ("dataset", model["dataset_id"]),
            "signal": ("signal", analysis_id),
        }
        bundle: dict[str, Any] = {
            "model_id": model_id,
            "snapshot_id": snapshot_id,
            "analysis_id": analysis_id,
        }
        bundle.update({key: store.get(kind, aid) for key, (kind, aid) in targets.items()})
        bundle["created_at"] = {
            key: instant(store.created_at(kind, aid)) for key, (kind, aid) in targets.items()
        }
        return cls(manifest, release_plan, FrozenJSON.freeze(bundle))

    def view(self, clock: VirtualClock) -> PublicView:
        m, b = self.manifest.to_dict(), self.bundle.to_dict()
        s = b["signal"]
        cutoff = utc(clock.cutoff)
        start, end = utc(m["start_at"]), utc(m["end_at"])
        require(
            start <= cutoff <= end
            if m["time_basis"]["boundary"] == "inclusive"
            else start < cutoff < end,
            "outside_run_period",
        )
        r = next(
            r
            for r in self.release_plan.to_dict()["analysis_releases"]
            if r["analysis_id"] == b["analysis_id"]
        )
        reason = None
        if utc(r["available_at"]) > cutoff or r["sequence"] > clock.sequence:
            reason = "analysis_not_released"
        elif utc(s["anchor_at"]) > cutoff:
            reason = "future_analysis_anchor"
        elif m["information_mode"] == "point_in_time":
            if utc(b["created_at"]["model"]) > utc(s["anchor_at"]):
                reason = "model_not_available_at_feature_cutoff"
            elif utc(b["created_at"]["signal"]) > cutoff:
                reason = "analysis_artifact_not_available"
            elif any(
                utc(b["created_at"][key]) > cutoff
                for key in ("snapshot", "training_snapshot", "dataset")
            ):
                reason = "dependency_artifact_not_available"
            elif any(
                row.get("label_available_at") is None
                or utc(row["label_available_at"]) > utc(b["created_at"]["model"])
                for row in b["dataset"]["samples"]
                if row.get("label") is not None
            ):
                reason = "future_label_dependency"
            else:
                # Public Phase 2 APIs expose timestamps, but no immutable model-selection receipt
                # or revision archive proof. Never treat a manually supplied approval as evidence.
                reason = "pit_selection_and_revision_evidence_unavailable"
        items = []
        if reason is None:
            keys = (
                "ticker",
                "anchor_at",
                "input_status",
                "context",
                "up_score",
                "down_score",
                "up_status",
                "down_status",
                "signal_state",
                "last_trading_date",
                "latest_collected_at",
                "quality_flags",
                "event_description",
                "calibration",
            )
            item = {key: s[key] for key in keys}
            for key in ("anchor_at", "latest_collected_at"):
                if item[key] is not None:
                    item[key] = instant(item[key])
            items = [item]
        payload = {
            **{k: m[k] for k in PROVENANCE},
            "execution_mode": m["execution_mode"],
            "scope": "research",
            "executable": False,
            "operational_eligible": False,
            "cutoff": clock.cutoff,
            "sequence": clock.sequence,
            "status": "blocked" if reason else "research_only",
            "reason": reason,
            "items": items,
        }
        return PublicView(
            FrozenJSON.freeze(payload),
            FrozenJSON.freeze(
                {
                    "run_id": self.manifest.identifier,
                    "bundle_id": self.bundle.identifier,
                    "model_id": b["model_id"],
                    "snapshot_id": b["snapshot_id"],
                    "analysis_id": b["analysis_id"],
                    "anchor_at": s["anchor_at"],
                    "created_at": b["created_at"],
                }
            ),
        )
