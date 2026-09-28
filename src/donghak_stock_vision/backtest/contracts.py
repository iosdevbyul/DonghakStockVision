"""Immutable Phase 4 records. Descriptions of simulations, never executable actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from donghak_stock_vision.backtest.validation import ContractError, normalize


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ContractError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(value: str) -> Any:
    raise ContractError("nonfinite_json")


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True)
class Contract:
    payload_json: str
    kind: ClassVar[str] = "abstract"

    def __post_init__(self) -> None:
        data = json.loads(self.payload_json, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        object.__setattr__(self, "payload_json", _canonical(normalize(self.kind, data)))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(_canonical(normalize(cls.kind, value)))

    @classmethod
    def from_json(cls, value: str) -> Self:
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.payload_json)
        return result

    def to_json(self) -> str:
        return self.payload_json

    def _hash(self, *, audit: bool) -> str:
        payload = self.to_dict()
        if not audit:
            payload.pop("recorded_at", None)
        envelope = {"kind": self.kind, "hash_version": 1, "payload": payload}
        return hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()

    @property
    def identifier(self) -> str:
        """Semantic ID excludes caller-supplied wall-clock audit recorded_at."""
        return self._hash(audit=False)

    @property
    def content_hash(self) -> str:
        """Integrity hash includes the complete canonical payload, including audit."""
        return self._hash(audit=True)


@dataclass(frozen=True)
class ExecutionPolicy(Contract):
    kind: ClassVar[str] = "execution_policy"


@dataclass(frozen=True)
class RunManifest(Contract):
    kind: ClassVar[str] = "run_manifest"

    @property
    def run_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class MarketEvent(Contract):
    kind: ClassVar[str] = "market_event"

    @property
    def event_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class SimulationAccount(Contract):
    kind: ClassVar[str] = "simulation_account"


@dataclass(frozen=True)
class VirtualOrder(Contract):
    kind: ClassVar[str] = "virtual_order"

    @property
    def order_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class VirtualFill(Contract):
    kind: ClassVar[str] = "virtual_fill"

    @property
    def fill_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class LedgerEvent(Contract):
    kind: ClassVar[str] = "ledger_event"

    @property
    def event_id(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class Checkpoint(Contract):
    kind: ClassVar[str] = "checkpoint"
