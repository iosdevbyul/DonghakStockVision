"""Exact opt-in archive transport; existing replay remains the economic authority."""

import copy
import hashlib
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest
from donghak_stock_vision.backtest.receipt_archive import (
    ArchiveLimits,
    ReceiptArchive,
    encode_result,
    reference,
)
from donghak_stock_vision.backtest.receipt_validation import reported_result
from donghak_stock_vision.backtest.service import freeze_request
from donghak_stock_vision.backtest.trading_report import report_backtest
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.data.schema import SEOUL
from tests.backtest.portfolio_helpers import TICKERS, execute_fixture, mechanics_run, prepared
from tests.backtest.test_hgb_trading import hgb_runner
from tests.backtest.test_historical_service import execute, setup_request
from tests.backtest.test_performance import valuation_policy
from tests.backtest.test_position_history import round_trip
from tests.backtest.test_public_inference import scored_runner


def source(runner: Any, *, shared: bool = True) -> FrozenJSON:
    return reported_result(
        freeze_request(runner, valuation_policy(), shared=shared),
        report_backtest(runner, valuation_policy()),
    )


@pytest.fixture(scope="module")
def small() -> tuple[FrozenJSON, ReceiptArchive]:
    original = source(round_trip(twice=True))
    return original, encode_result(original)


def test_exact_reconstruction_hashes_components_and_owner_isolation(
    small: tuple[FrozenJSON, ReceiptArchive],
) -> None:
    original, archive = small
    assert "".join(archive.iter_legacy_json()) == original.payload_json
    assert archive.expand_validated() == original
    assert archive.transport_hash != original.identifier
    assert archive.document.to_dict()["legacy_hash"] == original.identifier
    assert encode_result(FrozenJSON(original.payload_json)).document == archive.document
    assert ReceiptArchive.from_json(archive.document.payload_json).expand_validated() == original
    for name in ("run", "performance", "detailed_report"):
        assert archive.component(name) == FrozenJSON.freeze(original.to_dict()[name])
    archive.verify_transport()
    assert archive._graph is archive._graph
    fresh = ReceiptArchive(FrozenJSON(archive.document.payload_json))
    fresh.verify_transport()
    assert fresh._graph is not archive._graph
    detached = archive.document.to_dict()
    detached["artifacts"][0]["source_hash"] = "0" * 64
    with pytest.raises(ValueError):
        ReceiptArchive(FrozenJSON.freeze(detached)).verify_transport()
    assert archive.expand_validated() == original
    with pytest.raises(FrozenInstanceError):
        archive.document = original  # type: ignore[misc]


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("twice", [False, True])
def test_legacy_single_ticker_v1_v2_open_closed_reentry(shared: bool, twice: bool) -> None:
    original = source(round_trip(twice=twice), shared=shared)
    archive = encode_result(original)
    assert archive.expand_validated() == original
    d = original.to_dict()
    assert d["frozen_input"]["version"] == (2 if shared else 1)
    assert d["performance"]["fill_count"] == (4 if twice else 3)
    episodes = d["run"]["position_history"]["episodes"]
    assert len(episodes) == 2
    assert episodes[-1]["status"] == ("confirmed_full_exit" if twice else "still_open")


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "kind",
        "digest",
        "payload",
        "duplicate",
        "conflicting_duplicate",
        "unused",
        "run",
        "reference_run",
        "reference_source",
        "path",
        "overlap",
        "size",
        "root",
        "views",
        "metadata",
        "version",
        "references_type",
        "artifacts_type",
    ],
)
def test_transport_tampering_fails_closed(
    small: tuple[FrozenJSON, ReceiptArchive], damage: str
) -> None:
    _, archive = small
    d = archive.document.to_dict()
    records = d["artifacts"]
    root = next(r for r in records if r["artifact_id"] == d["root"]["artifact_id"])
    if damage == "missing":
        records.pop(0)
    elif damage == "kind":
        records[0]["kind"] = "not_a_kind"
    elif damage == "digest":
        records[0]["source_hash"] = "0" * 64
    elif damage == "payload":
        root["payload"]["report"] += "altered"
    elif damage in {"duplicate", "conflicting_duplicate"}:
        duplicate = copy.deepcopy(records[0])
        if damage == "conflicting_duplicate":
            duplicate["payload"] = {"changed": True}
        records.insert(1, duplicate)
    elif damage == "unused":
        payload = {"unused": "x" * 1100}
        ref = reference(d["run_id"], "object", digest(payload))
        records.append(
            {
                **ref,
                "expanded_bytes": len(FrozenJSON.freeze(payload).payload_json.encode()),
                "payload": payload,
                "references": [],
            }
        )
        records.sort(key=lambda r: r["artifact_id"])
    elif damage == "run":
        d["run_id"] = "0" * 64
    elif damage == "reference_run":
        root["references"][0]["run_id"] = "0" * 64
    elif damage == "reference_source":
        root["references"][0]["source_hash"] = "0" * 64
    elif damage == "path":
        root["references"][0]["path"] = ["absent"]
    elif damage == "overlap":
        root["references"].append(copy.deepcopy(root["references"][0]))
    elif damage == "size":
        root["expanded_bytes"] += 1
    elif damage == "root":
        d["root"]["artifact_id"] = "0" * 64
    elif damage == "views":
        d["views"]["run"] = d["views"]["performance"]
    elif damage == "metadata":
        d["metadata"]["event_count"] += 1
    elif damage == "version":
        d["version"] = "unsupported"
    elif damage == "references_type":
        root["references"] = None
    else:
        d["artifacts"] = None
    with pytest.raises(ValueError):
        ReceiptArchive(FrozenJSON.freeze(d)).verify_transport()


