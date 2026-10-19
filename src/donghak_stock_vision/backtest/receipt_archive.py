"""Opt-in exact JSON receipt transport, separate from economic validation/identity."""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha
from donghak_stock_vision.data.learning import canonical, digest

VERSION = "shared_trading_receipts_v1"
MIN_SHARED_BYTES = 1024


@dataclass(frozen=True)
class ArchiveLimits:
    transport_bytes: int = 64 * 1024 * 1024
    expanded_bytes: int = 256 * 1024 * 1024
    artifacts: int = 25000
    references: int = 250000
    depth: int = 64
    nodes: int = 5000000
    digest_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        require(
            all(type(v) is int and v > 0 for v in vars(self).values()), "invalid_archive_limits"
        )


DEFAULT_LIMITS = ArchiveLimits()


def kind(value: Any) -> str:
    """Classify by immutable keys/scalar tags, never by a caller's claimed kind."""
    if isinstance(value, list):
        return "array"
    require(isinstance(value, dict), "archive_container_required")
    keys = set(value)
    shapes = (
        ({"run", "performance", "detailed_report", "run_hash"}, "result"),
        ({"processed_event_count", "final_checkpoint", "events"}, "run"),
        ({"sequence", "cutoff", "checkpoint_hash", "status"}, "event"),
        ({"event_id", "order_id", "effective_at", "known_at", "data"}, "ledger_event"),
        ({"accepted_order", "reservation_checkpoint", "acceptance_clock"}, "acceptance"),
        ({"fill", "fill_hash", "ledger_command"}, "execution"),
        ({"release_status", "acceptance_hash", "ledger_command"}, "release"),
        ({"side", "quantity", "idempotency_key", "account_revision"}, "order"),
        ({"fill_known_at", "order_id", "price_krw", "fee_krw"}, "fill"),
        ({"manifest", "state", "state_hash", "events"}, "checkpoint"),
        ({"episodes", "evidence", "ledger_hash", "complete"}, "position_history"),
        ({"analysis_bundle", "decision_input", "acceptance", "execution"}, "trade_evidence"),
        ({"analysis_id", "model", "snapshot", "signal"}, "analysis"),
        ({"rows", "cutoff", "quality"}, "snapshot"),
        ({"samples", "snapshot_id", "feature_version"}, "dataset"),
        ({"initial_account", "execution_mode", "market_snapshots"}, "manifest"),
        ({"equity_curve", "fill_count", "final_equity"}, "performance"),
    )
    for required, name in shapes:
        if required <= keys:
            return name
    if (
        value.get("version") in ("historical_trading_report_v1", "five_ticker_historical_report_v1")
        or value.get("schema_version") == "historical_trading_report_v1"
    ):
        return "report"
    if value.get("kind") in ("historical_input_v1", "historical_input_v2"):
        return "historical_input"
    if "parameters" in keys and "dataset_id" in keys:
        return "model"
    return "object"


def address(run_id: str, artifact_kind: str, source_hash: str) -> str:
    return digest(
        {"version": VERSION, "run_id": run_id, "kind": artifact_kind, "source_hash": source_hash}
    )


def reference(run_id: str, artifact_kind: str, source_hash: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "kind": artifact_kind,
        "source_hash": source_hash,
        "artifact_id": address(run_id, artifact_kind, source_hash),
    }


