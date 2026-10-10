"""Offline semantic decisions never substitute for final security coverage."""

import ast
import copy
import hashlib
import inspect
import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest import corporate_action_semantic_review as review
from donghak_stock_vision.backtest import verify_corporate_action_semantic_review as cli
from donghak_stock_vision.backtest.corporate_action_coverage import CorporateActionEventEvidence
from donghak_stock_vision.backtest.corporate_action_document_review import review_document
from donghak_stock_vision.backtest.dart_evidence import source_valid
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import canonical, digest


@pytest.fixture
def index() -> dict[str, Any]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "filing.xml", "<document>split 2024-08-09 private document text</document>"
        )
        archive.writestr("attachment.xml", "<document>additional evidence</document>")
    raw = buffer.getvalue()
    document = review_document(
        raw,
        rcept_no="20240701000001",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        plan_row={
            "rcept_no": "20240701000001",
            "repository_tickers": ["005930", "00088K"],
            "review_categories": ["split"],
            "lifecycle_review": False,
        },
    )
    document["candidate_event_classes"] = ["split"]
    value = {"version": review.INDEX_VERSION, "document_count": 1, "documents": [document]}
    return {"artifact": value, "fingerprint": digest(value)}


def decisions(index: dict[str, Any], *, disposition: str = "CONFIRMED_EVENT") -> dict[str, Any]:
    document = index["artifact"]["documents"][0]
    entry = next(entry for entry in document["entries"] if entry["name"] == "filing.xml")
    return {
        "version": review.DECISIONS_VERSION,
        "document_review_index_fingerprint": index["fingerprint"],
        "records": [
            {
                "rcept_no": document["rcept_no"],
                "ticker": "005930",
                "raw_sha256": document["raw_sha256"],
                "disposition": disposition,
                "review_reference": "reviewer-note-1",
                "events": [
                    {
                        "event_class": "split",
                        "effective_date": "2024-08-09",
                        "evidence_entry_name": entry["name"],
                        "evidence_entry_sha256": entry["sha256"],
                    }
                ]
                if disposition == "CONFIRMED_EVENT"
                else [],
            }
        ],
    }


def build(index: dict[str, Any], value: dict[str, Any]) -> FrozenJSON:
    return review.build_semantic_review(index, FrozenJSON.freeze(value))


def repin(index: dict[str, Any]) -> None:
    index["fingerprint"] = digest(index["artifact"])


def test_explicit_confirmed_event(index: dict[str, Any]) -> None:
    value = FrozenJSON.freeze(decisions(index))
    events = review.approved_event_evidence(index, value)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CorporateActionEventEvidence)
    assert (event.ticker, event.event_class, event.effective_date) == (
        "005930",
        "split",
        date(2024, 8, 9),
    )
    assert event.document_reference == "20240701000001"
    assert event.review_reference == "reviewer-note-1"
    assert source_valid(event.source.to_dict())
    assert event.source.to_dict()["url"] == review.DOCUMENT_ENDPOINT
    assert event.source.to_dict()["evidence_entry_name"] == "filing.xml"
    result = review.build_semantic_review(index, value).to_dict()
    assert result["approved_events"] == [event.to_dict()]
    assert result["pending_document_security_pair_count"] == 1
    assert result["corporate_action_gate_verified"] is False


def test_candidates_never_approve(index: dict[str, Any]) -> None:
    value = decisions(index)
    value["records"] = []
    result = build(index, value).to_dict()
    assert index["artifact"]["documents"][0]["date_candidates"] == ["2024-08-09"]
    assert result["approved_event_count"] == 0
    assert result["unreviewed_document_count"] == 1
    assert result["pending_document_security_pair_count"] == 2
    assert result["corporate_action_gate_verified"] is False


def test_explicit_date_not_required_in_candidates(index: dict[str, Any]) -> None:
    value = decisions(index)
    value["records"][0]["events"][0]["effective_date"] = "2024-09-19"
    result = build(index, value).to_dict()
    assert result["approved_events"][0]["effective_date"] == "2024-09-19"


@pytest.mark.parametrize(
    ("field", "bad", "reason"),
    [
        ("rcept_no", "20240701000002", "unknown_review_document"),
        ("raw_sha256", "a" * 64, "document_hash_mismatch"),
        ("ticker", "123456", "ticker_not_linked"),
        ("ticker", "00593", "exact_security_code"),
        ("ticker", "00088k", "exact_security_code"),
        ("review_reference", "  ", "nonempty"),
        ("disposition", "APPROVED", "unsupported_review_disposition"),
        ("disposition", None, "unsupported_review_disposition"),
    ],
)
def test_record_rejection(index: dict[str, Any], field: str, bad: Any, reason: str) -> None:
    value = decisions(index)
    value["records"][0][field] = bad
    with pytest.raises(ContractError, match=reason):
        build(index, value)