@pytest.mark.parametrize(
    "limit",
    [
        "transport_bytes",
        "expanded_bytes",
        "artifacts",
        "references",
        "depth",
        "nodes",
        "digest_bytes",
    ],
)
def test_explicit_limits(small: tuple[FrozenJSON, ReceiptArchive], limit: str) -> None:
    original, archive = small
    limits = replace(ArchiveLimits(), **{limit: 1})
    with pytest.raises(ValueError):
        encode_result(original, limits=limits)
    with pytest.raises(ValueError):
        ReceiptArchive(archive.document, limits).verify_transport()


def test_reference_like_source_keys_are_not_in_band_markers(
    small: tuple[FrozenJSON, ReceiptArchive],
) -> None:
    original, _ = small
    d = original.to_dict()
    d["literal"] = {"$ref": "literal", "$evidence": {"kind": "literal"}, "path": [None]}
    modified = FrozenJSON.freeze(d)
    archive = encode_result(modified)
    assert "".join(archive.iter_legacy_json()) == modified.payload_json
    with pytest.raises(ValueError):  # New source field is rejected by exact legacy replay.
        archive.expand_validated()


@pytest.mark.parametrize(
    "damage",
    [
        "decision",
        "admission",
        "order",
        "fill",
        "ledger",
        "event_order",
        "policy",
        "provenance",
        "availability",
        "performance",
        "timeline",
    ],
)
def test_resigned_invalid_economics_still_rejected_by_original_replay(
    small: tuple[FrozenJSON, ReceiptArchive], damage: str
) -> None:
    d = small[0].to_dict()
    events = d["run"]["events"]
    first = next(e for e in events if e["decisions"])
    filled = next(e for e in events if any(x["status"] == "filled" for x in e["executions"]))
    if damage == "decision":
        first["decisions"][0]["result"]["action"] = "WAIT"
    elif damage == "admission":
        first["candidates"][0]["budget_krw"] = "1"
    elif damage == "order":
        first["admissions"][0]["order_id"] = "0" * 64
    elif damage == "fill":
        filled["executions"][0]["fill"]["price_krw"] = "1"
    elif damage == "ledger":
        d["run"]["final_checkpoint"]["state_hash"] = "0" * 64
    elif damage == "event_order":
        events.reverse()
    elif damage == "policy":
        first["admissions"][0]["execution_policy"]["buy_fee_rate"] = "0"
    elif damage in {"provenance", "availability"}:
        f = d["frozen_input"]
        # Change the versioned source itself, not just its archive pointer.
        artifact = next(a for a in f["evidence_table"]["artifacts"] if a["kind"] == "analysis")
        if damage == "provenance":
            artifact["payload"]["model_id"] = "0" * 64
        else:
            artifact["payload"]["created_at"]["signal"] = "2099-01-01T00:00:00Z"
    elif damage == "performance":
        d["performance"]["ledger_realized_pnl"] = "123456"
    else:
        d["detailed_report"]["timeline"].reverse()
    for key in ("run", "performance", "detailed_report"):
        d[key + "_hash"] = digest(d[key])
    archive = encode_result(FrozenJSON.freeze(d))
    archive.verify_transport()  # A newly consistent transport is not economic approval.
    with pytest.raises(ValueError):
        archive.expand_validated()