def source_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Convenience index only; it is checked against the reconstructed result."""
    r = value["run"]
    return {
        "run_id": r["run_id"],
        "result_version": value["version"],
        "run_hash": value["run_hash"],
        "performance_hash": value["performance_hash"],
        "detailed_report_hash": value["detailed_report_hash"],
        "event_count": r["processed_event_count"],
        "information_mode": r["information_mode"],
        "operational_eligible": r["operational_eligible"],
        "executable": r["executable"],
    }


def prune_single_use(artifacts: dict[str, dict[str, Any]]) -> None:
    """Inline unique storage-only nodes; retain individual auditable trading identities."""
    keep = {
        "result",
        "run",
        "event",
        "ledger_event",
        "acceptance",
        "order",
        "execution",
        "fill",
        "release",
        "checkpoint",
        "position_history",
        "trade_evidence",
        "performance",
        "report",
    }
    incoming: dict[str, int] = dict.fromkeys(artifacts, 0)
    for item in artifacts.values():
        for edge in item["references"]:
            incoming[edge["artifact_id"]] += 1
    inline = {
        identity
        for identity, item in artifacts.items()
        if incoming[identity] == 1 and item["kind"] not in keep
    }
    # Builder insertion is dependency-first. Rewrites preserve original source hashes.
    for item in artifacts.values():
        references = []
        for edge in item["references"]:
            if edge["artifact_id"] not in inline:
                references.append(edge)
                continue
            child = artifacts[edge["artifact_id"]]
            parent = item["payload"]
            for key in edge["path"][:-1]:
                parent = parent[key]
            parent[edge["path"][-1]] = child["payload"]
            references.extend(
                {**r, "path": [*edge["path"], *r["path"]]} for r in child["references"]
            )
        item["references"] = sorted(references, key=lambda r: canonical(r["path"]))
    for identity in inline:
        del artifacts[identity]


def encode_result(
    result: FrozenJSON, *, limits: ArchiveLimits = DEFAULT_LIMITS
) -> "ReceiptArchive":
    """Encode without inferring validity; expand_validated performs original replay."""
    try:
        return _encode_result(result, limits=limits)
    except (KeyError, TypeError, IndexError, AttributeError, RecursionError) as error:
        raise ValueError("archive_invalid_result") from error


def _encode_result(
    result: FrozenJSON, *, limits: ArchiveLimits = DEFAULT_LIMITS
) -> "ReceiptArchive":
    """Encode an already executed result; integrity alone is not economic approval.

    Use expand_validated() to run the original replay validators. The input result
    already exists in memory; this migration writer is not a streaming Runner.
    """
    require(len(result.payload_json.encode()) <= limits.expanded_bytes, "archive_expansion_limit")
    original = result.to_dict()
    require(kind(original) == "result", "archive_result_required")
    require(
        original.get("version")
        in ("five_ticker_historical_result_v1", "reported_backtest_archive_source_v1")
        or (
            type(original.get("version")) is int
            and original["version"] == 1
            and original.get("mode") == "historical_research"
        ),
        "unsupported_archive_result",
    )
    run_id = sha(original["run"]["run_id"])
    artifacts: dict[str, dict[str, Any]] = {}
    visited = 0
    references = 0
    work = 0

    def add(
        value: Any, *, force: bool = False, depth: int = 0
    ) -> tuple[Any, dict[str, str] | None]:
        nonlocal visited, references, work
        visited += 1
        require(visited <= limits.nodes and depth <= limits.depth, "archive_structure_limit")
        if not isinstance(value, (dict, list)):
            return value, None
        encoded = canonical(value).encode()
        size = len(encoded)
        # Repeated input containers still require hashing before deduplication.
        work += size
        require(work <= limits.digest_bytes, "archive_digest_work_limit")
        k = kind(value)
        shared = force or k not in {"object", "array"} or size >= MIN_SHARED_BYTES
        ref = reference(run_id, k, hashlib.sha256(encoded).hexdigest()) if shared else None
        if ref is not None and ref["artifact_id"] in artifacts:
            return None, ref
        payload: Any = {} if isinstance(value, dict) else []
        edges: list[dict[str, Any]] = []

        def descend(item: Any, path: list[str | int], level: int) -> Any:
            if isinstance(item, (dict, list)):
                body, child_ref = add(item, depth=level)
                if child_ref is not None:
                    edges.append({"path": path, **child_ref})
                    return None
                # Inline child's edges are flattened by add() below.
                return body
            return item

        # Inline containers cannot hide references: carry their edges to this owner.
        def inline(item: Any, path: list[str | int], level: int) -> Any:
            nonlocal visited
            visited += 1
            require(visited <= limits.nodes and level <= limits.depth, "archive_structure_limit")
            if not isinstance(item, (dict, list)):
                return item
            if len(canonical(item).encode()) >= MIN_SHARED_BYTES or kind(item) not in {
                "object",
                "array",
            }:
                return descend(item, path, level)
            if isinstance(item, dict):
                return {key: inline(v, [*path, key], level + 1) for key, v in item.items()}
            return [inline(v, [*path, i], level + 1) for i, v in enumerate(item)]

        if isinstance(value, dict):
            payload = {key: inline(v, [key], depth + 1) for key, v in value.items()}
        else:
            payload = [inline(v, [i], depth + 1) for i, v in enumerate(value)]
        if ref is None:
            require(not edges, "archive_inline_reference_invalid")
            return payload, None
        references += len(edges)
        require(references <= limits.references, "archive_reference_limit")
        require(len(artifacts) < limits.artifacts, "archive_artifact_limit")
        artifacts[ref["artifact_id"]] = {
            **ref,
            "expanded_bytes": size,
            "payload": payload,
            "references": sorted(edges, key=lambda row: canonical(row["path"])),
        }
        return None, ref

    _, root = add(original, force=True)
    assert root is not None
    prune_single_use(artifacts)
    # Direct root component references enable future API retrieval without expansion.
    root_record = artifacts[root["artifact_id"]]
    views = {
        row["path"][0]: {k: row[k] for k in ("run_id", "kind", "source_hash", "artifact_id")}
        for row in root_record["references"]
        if row["path"] in [["run"], ["performance"], ["detailed_report"]]
    }
    archive = ReceiptArchive(
        FrozenJSON.freeze(
            {
                "version": VERSION,
                "run_id": run_id,
                "legacy_hash": root["source_hash"],
                "root": root,
                "metadata": source_metadata(original),
                "views": views,
                "artifacts": [artifacts[key] for key in sorted(artifacts)],
            }
        ),
        limits,
    )
    archive.verify_transport()
    return archive


@dataclass(frozen=True)
class ReceiptArchive:
    document: FrozenJSON
    limits: ArchiveLimits = DEFAULT_LIMITS

    def __post_init__(self) -> None:
        require(
            len(self.document.payload_json.encode()) <= self.limits.transport_bytes,
            "archive_transport_limit",
        )

    @classmethod
    def from_json(cls, value: str, *, limits: ArchiveLimits = DEFAULT_LIMITS) -> "ReceiptArchive":
        require(len(value.encode()) <= limits.transport_bytes, "archive_transport_limit")
        try:
            return cls(FrozenJSON(value), limits)
        except (RecursionError, TypeError) as error:
            raise ValueError("archive_invalid_json") from error

    @property
    def transport_hash(self) -> str:
        return self.document.identifier

    @cached_property
    def _graph(self) -> "VerifiedGraph":
        return VerifiedGraph(self.document, self.limits)

    def verify_transport(self) -> None:
        """Verify every byte/reference. Does not assert an executed/qualified run."""
        try:
            self._graph.verify()
        except (KeyError, TypeError, IndexError, AttributeError, RecursionError) as error:
            raise ValueError("archive_malformed_document") from error

    def iter_legacy_json(self) -> Iterator[str]:
        """Verified canonical byte reconstruction; no economic approval or full copy."""
        self.verify_transport()
        yield from self._graph.chunks(self._graph.root)

    def component(self, name: str) -> FrozenJSON:
        """Transport-verified projection; call expand_validated for economic validation."""
        self.verify_transport()
        require(name in self._graph.views, "archive_view_missing")
        return FrozenJSON("".join(self._graph.chunks(self._graph.views[name]["artifact_id"])))

    def expand_validated(self) -> FrozenJSON:
        """Reconstruct exactly, then delegate to the original complete replay validators."""
        from donghak_stock_vision.backtest.receipt_validation import validate_result

        result = FrozenJSON("".join(self.iter_legacy_json()))
        require(result.identifier == self._graph.legacy_hash, "archive_legacy_hash_mismatch")
        require(
            source_metadata(result.to_dict()) == self._graph.metadata, "archive_metadata_mismatch"
        )
        validate_result(result)
        return result


class VerifiedGraph:
    """Owner-private verified graph. No expanded result or process-global cache."""

    def __init__(self, document: FrozenJSON, limits: ArchiveLimits) -> None:
        self.limits = limits
        d = fields(document.to_dict(), "version run_id legacy_hash root metadata views artifacts")
        require(d["version"] == VERSION, "unsupported_receipt_archive")
        self.run_id = sha(d["run_id"])
        self.legacy_hash = sha(d["legacy_hash"])
        self.metadata, self.views = d["metadata"], d["views"]
        self.index: dict[str, dict[str, Any]] = {}
        self.tries: dict[str, dict[Any, Any]] = {}
        self.sizes: dict[str, int] = {}
        self.heights: dict[str, int] = {}
        self.payload_heights: dict[str, int] = {}
        self.visiting: set[str] = set()
        self.work = 0
        self.nodes = 0
        refs = 0
        require(
            isinstance(d["artifacts"], list) and len(d["artifacts"]) <= limits.artifacts,
            "archive_artifact_limit",
        )
        for raw in d["artifacts"]:
            item = fields(
                raw, "run_id kind source_hash artifact_id expanded_bytes payload references"
            )
            self.check_reference(
                {key: item[key] for key in ("run_id", "kind", "source_hash", "artifact_id")}
            )
            identity = item["artifact_id"]
            require(identity not in self.index, "duplicate_archive_artifact")
            require(kind(item["payload"]) == item["kind"], "archive_artifact_kind_mismatch")
            require(isinstance(item["references"], list), "archive_references_required")
            refs += len(item["references"])
            require(refs <= limits.references, "archive_reference_limit")
            self.payload_heights[identity] = self.inspect_payload(item["payload"])
            self.index[identity] = item
        require(list(self.index) == sorted(self.index), "archive_artifacts_not_canonical")
        self.check_reference(d["root"])
        self.root = d["root"]["artifact_id"]
        require(d["root"]["kind"] == "result", "archive_result_required")
        self._verify_node(self.root, 0)
        require(set(self.sizes) == set(self.index), "unreferenced_archive_artifact")
        require(
            self.index[self.root]["source_hash"] == self.legacy_hash, "archive_legacy_hash_mismatch"
        )
        require(self.metadata.get("run_id") == self.run_id, "archive_metadata_mismatch")
        actual_views = {
            row["path"][0]: {k: row[k] for k in ("run_id", "kind", "source_hash", "artifact_id")}
            for row in self.index[self.root]["references"]
            if row["path"] in [["run"], ["performance"], ["detailed_report"]]
        }
        require(self.views == actual_views and len(self.views) == 3, "archive_view_mismatch")
        root_payload = self.index[self.root]["payload"]
        run_payload = self.index[self.views["run"]["artifact_id"]]["payload"]
        metadata = source_metadata(
            {
                "version": root_payload["version"],
                "run": run_payload,
                **{
                    k: root_payload[k]
                    for k in ("run_hash", "performance_hash", "detailed_report_hash")
                },
            }
        )
        require(self.metadata == metadata, "archive_metadata_mismatch")
        require(
            metadata["information_mode"] == "historical_research"
            and metadata["operational_eligible"] is False
            and metadata["executable"] is False,
            "archive_research_only",
        )

    def inspect_payload(self, value: Any, depth: int = 0) -> int:
        self.nodes += 1
        require(
            self.nodes <= self.limits.nodes and depth <= self.limits.depth,
            "archive_structure_limit",
        )
        children = (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        )
        return max((self.inspect_payload(v, depth + 1) for v in children), default=depth)

    def check_reference(self, ref: dict[str, Any]) -> None:
        fields(ref, "run_id kind source_hash artifact_id")
        require(ref["run_id"] == self.run_id, "archive_cross_run_reference")
        sha(ref["source_hash"])
        require(
            ref["artifact_id"] == address(self.run_id, ref["kind"], ref["source_hash"]),
            "archive_reference_digest_mismatch",
        )

    def _verify_node(self, identity: str, depth: int) -> int:
        require(depth <= self.limits.depth, "archive_structure_limit")
        require(identity in self.index, "archive_artifact_missing")
        if identity in self.sizes:
            require(depth + self.heights[identity] <= self.limits.depth, "archive_structure_limit")
            return self.sizes[identity]
        require(identity not in self.visiting, "archive_cycle")
        self.visiting.add(identity)
        item = self.index[identity]
        payload = item["payload"]
        size = len(canonical(payload).encode())
        height = self.payload_heights[identity]
        trie: dict[Any, Any] = {}
        paths = []
        for edge in item["references"]:
            fields(edge, "path run_id kind source_hash artifact_id")
            path = edge["path"]
            require(isinstance(path, list) and bool(path), "archive_reference_path_invalid")
            require(all(type(key) in {str, int} for key in path), "archive_reference_path_invalid")
            require(len(path) + depth <= self.limits.depth, "archive_structure_limit")
            ref = {k: edge[k] for k in ("run_id", "kind", "source_hash", "artifact_id")}
            self.check_reference(ref)
            require(ref["artifact_id"] in self.index, "archive_artifact_missing")
            target = self.index[ref["artifact_id"]]
            require(all(ref[k] == target[k] for k in ref), "archive_reference_binding_mismatch")
            current = payload
            branch = trie
            for key in path:
                require(None not in branch, "archive_overlapping_reference")
                if isinstance(current, dict):
                    require(type(key) is str and key in current, "archive_reference_path_invalid")
                else:
                    require(
                        isinstance(current, list) and type(key) is int and 0 <= key < len(current),
                        "archive_reference_path_invalid",
                    )
                current = current[key]
                branch = branch.setdefault(key, {})
            require(current is None and not branch, "archive_reference_placeholder_invalid")
            branch[None] = ref["artifact_id"]
            paths.append(canonical(path))
            size += self._verify_node(ref["artifact_id"], depth + len(path)) - 4
            height = max(height, len(path) + self.heights[ref["artifact_id"]])
        require(paths == sorted(set(paths)), "archive_reference_paths_not_canonical")
        require(
            type(item["expanded_bytes"]) is int and item["expanded_bytes"] == size,
            "archive_expanded_size_mismatch",
        )
        require(size <= self.limits.expanded_bytes, "archive_expansion_limit")
        self.work += size
        require(self.work <= self.limits.digest_bytes, "archive_digest_work_limit")
        require(depth + height <= self.limits.depth, "archive_structure_limit")
        self.heights[identity] = height
        self.tries[identity] = trie
        h = hashlib.sha256()
        for chunk in self.chunks(identity):
            h.update(chunk.encode())
        require(h.hexdigest() == item["source_hash"], "archive_payload_digest_mismatch")
        self.visiting.remove(identity)
        self.sizes[identity] = size
        return size

    def chunks(self, identity: str) -> Iterator[str]:
        def emit(value: Any, trie: dict[Any, Any]) -> Iterator[str]:
            if None in trie:
                yield from self.chunks(trie[None])
            elif not trie:
                yield canonical(value)
            elif isinstance(value, dict):
                yield "{"
                for i, key in enumerate(sorted(value)):
                    if i:
                        yield ","
                    yield canonical(key) + ":"
                    yield from emit(value[key], trie.get(key, {}))
                yield "}"
            else:
                yield "["
                for i, child in enumerate(value):
                    if i:
                        yield ","
                    yield from emit(child, trie.get(i, {}))
                yield "]"

        yield from emit(self.index[identity]["payload"], self.tries[identity])

    def verify(self) -> None:
        # Full checks ran during construction, scoped to this exact immutable owner.
        return None
