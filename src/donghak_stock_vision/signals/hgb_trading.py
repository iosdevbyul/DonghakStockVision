"""Explicit HGB research evidence projection; no policy, fitting or execution math."""

import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Any

from donghak_stock_vision.data.learning import FEATURES, canonical, digest, event_contract
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import snapshot_bars, validate_quality
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.inference import HGBArtifact
from donghak_stock_vision.signals.domain import (
    DirectionalSignal,
    SignalCandidate,
    SignalContext,
    SignalDirection,
    SignalStrength,
)
from donghak_stock_vision.signals.integration import IntegratedResearchSignal

VERSION = "hgb_historical_analysis_v1"


def projection(evidence: dict[str, Any]) -> dict[str, Any]:
    """Validate the public envelope and reproduce only factual decision fields.

    This is integrity replay, not checkpoint deserialization or score re-inference.
    Artifact pins must originate from trusted configuration at acquisition time.
    """
    c, snapshot = evidence["candidate"], evidence["snapshot"]
    context = dict(c["context"])
    for key in ("as_of", "data_cutoff"):
        context[key] = datetime.fromisoformat(context[key])
    context["quality_flags"] = tuple(context["quality_flags"])
    ctx = SignalContext(**context)
    directions = []
    artifacts = {}
    provenance = evidence["experiment_provenance"]
    lock, qual, plan = (dict(provenance[k]) for k in ("lock", "qualification", "plan"))
    lid, qid = lock.pop("lock_id"), qual.pop("report_id")
    if (
        digest(lock) != lid
        or digest(qual) != qid
        or digest(plan) != lock["plan_id"]
        or qual["lock_id"] != lid
        or lock["research_calendar"] != snapshot.get("research_calendar")
        or lock["usage_restriction"] != "research_only"
        or qual["usage_restriction"] != "research_only"
        or lock["operational_status"] != "unregistered"
        or qual["operational_status"] != "unregistered"
    ):
        raise ValueError("hgb_experiment_evidence_mismatch")
    for side in ("up", "down"):
        raw = dict(evidence["models"][side])
        raw["features"] = tuple(raw["features"])
        raw["runtime_versions"] = tuple(tuple(p) for p in raw["runtime_versions"])
        artifact = HGBArtifact(**raw)
        if (
            artifact.experiment_lock_id != lid
            or artifact.qualification_id != qid
            or artifact.source_snapshot_id != lock["training_snapshot_id"]
            or artifact.checkpoint_sha256 != lock["directions"][side]["estimator_hash"]
            or qual["directions"][side]["qualification"] != "validation_gate_passed"
            or dict(artifact.runtime_versions)
            != {p: plan["provenance"]["versions"][p] for p, _ in artifact.runtime_versions}
        ):
            raise ValueError("hgb_artifact_provenance_mismatch")
        artifacts[side] = json.loads(canonical(asdict(artifact)))
        s = dict(c[side])
        s["direction"] = SignalDirection(s["direction"])
        s["strength"] = SignalStrength(s["strength"])
        directions.append(DirectionalSignal(**s))
        if (
            artifact.direction != side
            or artifact.fingerprint != c[side]["model_fingerprint"]
            or artifact.feature_version != evidence["feature_version"]
        ):
            raise ValueError("hgb_model_identity_mismatch")
    candidate = SignalCandidate(
        ctx,
        directions[0],
        directions[1],
        c["policy_fingerprint"],
        c["quality_policy_fingerprint"],
        tuple(c["rejected_quality_flags"]),
    )
    if (
        canonical(candidate.to_dict()) != canonical(c)
        or candidate.fingerprint != evidence["signal_fingerprint"]
    ):
        raise ValueError("hgb_candidate_mismatch")
    bars = snapshot_bars(snapshot, ctx.ticker)
    values = feature_vector(bars, snapshot)
    anchor = date.fromisoformat(evidence["anchor_date"])
    expected_cutoff = datetime.combine(anchor + timedelta(days=1), time(), SEOUL)
    if (
        ctx.snapshot_id != digest(snapshot)
        or ctx.dataset_id
        != digest({"snapshot_id": ctx.snapshot_id, "feature_version": evidence["feature_version"]})
        or ctx.input_hash != digest([b.to_dict() for b in bars])
        or ctx.feature_hash != digest([list(FEATURES), list(values)])
        or ctx.data_cutoff != expected_cutoff
        or bars[-1].trading_date != anchor
        or len(bars) > 12
        or ctx.population
        != ("prior_decline" if values[2] < 0 else "prior_rise" if values[2] > 0 else "flat")
        or ctx.quality_flags != tuple(snapshot["quality_flags"])
        or ctx.data_origin != snapshot["data_origin"]
        or ctx.mode != snapshot["mode"]
        or snapshot["usage_restriction"] != c["usage_restriction"]
        or any(b.collected_at > datetime.fromisoformat(snapshot["cutoff"]) for b in bars)
    ):
        raise ValueError("hgb_input_evidence_mismatch")
    quality = validate_quality(snapshot["quality"])
    if quality is not None and datetime.fromisoformat(
        quality["verified_at"]
    ) > datetime.fromisoformat(snapshot["cutoff"]):
        raise ValueError("hgb_quality_after_source_cutoff")
    # Public inference permits explicit quality-denied candidates. Trading additionally
    # requires a reviewed manifest; absent review cannot be allowed by policy flags.
    quality_ok = (
        snapshot["quality"] is not None and snapshot["session_basis"] == "verified_sessions"
    )
    if quality_ok and lock["session_basis"] != snapshot["session_basis"]:
        raise ValueError("hgb_session_basis_mismatch")
    pair = {side: c[side]["model_fingerprint"] for side in ("up", "down")}
    return {
        "research_contract": VERSION,
        "model_family": "hist_gradient_boosting",
        "model_version": digest(pair),
        "model_created_at": None,
        "ticker": ctx.ticker,
        "mode": ctx.mode,
        "data_origin": ctx.data_origin,
        "usage_restriction": c["usage_restriction"],
        "anchor_policy": "complete_daily_bar_next_seoul_midnight",
        **event_contract(snapshot["session_basis"]),
        "anchor_at": ctx.as_of.isoformat(),
        "snapshot_as_of": snapshot["cutoff"],
        "data_as_of": None,
        "snapshot_id": ctx.snapshot_id,
        "last_trading_date": anchor.isoformat(),
        "latest_collected_at": max(b.collected_at for b in bars).isoformat(),
        "input_hash": ctx.input_hash,
        "feature_hash": ctx.feature_hash,
        "feature_values": dict(zip(FEATURES, values, strict=True)),
        "input_status": "quality_unverified"
        if not quality_ok
        else "quality_blocked"
        if c["rejected_quality_flags"]
        else "ok",
        "context": ctx.population,
        **{
            f"{side}_{key}": c[side][source]
            for side in ("up", "down")
            for key, source in (("score", "raw_score"), ("status", "status"))
        },
        "quality_flags": list(ctx.quality_flags),
        "signal_state": "research_only",
        "operational_status": "unregistered",
        "operational_eligible": False,
        "executable": False,
        "reasons": {},
        "hgb_provenance": {
            "contract": VERSION,
            "public_evidence_hash": digest(evidence),
            "model_pair": pair,
            "models": artifacts,
            "quality_policy_fingerprint": c["quality_policy_fingerprint"],
            "signal_policy_fingerprint": c["policy_fingerprint"],
            "rejected_quality_flags": list(c["rejected_quality_flags"]),
            "calendar": snapshot.get("research_calendar"),
            "feature_hash": ctx.feature_hash,
            "input_dataset_id": ctx.dataset_id,
            "quality_manifest_hash": digest(snapshot["quality"]),
            "source_cutoff": snapshot["cutoff"],
            "information_available_at": ctx.data_cutoff.isoformat(),
            "contributions_unavailable_reason": "hgb_attribution_not_provided",
        },
    }


def adapt(result: IntegratedResearchSignal) -> dict[str, Any]:
    """Only the genuine public service envelope is supported."""
    if type(result) is not IntegratedResearchSignal:
        raise ValueError("public_hgb_result_required")
    return projection(result.to_dict())
