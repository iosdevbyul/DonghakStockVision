"""Immutable wire contracts and deterministic identities."""

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from typing import Any

import pytest

from donghak_stock_vision.backtest.contracts import (
    Checkpoint,
    Contract,
    LedgerEvent,
    MarketEvent,
    RunManifest,
)
from tests.backtest.helpers import CLASSES, H, payload


@pytest.mark.parametrize("cls", CLASSES)
def test_immutable_detached_round_trip(cls: type[Contract]) -> None:
    source = payload(cls)
    record = cls.from_dict(source)
    before = record.to_json()
    source["scope"] = "operational"
    detached = record.to_dict()
    detached["scope"] = "operational"
    assert record.to_json() == before
    assert cls.from_json(before) == record
    assert cls.from_dict(record.to_dict()) == record
    with pytest.raises(FrozenInstanceError):
        record.payload_json = "{}"  # type: ignore[misc]


def reverse_objects(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: reverse_objects(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_objects(v) for v in value]
    return value


@pytest.mark.parametrize("cls", CLASSES)
def test_hash_independent_of_object_key_order(cls: type[Contract]) -> None:
    record = cls.from_dict(payload(cls))
    reordered = cls.from_json(json.dumps(reverse_objects(record.to_dict())))
    assert reordered.to_json() == record.to_json()
    assert reordered.identifier == record.identifier
    assert reordered.content_hash == record.content_hash


@pytest.mark.parametrize("value", ["100.000", "1E+2", Decimal("100.00")])
def test_decimal_canonicalization_without_context_rounding(value: str | Decimal) -> None:
    source = payload(MarketEvent)
    source["public_fields"]["open"] = value
    with localcontext() as context:
        context.prec = 1
        result = MarketEvent.from_dict(source)
    assert result == MarketEvent.from_dict(payload(MarketEvent))
    assert result.to_dict()["public_fields"]["open"] == "100"


@pytest.mark.parametrize(
    "stamp", ["2026-01-05T09:00:00+09:00", "2026-01-05T00:00:00Z", "2026-01-04T19:00:00-05:00"]
)
def test_equivalent_offsets_share_identity(stamp: str) -> None:
    source = payload(MarketEvent)
    for key in ("event_at", "source_received_at", "available_at", "quality_available_at"):
        source[key] = stamp
    assert MarketEvent.from_dict(source) == MarketEvent.from_dict(payload(MarketEvent))


@pytest.mark.parametrize("cls", [LedgerEvent, Checkpoint])
def test_audit_time_is_preserved_but_not_economic_identity(cls: type[Contract]) -> None:
    a = cls.from_dict(payload(cls))
    source = a.to_dict()
    source["recorded_at"] = "2026-02-01T00:00:00Z"
    b = cls.from_dict(source)
    assert a.identifier == b.identifier
    assert a.content_hash != b.content_hash
    assert cls.from_json(b.to_json()) == b
    source["run_id"] = "b" * 64
    if cls is Checkpoint:
        source["manifest_hash"] = source["run_id"]
    assert cls.from_dict(source).identifier != b.identifier


def test_nested_collections_are_detached_and_sets_are_canonical() -> None:
    source = payload(RunManifest)
    source["models"].append({**source["models"][0], "artifact_id": "another"})
    a = RunManifest.from_dict(source)
    source["models"].reverse()
    assert RunManifest.from_dict(source) == a
    view = a.to_dict()
    view["models"][0]["content_hash"] = "b" * 64
    assert a.to_dict()["models"][0]["content_hash"] == H
    source["end_at"] = "2026-01-07T00:00:00Z"
    assert RunManifest.from_dict(source).identifier != a.identifier


@pytest.mark.parametrize(
    "wire",
    [
        '{"scope":"research","scope":"operational"}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
    ],
)
def test_ambiguous_json_is_rejected(wire: str) -> None:
    with pytest.raises(ValueError):
        MarketEvent.from_json(wire)
