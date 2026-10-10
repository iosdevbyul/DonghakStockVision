"""Synthetic pinned evidence only; no runner, model, signal or real-data execution."""

import ast
import inspect
import json
import sys
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from donghak_stock_vision.backtest import runner_tail as rt
from donghak_stock_vision.backtest import verify_runner_tail as cli
from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy
from donghak_stock_vision.backtest.corporate_action_coverage import (
    CorporateActionSecurityCoverage,
    summarize_corporate_action_coverage,
)
from donghak_stock_vision.backtest.dart_evidence import EVENT_CLASSES
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import AnchorRange, derive_boundaries
from donghak_stock_vision.backtest.run_evidence import check, settlement_check
from donghak_stock_vision.backtest.security_lifecycle import (
    LifecycleEvidence,
    LifecycleReason,
    reconcile_lifecycle,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import canonical, digest

H = "a" * 64
TAIL = ["2025-07-01", "2025-07-02", "2025-07-03", "2025-07-04", "2025-07-07"]
SOURCE = FrozenJSON.freeze(
    dict(url="https://kind.krx.co.kr/fixture", raw_sha256=H, document_reference="synthetic-only")
)


def link(data: dict[str, Any]) -> None:
    """Fixture authors pin parent edges before an independent verifier receives them."""
    calendar_pin = digest(data["calendar"])
    for name in ("aggregate", "per_security"):
        data["calendar_verification"][name]["details"]["calendar_pin"] = calendar_pin
    data["settlement"]["calendar_fingerprint"] = calendar_pin
    data["settlement"]["verification"]["details"]["calendar_pin"] = calendar_pin
    data["parent_gaps"]["parent_calendar_fingerprint"] = digest(data["calendar_verification"])
    manifest = data["lifecycle_manifest"]
    manifest["parent_gap_fingerprint"] = digest(data["parent_gaps"])
    manifest["artifact_fingerprints"]["missing-session-reconciliation"] = digest(
        data["lifecycle_reconciliation"]
    )
    data["lifecycle"]["manifest_fingerprint"] = digest(manifest)


def inputs_and_pins(data: dict[str, Any]) -> tuple[dict[str, FrozenJSON], FrozenJSON]:
    inputs = {
        name: FrozenJSON.freeze(
            value
            if name in {"approval", "universe"}
            else {"artifact": value, "fingerprint": digest(value)}
        )
        for name, value in data.items()
    }
    pins = FrozenJSON.freeze(
        {
            "version": rt.PINS_VERSION,
            "artifacts": {name: digest(value) for name, value in data.items()},
            "parent_integrated_fingerprint": "b" * 64,
            "parent_mapping_fingerprint": "c" * 64,
        }
    )
    return inputs, pins


def verify(data: dict[str, Any]) -> FrozenJSON:
    inputs, pins = inputs_and_pins(data)
    return rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint=pins.identifier)


def assert_blocked(data: dict[str, Any]) -> None:
    try:
        result = verify(data).to_dict()
    except ContractError:
        return
    assert result["runner_tail_integrated"] is False
    assert result["blockers"]