@pytest.mark.parametrize(
    ("field", "bad", "reason"),
    [
        ("event_class", "fictional", "unsupported_corporate_action_class"),
        ("effective_date", "2024-02-30", "invalid_effective_date"),
        ("effective_date", "20240809", "invalid_effective_date"),
        ("effective_date", "2024-W32-5", "invalid_effective_date"),
        ("effective_date", None, "nonempty"),
        ("evidence_entry_name", "missing.xml", "unknown_evidence_entry"),
        ("evidence_entry_sha256", "b" * 64, "evidence_entry_hash_mismatch"),
    ],
)
def test_event_rejection(index: dict[str, Any], field: str, bad: Any, reason: str) -> None:
    value = decisions(index)
    value["records"][0]["events"][0][field] = bad
    with pytest.raises(ContractError, match=reason):
        build(index, value)


def test_alphanumeric_security(index: dict[str, Any]) -> None:
    value = decisions(index)
    value["records"][0]["ticker"] = "00088K"
    assert build(index, value).to_dict()["approved_events"][0]["ticker"] == "00088K"


@pytest.mark.parametrize("target", ["wrapper", "decision", "index_version", "decision_version"])
def test_identity_mismatch(index: dict[str, Any], target: str) -> None:
    value = decisions(index)
    if target == "wrapper":
        index["fingerprint"] = "a" * 64
    elif target == "decision":
        value["document_review_index_fingerprint"] = "a" * 64
    elif target == "index_version":
        index["artifact"]["version"] = "future_index_v2"
        repin(index)
    else:
        value["version"] = "future_decisions_v2"
    with pytest.raises(ContractError, match="fingerprint_mismatch|version"):
        build(index, value)


def test_duplicate_pair(index: dict[str, Any]) -> None:
    value = decisions(index)
    value["records"].append(copy.deepcopy(value["records"][0]))
    with pytest.raises(ContractError, match="duplicate_review_decision"):
        build(index, value)


@pytest.mark.parametrize("different_attachment", [False, True])
def test_duplicate_semantic_event(index: dict[str, Any], different_attachment: bool) -> None:
    value = decisions(index)
    duplicate = copy.deepcopy(value["records"][0]["events"][0])
    if different_attachment:
        entry = index["artifact"]["documents"][0]["entries"][0]
        duplicate.update(evidence_entry_name=entry["name"], evidence_entry_sha256=entry["sha256"])
    value["records"][0]["events"].append(duplicate)
    with pytest.raises(ContractError, match="duplicate_semantic_event"):
        build(index, value)


@pytest.mark.parametrize("disposition", list(review.ReviewDisposition))
def test_disposition_event_cardinality(index: dict[str, Any], disposition: str) -> None:
    value = decisions(index)
    value["records"][0]["disposition"] = disposition
    if disposition == "CONFIRMED_EVENT":
        value["records"][0]["events"] = []
    with pytest.raises(ContractError, match="events_disposition_mismatch"):
        build(index, value)


@pytest.mark.parametrize("disposition", ["NO_RELEVANT_EVENT_IN_DOCUMENT", "UNRESOLVED"])
def test_non_event_never_promotes_coverage(index: dict[str, Any], disposition: str) -> None:
    value = decisions(index, disposition=disposition)
    result = build(index, value).to_dict()
    assert review.approved_event_evidence(index, FrozenJSON.freeze(value)) == ()
    assert result["approved_events"] == []
    assert result["coverage_records_approved"] == 0
    for key in (
        "authoritative_no_event_semantics_verified",
        "corporate_action_gate_verified",
        "historical_identity_verified",
        "prices_adjusted",
        "prediction_or_backtest_invoked",
    ):
        assert result[key] is False
    assert "authoritative_complete_semantics" not in result
    assert result["unresolved_record_count"] == int(disposition == "UNRESOLVED")


def test_partial_multi_security_review_and_determinism(index: dict[str, Any]) -> None:
    value = decisions(index)
    extra = copy.deepcopy(value["records"][0])
    extra["ticker"] = "00088K"
    value["records"].append(extra)
    for record in value["records"]:
        second = copy.deepcopy(record["events"][0])
        second.update(event_class="merger", effective_date="2024-09-01")
        record["events"].append(second)
    first = build(index, value)
    value["records"].reverse()
    for record in value["records"]:
        record["events"].reverse()
    assert build(index, value) == first
    result = first.to_dict()
    assert result["approved_event_count"] == 4
    assert result["reviewed_document_count"] == 1
    assert result["pending_document_security_pair_count"] == 0
    assert result["corporate_action_gate_verified"] is False
    assert [event["ticker"] for event in result["approved_events"]] == [
        "00088K",
        "00088K",
        "005930",
        "005930",
    ]
    value["records"][0]["review_reference"] = "different explicit review"
    assert build(index, value).identifier != first.identifier


@pytest.mark.parametrize("kind", ["documents", "entries", "repository_tickers"])
def test_ambiguous_index_rejected(index: dict[str, Any], kind: str) -> None:
    if kind == "documents":
        values = index["artifact"]["documents"]
        index["artifact"]["document_count"] = 2
    else:
        values = index["artifact"]["documents"][0][kind]
    values.append(copy.deepcopy(values[0]))
    repin(index)
    with pytest.raises(ContractError, match="duplicate_indexed"):
        build(index, decisions(index))


