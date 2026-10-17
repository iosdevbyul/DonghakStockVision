"""Run-bound immutable analysis DAG; references are integrity, never PIT approval."""

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from donghak_stock_vision.backtest.contracts import RunManifest
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import canonical, digest

if TYPE_CHECKING:
    from donghak_stock_vision.backtest.availability import FrozenAnalysis

VERSION = "shared_historical_evidence_v1"
# Only these existing evidence components may be replaced by typed references.
SCHEMA = {
    "analysis": {
        "model": "linear_model",
        "snapshot": "snapshot",
        "training_snapshot": "snapshot",
        "dataset": "dataset",
        "signal": "signal",
        "hgb_evidence": "hgb_public",
    },
    "snapshot": {"quality": "quality"},
    "signal": {"hgb_provenance": "hgb_provenance"},
    "hgb_provenance": {"models": "hgb_models"},
    "hgb_public": {
        "snapshot": "snapshot",
        "models": "hgb_models",
        "experiment_provenance": "hgb_experiment",
    },
    "hgb_models": {"up": "hgb_model", "down": "hgb_model"},
    "hgb_experiment": {
        "lock": "hgb_lock",
        "qualification": "hgb_qualification",
        "plan": "hgb_plan",
    },
}
KINDS = set(SCHEMA) | {v for schema in SCHEMA.values() for v in schema.values()}


def source_identity(kind: str, value: dict[str, Any], canonical_id: str) -> str:
    key = {"hgb_lock": "lock_id", "hgb_qualification": "report_id"}.get(kind)
    if key is None:
        return canonical_id
    identity = sha(value.get(key))
    require(
        digest({k: v for k, v in value.items() if k != key}) == identity,
        "shared_source_identity_mismatch",
    )
    return str(identity)


class EvidenceBuilder:
    """Incremental writer: retain shared canonical components, not expanded bundles."""

    def __init__(self) -> None:
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.records: list[dict[str, str]] = []

    def _add(self, kind: str, value: dict[str, Any], analysis_id: str) -> dict[str, str]:
        identity = digest(value)
        if identity in self.artifacts:
            item = self.artifacts[identity]
            require(item["kind"] == kind, "shared_kind_conflict")
            item["analysis_ids"].add(analysis_id)
            # Propagate consumers even when a whole parent is already interned.
            for key, child_kind in SCHEMA.get(kind, {}).items():
                if isinstance(value.get(key), dict):
                    self._add(child_kind, value[key], analysis_id)
            return {"artifact_id": identity, "kind": kind}
        payload = dict(value)
        for key, child_kind in SCHEMA.get(kind, {}).items():
            if isinstance(value.get(key), dict):
                payload[key] = {"$evidence": self._add(child_kind, value[key], analysis_id)}
        self.artifacts[identity] = {
            "artifact_id": identity,
            "source_id": source_identity(kind, value, identity),
            "kind": kind,
            "payload": payload,
            "analysis_ids": {analysis_id},
        }
        return {"artifact_id": identity, "kind": kind}

    def add(self, bundle: dict[str, Any]) -> None:
        aid = bundle["analysis_id"]
        sha(aid)
        ref = self._add("analysis", bundle, aid)
        self.records.append({"analysis_id": aid, **ref})

    def table(self, run_id: str) -> dict[str, Any]:
        sha(run_id)
        return {
            "version": VERSION,
            "run_id": run_id,
            "artifacts": [
                {**item, "analysis_ids": sorted(item["analysis_ids"])}
                for _, item in sorted(self.artifacts.items())
            ],
        }