@pytest.fixture
def evidence() -> dict[str, Any]:
    # Weekday construction belongs only to synthetic fixtures, never production evidence.
    days = {}
    cursor = date(2024, 6, 1)
    while cursor <= date(2025, 7, 31):
        days[cursor.isoformat()] = "weekend" if cursor.weekday() >= 5 else "session"
        cursor += timedelta(days=1)
    days["2024-08-15"] = "holiday"
    days["2025-06-03"] = "extraordinary_closure"
    exclusions = ["2024-03-28", "2025-03-21"]
    rule = dict(url="https://global.krx.co.kr/fixture", raw_sha256=H, document_id="fixture-T2")
    calendar = dict(
        version="kind_cash_calendar_v1",
        days=days,
        rule_source=rule,
        rule="kospi_cash_equity_T_plus_2_settlement_sessions",
        effective_start="2024-06-01",
        effective_end="2025-07-31",
        source_urls=["https://kind.krx.co.kr/fixture"],
        source_content_hashes=[H],
        review_reference="synthetic_calendar_review",
        research_exclusions_are_holidays=False,
    )
    frozen = FrozenJSON.freeze(calendar)
    boundaries = derive_boundaries(
        AnchorRange(date(2024, 7, 1), date(2025, 6, 30)),
        tuple(map(date.fromisoformat, exclusions)),
        frozen,
        frozen.identifier,
    ).to_dict()
    universe = tuple(f"{number:06d}" for number in range(1, 994))
    universe_doc = dict(
        schema_version=1,
        count=993,
        declared_as_of="2025-07-01",
        selection_basis="synthetic_fixed_universe",
        tickers=list(universe),
    )
    policy = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
    approval = dict(
        version="baseline_run_approval_v1",
        start="2024-07-01",
        end="2025-06-30",
        universe_sha256=digest(universe_doc),
        market_sha256=H,
        excluded_dates=exclusions,
        model_and_policy_pins=dict(
            baseline_policy=policy.fingerprint,
            quality_policy=policy.quality_policy.fingerprint,
            cost_policy=policy.require_costs().fingerprint,
            runner_version="baseline_runner_v1",
        ),
    )
    sessions = [
        day
        for day, state in days.items()
        if state == "session" and "2024-06-14" <= day <= "2025-07-07" and day not in exclusions
    ]
    gap = {universe[0]: ["2024-06-14"]}
    comparison = dict(
        version="official_calendar_comparison_v1",
        start="2024-06-14",
        end="2025-07-07",
        database=dict(sha256_before=H, sha256_after=H, integrity=["ok"]),
        ordering=dict(
            date_count=len(sessions),
            duplicates=0,
            nonincreasing=0,
            malformed=0,
            out_of_range=0,
            unexpected_ticker=0,
            metadata_mismatch=0,
        ),
        aggregate=check(
            "CALENDAR_EVIDENCE",
            [],
            calendar_pin=frozen.identifier,
            exclusions=exclusions,
            missing_sessions={},
        ).to_dict(),
        per_security=check(
            "CALENDAR_EVIDENCE",
            ["unexplained_missing_ticker_sessions"],
            calendar_pin=frozen.identifier,
            exclusions=exclusions,
            missing_sessions=gap,
        ).to_dict(),
    )
    reconciliation = reconcile_lifecycle(
        FrozenJSON.freeze(gap),
        (
            LifecycleEvidence(
                ticker=universe[0],
                security_identity="fixture-security",
                reason=LifecycleReason.NOT_YET_LISTED,
                start=date(2024, 6, 14),
                end=date(2024, 6, 14),
                effective_date=date(2024, 6, 17),
                source=SOURCE,
                identity_source=SOURCE,
                exact_identity_verified=True,
                effective_date_verified=True,
                review_reference="fixture-lifecycle",
            ),
        ),
    ).to_dict()
    coverage = tuple(
        CorporateActionSecurityCoverage(
            ticker=ticker,
            security_identity=f"fixture-{ticker}",
            identity_source=SOURCE,
            identity_effective_start=date(2024, 6, 14),
            identity_effective_end=date(2025, 7, 7),
            historical_identity_verified=True,
            query_start=date(2024, 6, 14),
            query_end=date(2025, 7, 7),
            covered_classes=EVENT_CLASSES,
            query_complete=True,
            authoritative_complete_semantics=True,
            query_sources=(SOURCE,),
            events=(),
            review_reference="synthetic-coverage",
        )
        for ticker in universe
    )
    data = dict(
        approval=approval,
        universe=universe_doc,
        calendar=calendar,
        boundaries=boundaries,
        calendar_verification=comparison,
        settlement=dict(
            calendar_fingerprint=frozen.identifier,
            official_rule_source=rule,
            possible_trade_sessions=len(boundaries["settlement_mapping"]),
            verification=settlement_check(
                tuple(map(date.fromisoformat, boundaries["settlement_mapping"])),
                frozen,
                frozen.identifier,
            ).to_dict(),
        ),
        parent_gaps=dict(
            version="missing_session_review_v1",
            parent_calendar_fingerprint="",
            explained=0,
            unresolved=1,
            total_missing=1,
            securities=[
                dict(
                    ticker=universe[0],
                    explained={},
                    unexplained=["2024-06-14"],
                    bar_absence_used_as_lifecycle_evidence=False,
                )
            ],
        ),
        lifecycle_reconciliation=reconciliation,
        lifecycle_manifest=dict(
            version="baseline_security_lifecycle_v1",
            parent_gap_fingerprint="",
            parent_integrated_fingerprint="b" * 64,
            parent_mapping_fingerprint="c" * 64,
            artifact_fingerprints={},
            starting_unexplained_sessions=1,
            starting_unresolved_securities=1,
            newly_explained=1,
            unexplained=0,
        ),
        lifecycle=dict(
            version="baseline_security_lifecycle_v1",
            manifest_fingerprint="",
            parent_integrated_fingerprint="b" * 64,
            exchange_calendar="VERIFIED",
            security_calendar="VERIFIED",
            settlement_mapping="VERIFIED",
            corporate_action="BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
            policy_override=False,
            prediction_or_backtest_invoked=False,
        ),
        corporate_coverage=summarize_corporate_action_coverage(universe, coverage).to_dict(),
    )
    link(data)
    return data