def test_unreadable_document_cannot_supply_inferred_ticker(index: dict[str, Any]) -> None:
    document = index["artifact"]["documents"][0]
    value = decisions(index)
    document.update(parse_status="DOCUMENT_ARCHIVE_UNREADABLE", entries=[])
    del document["repository_tickers"]
    repin(index)
    value["document_review_index_fingerprint"] = index["fingerprint"]
    with pytest.raises(ContractError, match="ticker_not_linked"):
        build(index, value)
    value["records"] = []
    assert build(index, value).to_dict()["unreviewed_document_count"] == 1


@pytest.mark.parametrize("level", ["root", "record", "event"])
def test_unknown_fields_fail_closed(index: dict[str, Any], level: str) -> None:
    value = decisions(index)
    target = value if level == "root" else value["records"][0]
    if level == "event":
        target = target["events"][0]
    target["source_url"] = "https://untrusted.invalid"
    with pytest.raises(ContractError, match="missing_or_unknown_fields"):
        build(index, value)


def test_missing_explicit_date_never_uses_candidate(index: dict[str, Any]) -> None:
    value = decisions(index)
    del value["records"][0]["events"][0]["effective_date"]
    with pytest.raises(ContractError, match="missing_or_unknown_fields"):
        build(index, value)


def test_cli_wrapped_index_and_aggregate_output(
    index: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    index_path, decision_path, output = (
        tmp_path / name for name in ("index.json", "decisions.json", "output.json")
    )
    index_path.write_text(canonical(index))
    decision_path.write_text(canonical(decisions(index)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--index",
            str(index_path),
            "--decisions",
            str(decision_path),
            "--output",
            str(output),
        ],
    )
    cli.main()
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    wrapped = json.loads(output.read_text())
    assert wrapped["fingerprint"] == digest(wrapped["artifact"]) == summary["fingerprint"]
    assert output.read_text() == canonical(wrapped) + "\n"
    assert summary["approved_event_count"] == 1
    assert summary["corporate_action_gate_verified"] is False
    assert "private document text" not in stdout
    assert "20240701000001" not in stdout
    assert "reviewer-note" not in stdout
    assert "approved_events" not in summary
    before = output.read_bytes()
    with pytest.raises(ContractError, match="output_must_be_new_file"):
        cli.main()
    assert output.read_bytes() == before


def test_source_boundary_has_no_execution_or_storage_calls() -> None:
    forbidden = {
        "fit",
        "predict",
        "predict_proba",
        "backtest",
        "generate_signal",
        "adjust_prices",
        "load_dotenv",
        "getenv",
        "connect",
        "urlopen",
        "get",
        "post",
    }
    for module in (review, cli):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                )
                assert not any(
                    name.startswith(("httpx", "requests", "sqlite3", "dotenv", "sklearn", "pickle"))
                    for name in names
                )
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                # Dictionary lookup is the only permitted 'get' operation.
                assert name not in forbidden - {"get"}
                assert name != "CorporateActionSecurityCoverage"


@pytest.mark.parametrize("disposition", ["CONFIRMED_EVENT", "NO_RELEVANT_EVENT_IN_DOCUMENT"])
def test_no_text_document_cannot_be_concluded(index: dict[str, Any], disposition: str) -> None:
    value = decisions(index, disposition=disposition)
    index["artifact"]["documents"][0].update(parse_status="NO_TEXT_ENTRY", entries=[])
    repin(index)
    value["document_review_index_fingerprint"] = index["fingerprint"]
    with pytest.raises(ContractError, match="document_not_reviewable"):
        build(index, value)


@pytest.mark.parametrize("corruption", ["fingerprint", "duplicate_key"])
def test_cli_invalid_input_creates_no_output(
    index: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    value = decisions(index)
    if corruption == "fingerprint":
        index["fingerprint"] = "a" * 64
    index_path = tmp_path / "index.json"
    decision_path = tmp_path / "decisions.json"
    output = tmp_path / "output.json"
    index_path.write_text(canonical(index))
    serialized = canonical(value)
    if corruption == "duplicate_key":
        serialized = '{"records":[],' + serialized[1:]
    decision_path.write_text(serialized)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--index",
            str(index_path),
            "--decisions",
            str(decision_path),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(ContractError):
        cli.main()
    assert not output.exists()


def test_separate_documents_are_not_silently_deduplicated(index: dict[str, Any]) -> None:
    second = copy.deepcopy(index["artifact"]["documents"][0])
    second["rcept_no"] = "20240702000001"
    index["artifact"]["documents"].append(second)
    index["artifact"]["document_count"] = 2
    repin(index)
    value = decisions(index)
    record = copy.deepcopy(value["records"][0])
    record["rcept_no"] = second["rcept_no"]
    value["records"].append(record)
    result = build(index, value).to_dict()
    assert result["approved_event_count"] == 2
    assert result["reviewed_document_count"] == 2
    assert result["corporate_action_gate_verified"] is False