@dataclass(frozen=True, eq=False, init=False)
class SharedEvidence:
    """Fully verified immutable graph, cached only for its owning frozen document.

    Expanded private nodes share children. Public resolves return detached copies.
    No source DB, pickle loader, policy default or availability inference is used.
    """

    run_id: str
    references: tuple[str, ...]
    _index: Mapping[str, tuple[str, FrozenJSON]]
    _sources: Mapping[str, str]
    _resolved: dict[str, dict[str, Any]]
    _visiting: set[str]
    _edges: dict[str, set[str]]
    _analyses: tuple["FrozenAnalysis", ...] | None

    def __init__(self, table: dict[str, Any], records: list[dict[str, Any]]) -> None:
        fields(table, "version run_id artifacts")
        require(table["version"] == VERSION, "unsupported_shared_evidence_version")
        sha(table["run_id"])
        object.__setattr__(self, "run_id", table["run_id"])
        index = {}
        consumers = {}
        sources = {}
        for raw in table["artifacts"]:
            item = fields(raw, "artifact_id source_id kind payload analysis_ids")
            identity = item["artifact_id"]
            sha(identity)
            sha(item["source_id"])
            sources[identity] = item["source_id"]
            require(identity not in index, "duplicate_shared_artifact")
            require(item["kind"] in KINDS, "wrong_shared_artifact_kind")
            require(
                item["analysis_ids"] == sorted(set(item["analysis_ids"])),
                "shared_consumers_invalid",
            )
            index[identity] = (item["kind"], FrozenJSON.freeze(item["payload"]))
            consumers[identity] = set(item["analysis_ids"])
        object.__setattr__(self, "_index", MappingProxyType(index))
        object.__setattr__(self, "_sources", MappingProxyType(sources))
        object.__setattr__(self, "_resolved", {})
        object.__setattr__(self, "_visiting", set())
        object.__setattr__(self, "_edges", {})
        actual: dict[str, set[str]] = {identity: set() for identity in index}
        refs = []
        for raw in records:
            record = fields(raw, "analysis_id artifact_id kind")
            value = self._resolve(record["artifact_id"], record["kind"])
            require(record["kind"] == "analysis", "wrong_shared_artifact_kind")
            require(
                value["analysis_id"] == record["analysis_id"], "shared_analysis_identity_mismatch"
            )
            refs.append(record["artifact_id"])
            pending = [record["artifact_id"]]
            reached = set()
            while pending:
                identity = pending.pop()
                if identity in reached:
                    continue
                reached.add(identity)
                actual[identity].add(record["analysis_id"])
                pending.extend(self._edges[identity])
        require(actual == consumers and all(actual.values()), "shared_consumers_mismatch")
        object.__setattr__(self, "references", tuple(refs))
        object.__setattr__(self, "_analyses", None)

    def _resolve(self, identity: str, kind: str) -> dict[str, Any]:
        require(identity in self._index, "missing_shared_artifact")
        actual_kind, payload = self._index[identity]
        require(actual_kind == kind, "wrong_shared_artifact_kind")
        if identity in self._resolved:
            return self._resolved[identity]
        require(identity not in self._visiting, "cyclic_shared_evidence")
        self._visiting.add(identity)
        value = payload.to_dict()
        edges = set()
        for key, child_kind in SCHEMA.get(kind, {}).items():
            if value.get(key) is None:
                continue
            wrapper = fields(value[key], "$evidence")
            ref = fields(wrapper["$evidence"], "artifact_id kind")
            require(ref["kind"] == child_kind, "wrong_shared_artifact_kind")
            value[key] = self._resolve(ref["artifact_id"], child_kind)
            edges.add(ref["artifact_id"])
        require(digest(value) == identity, "shared_artifact_checksum_mismatch")
        require(
            source_identity(kind, value, identity) == self._sources[identity],
            "shared_source_identity_mismatch",
        )
        self._visiting.remove(identity)
        self._edges[identity] = edges
        self._resolved[identity] = value
        return value

    def resolve(self, identity: str) -> dict[str, Any]:
        return copy.deepcopy(self._resolve(identity, "analysis"))

    def analyses(
        self, manifest: RunManifest, release_plan: FrozenJSON
    ) -> tuple["FrozenAnalysis", ...]:
        from donghak_stock_vision.backtest.availability import FrozenAnalysis

        require(self.run_id == manifest.identifier, "shared_run_mismatch")
        if self._analyses is None:
            object.__setattr__(
                self,
                "_analyses",
                tuple(
                    FrozenAnalysis(manifest, release_plan, SharedAnalysis(self, ref))
                    for ref in self.references
                ),
            )
        assert self._analyses is not None
        require(
            all(a.manifest == manifest and a.release_plan == release_plan for a in self._analyses),
            "shared_run_mismatch",
        )
        return self._analyses