def test_complete_synthetic_tail_is_not_freeze_or_execution(evidence: dict[str, Any]) -> None:
    result = verify(evidence).to_dict()
    assert result["runner_tail_integrated"] is True
    assert result["blockers"] == []
    assert result["warmup_session_count"] == 11
    assert result["warmup_first"] == "2024-06-14"
    assert result["warmup_last"] == "2024-06-28"
    assert result["execution_tail_sessions"] == TAIL
    assert result["final_settlement_date"] == "2025-07-09"
    assert result["settlement_only_sessions"] == ["2025-07-08", "2025-07-09"]
    for key in (
        "run_input_frozen",
        "backtest_authorized",
        "real_backtest_invoked",
        "model_fit_invoked",
        "prediction_invoked",
        "performance_calculated",
        "prices_adjusted",
        "entry_retry_authorized",
    ):
        assert result[key] is False


def test_window_partitions_and_only_one_entry_candidate(evidence: dict[str, Any]) -> None:
    result = verify(evidence).to_dict()
    window = rt.RunnerTailWindow(
        *(
            tuple(map(date.fromisoformat, result[key]))
            for key in (
                "warmup_sessions",
                "signal_sessions",
                "execution_tail_sessions",
                "settlement_only_sessions",
            )
        )
    )
    assert window.entry_candidate(date(2025, 6, 30)) == date(2025, 7, 1)
    for day in window.warmup + window.execution + window.settlement_only:
        assert not window.permits_signal(day)
        with pytest.raises(ContractError, match="outside_signal_anchor_window"):
            window.entry_candidate(day)
    assert not set(window.execution).intersection(window.settlement_only)
    assert not window.permits_signal(date(2025, 3, 21))
    with pytest.raises(FrozenInstanceError):
        window.signals = ()  # type: ignore[misc]
    with pytest.raises(ContractError, match="eleven_warmup"):
        rt.RunnerTailWindow(
            window.warmup[1:], window.signals, window.execution, window.settlement_only
        )


@pytest.mark.parametrize("day", ["2024-06-14", "2025-07-01", "2025-07-03", "2025-07-07"])
def test_missing_required_calendar_day_blocks(evidence: dict[str, Any], day: str) -> None:
    del evidence["calendar"]["days"][day]
    link(evidence)
    assert_blocked(evidence)


@pytest.mark.parametrize("day", ["2024-06-14", *TAIL])
def test_wrong_non_session_classification_cannot_shift_tail(
    evidence: dict[str, Any], day: str
) -> None:
    evidence["calendar"]["days"][day] = "holiday"
    link(evidence)
    assert_blocked(evidence)


@pytest.mark.parametrize("change", ["missing", "wrong", "non_session", "unsupported", "unverified"])
def test_settlement_failures(evidence: dict[str, Any], change: str) -> None:
    settlement = evidence["settlement"]
    mapping = settlement["verification"]["details"]["mapping"]
    if change == "missing":
        del mapping["2025-07-07"]
    elif change == "wrong":
        mapping["2025-07-07"] = "2025-07-08"
    elif change == "non_session":
        mapping["2025-07-07"] = "2025-07-12"
    elif change == "unsupported":
        mapping["2025-07-07"] = "2030-01-02"
    else:
        settlement["verification"].update(
            status="BLOCKED_ON_SETTLEMENT_EVIDENCE", reasons=["unverified"]
        )
    assert "BLOCKED_ON_SETTLEMENT_EVIDENCE" in verify(evidence).to_dict()["blockers"]


def test_t2_weekend_crossing(evidence: dict[str, Any]) -> None:
    assert {
        day: evidence["settlement"]["verification"]["details"]["mapping"][day] for day in TAIL
    } == {
        "2025-07-01": "2025-07-03",
        "2025-07-02": "2025-07-04",
        "2025-07-03": "2025-07-07",
        "2025-07-04": "2025-07-08",
        "2025-07-07": "2025-07-09",
    }


