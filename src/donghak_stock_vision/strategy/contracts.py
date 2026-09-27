"""Immutable value objects: canonical JSON prevents nested mutable aliases."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from donghak_stock_vision.data.learning import canonical, digest, timestamp
from donghak_stock_vision.data.schema import validate_ticker


@dataclass(frozen=True)
class Document:
    payload_json: str
    kind: ClassVar[str] = "document"

    def __post_init__(self) -> None:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("document must be an object")
        object.__setattr__(self, "payload_json", canonical(value))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(canonical(value))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.payload_json)
        return value

    @property
    def identifier(self) -> str:
        return digest({"kind": self.kind, "value": self.to_dict()})


@dataclass(frozen=True)
class DecisionInput(Document):
    kind: ClassVar[str] = "decision_input"

    def __post_init__(self) -> None:
        super().__post_init__()
        request = self.to_dict()["request"]
        validate_ticker(request["ticker"])
        timestamp(request["decision_as_of"])
        if request["scope"] not in {"research", "operational"}:
            raise ValueError("invalid decision scope")


@dataclass(frozen=True)
class DecisionPolicy(Document):
    kind: ClassVar[str] = "decision_policy"


@dataclass(frozen=True)
class DecisionResult(Document):
    kind: ClassVar[str] = "decision_result"


@dataclass(frozen=True)
class RiskExitPolicy:
    stop_loss: str
    take_profit: str
    max_holding_period: str

    def diagnostics(self) -> dict[str, Any]:
        return {
            name: {"mode": mode, "reason": "policy_disabled"}
            for name, mode in (
                ("stop_loss", self.stop_loss),
                ("take_profit", self.take_profit),
                ("max_holding_period", self.max_holding_period),
            )
        }