@dataclass(frozen=True, eq=False)
class SharedAnalysis:
    """Lazy legacy value view; never stores an expanded canonical bundle per date."""

    evidence: SharedEvidence
    identifier: str

    def to_dict(self) -> dict[str, Any]:
        return self.evidence.resolve(self.identifier)

    @property
    def payload_json(self) -> str:
        # Explicit legacy serialization is still available, but not retained.
        return canonical(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (SharedAnalysis, FrozenJSON)):
            return self.identifier == other.identifier
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.payload_json,))


def is_shared(value: dict[str, Any]) -> bool:
    return value.get("version") == 2 and "evidence_table" in value


def pack(document: FrozenJSON) -> FrozenJSON:
    """Validated migration writer; legacy export is expand(). No source is rewritten."""
    d = document.to_dict()
    require(d.get("version") == 1, "legacy_input_required")
    builder = EvidenceBuilder()
    for bundle in d["analyses"]:
        builder.add(bundle)
    from donghak_stock_vision.backtest.data import FrozenTape

    tape = FrozenTape.from_json(FrozenJSON.freeze(d["tape"]).payload_json)
    d.update(
        version=2, analyses=builder.records, evidence_table=builder.table(tape.manifest.identifier)
    )
    if d.get("kind") == "historical_input_v1":
        d["kind"] = "historical_input_v2"
    result = FrozenJSON.freeze(d)
    result.shared_evidence.analyses(tape.manifest, tape.release_plan)
    return result


def _legacy_metadata(d: dict[str, Any]) -> dict[str, Any]:
    if is_shared(d):
        d.pop("evidence_table")
        d["version"] = 1
        if d.get("kind") == "historical_input_v2":
            d["kind"] = "historical_input_v1"
    return d


def legacy_dict(document: FrozenJSON) -> dict[str, Any]:
    """Explicit, validated legacy export, including compatibility trade receipts."""
    from donghak_stock_vision.backtest.data import FrozenTape

    d = document.to_dict()
    shared = is_shared(d)
    d = _legacy_metadata(d)
    if shared:
        tape = FrozenTape.from_json(FrozenJSON.freeze(d["tape"]).payload_json)
        document.shared_evidence.analyses(tape.manifest, tape.release_plan)
        d["analyses"] = [
            document.shared_evidence.resolve(ref) for ref in document.shared_evidence.references
        ]
    return d


def expand(document: FrozenJSON) -> FrozenJSON:
    return FrozenJSON.freeze(legacy_dict(document))


def legacy_identifier(document: FrozenJSON, *, normalize_audit: bool = False) -> str:
    """Stream the exact legacy canonical JSON; do not expand all decision bundles."""
    d = document.to_dict()
    shared = is_shared(d)
    d = _legacy_metadata(d)
    if not shared:
        if normalize_audit:
            for b in d["analyses"]:
                b["created_at"].pop("signal", None)
        return digest(d)
    evidence = document.shared_evidence
    h = hashlib.sha256()

    def write(value: str) -> None:
        h.update(value.encode())

    write("{")
    for n, key in enumerate(sorted(d)):
        if n:
            write(",")
        write(canonical(key) + ":")
        if key != "analyses":
            write(canonical(d[key]))
            continue
        write("[")
        for i, ref in enumerate(evidence.references):
            if i:
                write(",")
            b = evidence.resolve(ref)
            if normalize_audit:
                b["created_at"].pop("signal", None)
            write(canonical(b))
        write("]")
    write("}")
    return h.hexdigest()