@pytest.mark.parametrize(
    "key",
    [
        "corporate_action_gate_verified",
        "complete_corporate_coverage_count",
        "historical_identity_verified_count",
        "universe_count",
    ],
)
def test_corporate_gate_and_counts_required(evidence: dict[str, Any], key: str) -> None:
    evidence["corporate_coverage"][key] = False if key.endswith("verified") else 992
    result = verify(evidence).to_dict()
    assert result["runner_tail_integrated"] is False
    assert result["blockers"]


def test_wrong_corporate_universe(evidence: dict[str, Any]) -> None:
    statuses = evidence["corporate_coverage"]["statuses"]
    statuses["999999"] = statuses.pop("000001")
    with pytest.raises(ContractError, match="corporate_action_universe_mismatch"):
        verify(evidence)


def test_current_real_state_mirrored_by_fixture_remains_blocked(evidence: dict[str, Any]) -> None:
    universe = tuple(evidence["universe"]["tickers"])
    evidence["corporate_coverage"] = summarize_corporate_action_coverage(universe, ()).to_dict()
    result = verify(evidence).to_dict()
    assert result["runner_tail_integrated"] is False
    assert set(result["blockers"]) == {
        "BLOCKED_ON_HISTORICAL_IDENTITY",
        "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
    }


@pytest.mark.parametrize("key", ["exchange_calendar", "security_calendar", "settlement_mapping"])
def test_upstream_gate_unverified(evidence: dict[str, Any], key: str) -> None:
    evidence["lifecycle"][key] = (
        "BLOCKED_ON_SETTLEMENT_EVIDENCE"
        if key == "settlement_mapping"
        else "BLOCKED_ON_CALENDAR_EVIDENCE"
    )
    assert_blocked(evidence)


def test_unverified_calendar_comparison(evidence: dict[str, Any]) -> None:
    evidence["calendar_verification"]["aggregate"].update(
        status="BLOCKED_ON_CALENDAR_EVIDENCE", reasons=["missing_required_session"]
    )
    link(evidence)
    assert "BLOCKED_ON_CALENDAR_EVIDENCE" in verify(evidence).to_dict()["blockers"]


@pytest.mark.parametrize(
    "target,key",
    [
        ("settlement", "calendar_fingerprint"),
        ("lifecycle", "manifest_fingerprint"),
        ("lifecycle", "parent_integrated_fingerprint"),
        ("lifecycle_manifest", "parent_mapping_fingerprint"),
        ("lifecycle_manifest", "parent_gap_fingerprint"),
        ("parent_gaps", "parent_calendar_fingerprint"),
        ("lifecycle_reconciliation", "input_gap_fingerprint"),
    ],
)
def test_parent_links(evidence: dict[str, Any], target: str, key: str) -> None:
    evidence[target][key] = "f" * 64
    with pytest.raises(ContractError, match="link_mismatch"):
        verify(evidence)


def test_wrapper_tampering_and_unexpected_trusted_pin(evidence: dict[str, Any]) -> None:
    inputs, pins = inputs_and_pins(evidence)
    value = inputs["calendar"].to_dict()
    value["artifact"]["days"]["2025-07-01"] = "holiday"
    inputs["calendar"] = FrozenJSON.freeze(value)
    with pytest.raises(ContractError, match="artifact_fingerprint_mismatch"):
        rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint=pins.identifier)
    value["fingerprint"] = digest(value["artifact"])
    inputs["calendar"] = FrozenJSON.freeze(value)
    with pytest.raises(ContractError, match="independently_reviewed_artifact_mismatch"):
        rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint=pins.identifier)
    with pytest.raises(ContractError, match="independent_pins_mismatch"):
        rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint="f" * 64)


@pytest.mark.parametrize("name", list(rt.INPUT_VERSIONS))
def test_unsupported_versions(evidence: dict[str, Any], name: str) -> None:
    evidence[name]["version"] = "unsupported_v99"
    assert_blocked(evidence)


@pytest.mark.parametrize(
    "target,key", [("lifecycle", "security_calendar"), ("corporate_coverage", "blockers")]
)
def test_unknown_states_fail_closed(evidence: dict[str, Any], target: str, key: str) -> None:
    evidence[target][key] = ["FUTURE_BLOCKER"] if key == "blockers" else "FUTURE_STATUS"
    with pytest.raises(ContractError, match="unknown"):
        verify(evidence)