@pytest.mark.parametrize("family", ["linear", "hgb"])
def test_genuine_single_inference_compatibility(tmp_path: Path, family: str) -> None:
    runner = (scored_runner if family == "linear" else hgb_runner)(tmp_path)
    original = source(runner)
    restored = encode_result(original).expand_validated()
    assert restored == original
    assert restored.to_dict()["detailed_report_hash"] == original.to_dict()["detailed_report_hash"]


def test_historical_backtest_result_existing_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = setup_request(tmp_path, monkeypatch, cycle=True)
    original = execute(request, tmp_path).document
    assert encode_result(original).expand_validated() == original
    for field in ("quality", "manifest", "dates", "availability"):
        d = original.to_dict()
        c = d["request"]["config"]
        if field == "quality":
            c["quality"]["quality_flags"] = ["unverified"]
        elif field == "manifest":
            c["manifest"]["initial_account"]["account_id"] = "another-account"
        elif field == "dates":
            c["decision_dates"].insert(1, "2026-01-12")
        else:
            c["availability_assumption"] = "unsupported"
        with pytest.raises(ValueError):
            encode_result(FrozenJSON.freeze(d)).expand_validated()


@pytest.fixture(scope="module", params=["linear", "hgb"])
def genuine_five(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> FrozenJSON:
    root = tmp_path_factory.mktemp("receipt-five-" + str(request.param))
    specification, provider = prepared(root, str(request.param))
    before = {
        n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in ("market.db", "analysis.db")
    }
    result = execute_fixture(root, specification, provider)
    assert before == {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in before}
    return result


def test_genuine_five_model_provenance_and_complete_result(genuine_five: FrozenJSON) -> None:
    archive = encode_result(genuine_five)
    assert archive.expand_validated() == genuine_five
    assert len(archive.document.payload_json.encode()) < len(genuine_five.payload_json.encode())


@pytest.fixture(scope="module", params=["pending", "open", "stale", "releases"])
def five_mechanics(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> FrozenJSON:
    case = str(request.param)
    root = tmp_path_factory.mktemp("receipt-mechanics-" + case)
    specification, _ = prepared(root, controlled=True, omit_last=case == "stale")
    d = specification.document.to_dict()
    first = d["config"]["decision_dates"][0]
    market = ReadOnlyMarketStore(root / "market.db")
    next_day = min(
        b.trading_date.isoformat()
        for b in market.read(TICKERS[0], date.fromisoformat(first), date.fromisoformat(d["end"]))
        if b.trading_date.isoformat() > first
    )
    market.close()
    d["config"]["decision_dates"] = [first]
    if case != "stale":
        d["end"] = first if case == "pending" else next_day
    if case in {"pending", "open"}:
        d["initial_cash"] = "3500"
    if case == "open":
        d["config"]["decision_dates"].append(next_day)
    if case == "stale":
        d["config"]["valuation_policy"]["max_age_seconds"] = 1
    if case == "releases":
        d["config"]["admission_policy"]["expires_at"] = datetime.combine(
            date.fromisoformat(next_day) + timedelta(days=1), time(), SEOUL
        ).isoformat()
        d["config"]["release_steps"] = [
            {"trading_date": next_day, "ticker": t, "status": status}
            for t, status in zip(TICKERS[:3], ("cancelled", "expired", "rejected"), strict=True)
        ]
    result = mechanics_run(
        root, FiveTickerRequest(FrozenJSON.freeze(d)), contexts={next_day: "flat"}
    )
    (root / "result.json").write_text(result.payload_json)
    return result


def test_five_mechanics_exact_economics_and_states(five_mechanics: FrozenJSON) -> None:
    d = five_mechanics.to_dict()
    archive = encode_result(five_mechanics)
    assert archive.expand_validated() == five_mechanics
    assert len(archive.document.payload_json.encode()) < len(five_mechanics.payload_json.encode())
    statuses = {o["status"] for o in d["detailed_report"]["terminal_orders"]}
    if d["frozen_input"]["request"]["config"]["release_steps"]:
        assert {"cancelled", "expired", "rejected"} <= statuses
    if d["frozen_input"]["request"]["config"]["valuation_policy"]["max_age_seconds"] == 1:
        assert d["detailed_report"]["final_valuation"]["total_equity"] is None
        assert d["performance"]["unrealized_pnl"] is None
    kinds = {a["kind"] for a in archive.document.to_dict()["artifacts"]}
    assert "acceptance" in kinds and "order" in kinds
    if d["performance"]["fill_count"]:
        assert "fill" in kinds and "execution" in kinds


def test_changed_release_time_rejected(five_mechanics: FrozenJSON) -> None:
    d = five_mechanics.to_dict()
    releases = [x for e in d["run"]["events"] for x in e.get("releases", [])]
    if not releases:
        return
    releases[0]["ledger_command"]["known_at"] = "2099-01-01T00:00:00Z"
    d["run_hash"] = digest(d["run"])
    with pytest.raises(ValueError):
        encode_result(FrozenJSON.freeze(d)).expand_validated()


def test_actual_cross_run_substitution_and_distinct_receipts(
    small: tuple[FrozenJSON, ReceiptArchive],
) -> None:
    original, archive = small
    other = encode_result(source(round_trip(twice=False)))
    assert archive.document.to_dict()["run_id"] != other.document.to_dict()["run_id"]
    d = archive.document.to_dict()
    d["artifacts"][0] = other.document.to_dict()["artifacts"][0]
    d["artifacts"].sort(key=lambda a: a["artifact_id"])
    with pytest.raises(ValueError, match="archive_cross_run_reference"):
        ReceiptArchive(FrozenJSON.freeze(d)).verify_transport()
    accepted = [
        a
        for e in original.to_dict()["run"]["events"]
        for a in e["admissions"]
        if a["status"] == "accepted"
    ]
    records = archive.document.to_dict()["artifacts"]
    acceptances = [a for a in records if a["kind"] == "acceptance"]
    assert len(acceptances) == len(accepted) == 4
    assert len({a["source_hash"] for a in acceptances}) == 4
    for acceptance in acceptances:
        references = [
            r
            for a in records
            for r in a["references"]
            if r["artifact_id"] == acceptance["artifact_id"]
        ]
        assert len(references) >= 2  # Original journal and lineage share this exact receipt.


def test_digest_corruption_preserving_length(small: tuple[FrozenJSON, ReceiptArchive]) -> None:
    d = small[1].document.to_dict()
    record = next(a for a in d["artifacts"] if a["kind"] == "event")
    record["payload"]["cutoff"] = record["payload"]["cutoff"].replace("2026", "2027")
    with pytest.raises(ValueError, match="archive_payload_digest_mismatch"):
        ReceiptArchive(FrozenJSON.freeze(d)).verify_transport()


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"version": 123},
        {"run": None},
        {"run": {}, "performance": {}, "detailed_report": {}, "run_hash": "x"},
    ],
)
def test_invalid_source_fails_closed(value: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        encode_result(FrozenJSON.freeze(value))


def test_invalid_json_and_limits_are_explicit() -> None:
    with pytest.raises(ValueError, match="duplicate_evidence_key"):
        ReceiptArchive.from_json('{"version":1,"version":2}')
    with pytest.raises(ValueError):
        ReceiptArchive.from_json("[]")
    with pytest.raises(ValueError, match="invalid_archive_limits"):
        ArchiveLimits(depth=0)
    with pytest.raises(ValueError, match="invalid_archive_limits"):
        ArchiveLimits(artifacts=True)


def test_cycle_rejected_before_expansion(small: tuple[FrozenJSON, ReceiptArchive]) -> None:
    d = small[1].document.to_dict()
    root = next(a for a in d["artifacts"] if a["artifact_id"] == d["root"]["artifact_id"])
    edge = next(e for e in root["references"] if e["path"] == ["run"])
    edge.update(d["root"])
    with pytest.raises(ValueError, match="archive_cycle"):
        ReceiptArchive(FrozenJSON.freeze(d)).verify_transport()


def test_repeated_hashing_counts_toward_encoding_budget(
    small: tuple[FrozenJSON, ReceiptArchive], monkeypatch: pytest.MonkeyPatch
) -> None:
    import donghak_stock_vision.backtest.receipt_archive as codec

    with monkeypatch.context() as context:
        context.setattr(codec, "prune_single_use", lambda artifacts: None)
        unpruned = encode_result(small[0]).document.to_dict()
    unique_work = sum(a["expanded_bytes"] for a in unpruned["artifacts"])
    # Duplicate source bodies must still be hashed before their equality is known.
    with pytest.raises(ValueError, match="archive_digest_work_limit"):
        encode_result(small[0], limits=replace(ArchiveLimits(), digest_bytes=unique_work))
