"""Pinned local research artifacts: verify provenance before deserializing bytes."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, digest, event_contract
from donghak_stock_vision.models.inference import HGBArtifact, HGBInference


@dataclass(frozen=True)
class VerifiedHGB:
    artifact: HGBArtifact
    checkpoint: bytes
    calendar_json: str

    def load(self) -> HGBInference:
        return HGBInference(
            self.artifact, self.checkpoint, trusted_artifact_fingerprint=self.artifact.fingerprint
        )


class TrustedHGBResolver:
    """Root and manifest pins must be supplied by trusted local configuration.

    No caller-selected pickle filename, remote download or model discovery.
    SHA checks are not authentication of an untrusted pin or a pickle sandbox.
    """

    def __init__(
        self, root: Path, up: HGBArtifact, down: HGBArtifact, *, up_pin: str, down_pin: str
    ) -> None:
        try:
            self.root = root.resolve(strict=True)
        except OSError as error:
            raise AnalysisError("missing_artifact_root") from error
        self.artifacts = (up, down)
        self.pins = (up_pin, down_pin)
        for direction, artifact, pin in zip(("up", "down"), self.artifacts, self.pins, strict=True):
            if (
                type(pin) is not str
                or len(pin) != 64
                or any(c not in "0123456789abcdef" for c in pin)
            ):
                raise AnalysisError("invalid_artifact_pin")
            if artifact.direction != direction or artifact.fingerprint != pin:
                raise AnalysisError("untrusted_artifact")

    def _read(self, name: str) -> bytes:
        try:
            path = self.root / name
            if (
                path.is_symlink()
                or path.resolve(strict=True).parent != self.root
                or not path.is_file()
            ):
                raise AnalysisError("invalid_artifact_path")
            return path.read_bytes()
        except OSError as error:
            raise AnalysisError("missing_artifact") from error

    def _json(self, name: str) -> dict[str, Any]:
        raw = self._read(name)
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError) as error:
            raise AnalysisError("malformed_artifact") from error

    def verify(self) -> tuple[VerifiedHGB, VerifiedHGB]:
        from donghak_stock_vision.data.learning import canonical

        lock = self._json("lock.json")
        qualification = self._json("qualification.json")
        plan = self._json("plan.json")
        try:
            lock_id = lock.pop("lock_id")
            qualification_id = qualification.pop("report_id")
            if digest(lock) != lock_id or digest(qualification) != qualification_id:
                raise AnalysisError("provenance_hash_mismatch")
            if digest(plan) != lock["plan_id"] or qualification["lock_id"] != lock_id:
                raise AnalysisError("experiment_link_mismatch")
            if lock["features"] != list(FEATURES) or plan["features"] != list(FEATURES):
                raise AnalysisError("feature_contract_mismatch")
            for metadata in (lock, plan):
                if any(
                    metadata.get(k) != v for k, v in event_contract(lock["session_basis"]).items()
                ):
                    raise AnalysisError("feature_label_contract_mismatch")
                if (
                    metadata["seed"] != 42
                    or metadata["research_calendar"] != lock["research_calendar"]
                ):
                    raise AnalysisError("experiment_configuration_mismatch")
            if (
                qualification["usage_restriction"] != "research_only"
                or qualification["operational_status"] != "unregistered"
                or lock["usage_restriction"] != "research_only"
                or lock["operational_status"] != "unregistered"
            ):
                raise AnalysisError("research_artifact_required")
            verified = []
            for direction, artifact, pin in zip(
                ("up", "down"), self.artifacts, self.pins, strict=True
            ):
                if (
                    artifact.fingerprint != pin
                    or artifact.experiment_lock_id != lock_id
                    or artifact.qualification_id != qualification_id
                    or artifact.source_snapshot_id != lock["training_snapshot_id"]
                    or artifact.features != tuple(lock["features"])
                    or artifact.feature_version != lock["feature_version"]
                    or artifact.label_version != lock["label_version"]
                ):
                    raise AnalysisError("artifact_provenance_mismatch")
                selected = lock["directions"][direction]
                qualified = qualification["directions"][direction]
                expected = {
                    "name": "hist_leaves7" if direction == "up" else "hist_leaves15",
                    "family": "hist_gradient_boosting",
                    "value": 7 if direction == "up" else 15,
                }
                if (
                    selected["candidate"] != expected
                    or qualified["candidate"] != expected
                    or qualified["qualification"] != "validation_gate_passed"
                    or selected["estimator_hash"] != artifact.checkpoint_sha256
                ):
                    raise AnalysisError("locked_model_mismatch")
                params = json.loads(artifact.parameters_json)
                if any(params.get(k) != v for k, v in plan["hgb"].items()) or not plan["hgb"]:
                    raise AnalysisError("configuration_mismatch")
                versions = plan["provenance"]["versions"]
                if any(versions[p] != v for p, v in artifact.runtime_versions):
                    raise AnalysisError("runtime_provenance_mismatch")
                checkpoint = self._read(f"{direction}-research-only.pkl")
                if hashlib.sha256(checkpoint).hexdigest() != artifact.checkpoint_sha256:
                    raise AnalysisError("checkpoint_mismatch")
                verified.append(
                    VerifiedHGB(artifact, checkpoint, canonical(lock["research_calendar"]))
                )
            return verified[0], verified[1]
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            if isinstance(error, AnalysisError):
                raise
            raise AnalysisError("malformed_provenance") from error