def test_missing_input_and_universe(evidence: dict[str, Any]) -> None:
    inputs, pins = inputs_and_pins(evidence)
    del inputs["corporate_coverage"]
    with pytest.raises(ContractError, match="inputs_missing"):
        rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint=pins.identifier)
    evidence["universe"]["tickers"].pop()
    assert_blocked(evidence)


def test_order_audit_path_time_independence(evidence: dict[str, Any]) -> None:
    inputs, pins = inputs_and_pins(evidence)
    first = rt.verify_runner_tail(inputs, pins=pins, expected_pins_fingerprint=pins.identifier)
    for name in list(inputs):
        value = inputs[name].to_dict()
        if "artifact" in value:
            value["audit"] = {"local_path": "/different/location", "retrieved_at": "2099-01-01"}
        inputs[name] = FrozenJSON.freeze(dict(reversed(list(value.items()))))
    second = rt.verify_runner_tail(
        dict(reversed(list(inputs.items()))), pins=pins, expected_pins_fingerprint=pins.identifier
    )
    assert first == second
    evidence["corporate_coverage"]["record_fingerprints"]["000001"] = "d" * 64
    assert verify(evidence).identifier != first.identifier


def test_locked_policy_fingerprints_unchanged(evidence: dict[str, Any]) -> None:
    result = verify(evidence).to_dict()
    assert (
        result["baseline_policy_fingerprint"]
        == "89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8"
    )
    assert (
        result["quality_policy_fingerprint"]
        == "c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c"
    )
    assert (
        result["cost_policy_fingerprint"]
        == "c479c0a7c453549e8cf071862385f5d0bb89c1e9c86ebd1b587f39d4442cafef"
    )
    evidence["approval"]["model_and_policy_pins"]["baseline_policy"] = "f" * 64
    with pytest.raises(ContractError, match="locked_policy_fingerprint_changed"):
        verify(evidence)


def test_cli_aggregate_and_exclusive_output(
    evidence: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs, pins = inputs_and_pins(evidence)
    (tmp_path / "pins.json").write_text(pins.payload_json)
    output = tmp_path / "result.json"
    args = [
        "verify",
        "--pins",
        str(tmp_path / "pins.json"),
        "--expected-pins-fingerprint",
        pins.identifier,
        "--output",
        str(output),
    ]
    for name, value in inputs.items():
        path = tmp_path / (name + ".json")
        path.write_text(value.payload_json)
        args.extend(["--artifact", f"{name}={path}"])
    monkeypatch.setattr(sys, "argv", args)
    cli.main()
    summary = json.loads(capsys.readouterr().out)
    wrapped = json.loads(output.read_text())
    assert wrapped["fingerprint"] == digest(wrapped["artifact"]) == summary["fingerprint"]
    assert output.read_text() == canonical(wrapped) + "\n"
    assert set(summary) == {
        "fingerprint",
        "runner_tail_integrated",
        "blockers",
        "warmup_session_count",
        "signal_session_count",
        "execution_tail_end",
        "final_settlement_date",
        "run_input_frozen",
        "backtest_authorized",
    }
    before = output.read_bytes()
    with pytest.raises(ContractError, match="output_must_be_new_file"):
        cli.main()
    assert output.read_bytes() == before


def test_offline_source_boundary() -> None:
    forbidden = {
        "fit",
        "predict",
        "predict_proba",
        "run",
        "load",
        "load_dotenv",
        "connect",
        "research",
        "allocate",
        "adjust_prices",
        "getenv",
        "urlopen",
        "post",
        "now",
        "today",
    }
    for module in (rt, cli):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert name not in forbidden
            if isinstance(node, ast.ImportFrom):
                if node.module == "donghak_stock_vision.backtest.baseline_runner":
                    assert [alias.name for alias in node.names] == ["VERSION"]
                assert not (node.module or "").startswith(
                    ("httpx", "requests", "pickle", "joblib", "sqlite3", "dotenv")
                )


@pytest.mark.parametrize(
    "defect", ["schema", "identity", "approval", "shape", "source", "exclusion"]
)
def test_malformed_and_unapproved_inputs(evidence: dict[str, Any], defect: str) -> None:
    if defect == "schema":
        evidence["universe"]["schema_version"] = 2
    elif defect == "identity":
        evidence["approval"]["universe_sha256"] = "d" * 64
    elif defect == "approval":
        evidence["approval"]["end"] = "2025-06-27"
    elif defect == "shape":
        del evidence["calendar_verification"]["ordering"]
    elif defect == "source":
        evidence["calendar"]["source_urls"] = ["https://unofficial.example/calendar"]
    else:
        evidence["calendar"]["research_exclusions_are_holidays"] = True
    assert_blocked(evidence)


@pytest.mark.parametrize("day", ["2024-06-14", *TAIL])
def test_missing_observed_session_without_lifecycle_proof_blocks(
    evidence: dict[str, Any], day: str
) -> None:
    evidence["calendar_verification"]["per_security"]["details"]["missing_sessions"]["000002"] = [
        day
    ]
    link(evidence)
    assert_blocked(evidence)


def test_unknown_corporate_security_state_blocks(evidence: dict[str, Any]) -> None:
    evidence["corporate_coverage"]["statuses"]["000001"] = "NEW_UNREVIEWED_STATE"
    with pytest.raises(ContractError, match="unknown_corporate_action_status"):
        verify(evidence)


def test_other_security_calendar_condition_cannot_be_cleared_by_lifecycle(
    evidence: dict[str, Any],
) -> None:
    evidence["calendar_verification"]["per_security"]["reasons"].append("unknown_condition")
    link(evidence)
    with pytest.raises(ContractError, match="unsupported_security_calendar_condition"):
        verify(evidence)


def test_unresolved_lifecycle_session_blocks(evidence: dict[str, Any]) -> None:
    reconciliation = evidence["lifecycle_reconciliation"]
    row = reconciliation["sessions"][0]
    row.update(
        verified=False,
        status="BLOCKED",
        reason="UNEXPLAINED_DATA_GAP",
        evidence_fingerprint=None,
        source=None,
        security_identity=None,
    )
    reconciliation.update(
        status="BLOCKED", explained=0, unexplained=1, unresolved_securities=["000001"]
    )
    evidence["lifecycle_manifest"].update(newly_explained=0, unexplained=1)
    evidence["lifecycle"]["security_calendar"] = "BLOCKED_ON_CALENDAR_EVIDENCE"
    link(evidence)
    assert "BLOCKED_ON_SECURITY_LIFECYCLE" in verify(evidence).to_dict()["blockers"]


def test_holiday_and_extraordinary_closure_do_not_become_anchors(evidence: dict[str, Any]) -> None:
    result = verify(evidence).to_dict()
    assert "2024-08-15" not in result["signal_sessions"]
    assert "2025-06-03" not in result["signal_sessions"]
    assert evidence["calendar"]["days"]["2025-03-21"] == "session"
    assert "2025-03-21" not in result["signal_sessions"]
    mapping = evidence["settlement"]["verification"]["details"]["mapping"]
    assert mapping["2024-08-14"] == "2024-08-19"


def test_cli_duplicate_input_and_missing_required_input_fail(
    evidence: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, pins = inputs_and_pins(evidence)
    pins_path, path, output = (
        tmp_path / name for name in ("pins.json", "calendar.json", "result.json")
    )
    pins_path.write_text(pins.payload_json)
    path.write_text(inputs["calendar"].payload_json)
    args = [
        "verify",
        "--pins",
        str(pins_path),
        "--expected-pins-fingerprint",
        pins.identifier,
        "--output",
        str(output),
        "--artifact",
        f"calendar={path}",
    ]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(ContractError, match="inputs_missing_or_unknown"):
        cli.main()
    monkeypatch.setattr(sys, "argv", [*args, "--artifact", f"calendar={path}"])
    with pytest.raises(ContractError, match="invalid_or_duplicate_artifact_argument"):
        cli.main()
    assert not output.exists()


def test_calendar_scope_must_cover_warmup(evidence: dict[str, Any]) -> None:
    evidence["calendar"]["effective_start"] = "2024-07-01"
    link(evidence)
    assert_blocked(evidence)


def test_noninteger_coverage_count_is_malformed(evidence: dict[str, Any]) -> None:
    evidence["corporate_coverage"]["complete_corporate_coverage_count"] = 993.0
    with pytest.raises(ContractError, match="invalid_coverage_count"):
        verify(evidence)
