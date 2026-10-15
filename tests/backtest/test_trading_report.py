"""Factual synthetic reporting, without changing decision or accounting contracts."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.performance import calculate_performance
from donghak_stock_vision.backtest.report import format_report
from donghak_stock_vision.backtest.runner import BacktestRunner
from donghak_stock_vision.backtest.trading_report import (
    ReportedBacktest,
    duration,
    report_backtest,
    session_duration,
    validate_report,
)
from donghak_stock_vision.strategy.contracts import DecisionPolicy
from tests.backtest.test_performance import valuation_policy
from tests.backtest.test_position_history import round_trip
from tests.backtest.test_runner import configure, replan, runner


@pytest.fixture(scope="module")
def example() -> tuple[BacktestRunner, ReportedBacktest]:
    r = round_trip()
    return r, report_backtest(r, valuation_policy())


def test_economic_evidence_and_replay_unchanged(
    example: tuple[BacktestRunner, ReportedBacktest],
) -> None:
    r, result = example
    before = (r.tape.identifier, tuple(a.bundle.identifier for a in r.analyses))
    assert r.run() == result.run
    assert calculate_performance(result.run, r.tape, valuation_policy()) == result.performance
    validate_report(result.details, r, valuation_policy())
    assert (r.tape.identifier, tuple(a.bundle.identifier for a in r.analyses)) == before
    assert FrozenJSON(result.details.payload_json) == result.details
    d, p = result.details.to_dict(), result.performance.to_dict()
    assert d["totals"]["realized_pnl"] == p["realized_pnl"] == "170.55"
    assert d["totals"]["unrealized_pnl"] == p["unrealized_pnl"] == "-10.82"
    assert d["data_origin"] == "synthetic" and d["usage_restriction"] == "synthetic_test_only"
    assert d["sources"]["run_hash"] == result.run.identifier
    assert d["sources"]["performance_hash"] == result.performance.identifier


def test_buy_sell_fill_linkage_and_distinct_times(
    example: tuple[BacktestRunner, ReportedBacktest],
) -> None:
    r, result = example
    timeline = result.details.to_dict()["timeline"]
    assert [x["action"] for x in timeline] == ["BUY", "SELL", "BUY"]
    for row, analysis in zip(timeline, r.analyses, strict=True):
        assert row["analysis_id"] == analysis.bundle.to_dict()["analysis_id"]
        assert row["model_id"] == analysis.bundle.to_dict()["model_id"]
        assert row["snapshot_id"] == analysis.bundle.to_dict()["snapshot_id"]
        assert row["admission"]["status"] == "admitted"
        assert row["reservation"]["status"] == "accepted"
        fill = row["executions"][0]["fill"]
        assert fill["order_id"] == row["order_id"]
        assert fill["quantity"] == row["requested_quantity"]
        assert datetime.fromisoformat(fill["fill_at"]) > datetime.fromisoformat(
            row["decision_cutoff"]
        )
        assert fill["fill_known_at"] is not None and fill["source_event_id"]
        assert fill["fee_krw"] is not None and fill["slippage_krw"] is not None
    assert timeline[0]["conditions"]["entry"]["code"] == "entry_condition"
    assert timeline[1]["conditions"]["exit"]["code"] == "exit_condition"


def test_positions_complete_exit_reentry_and_terminal_open(
    example: tuple[BacktestRunner, ReportedBacktest],
) -> None:
    _, result = example
    d, p = result.details.to_dict(), result.performance.to_dict()
    first, second = d["positions"]
    assert first["status"] == "confirmed_full_exit" and first["quantity"] == 0
    assert first["acquisition"]["fill"]["quantity"] == first["sales"][0]["quantity"] == 10
    assert first["holding_duration"]["calendar_days"] == 2
    assert first["realized_net_pnl"] == p["trade_episodes"][0]["realized_pnl"]
    assert second["status"] == "still_open" and second["quantity"] == 10
    assert second["previous_completed_position_id"] == first["position_id"]
    assert second["exited_at"] is None
    assert second["holding_duration"]["reason"] == "exit_not_confirmed"
    assert second["unrealized_pnl"] == p["unrealized_pnl"]
    assert d["account_timeline"] == p["equity_curve"]


@pytest.mark.parametrize("held", [False, True])
def test_wait_hold_context_and_threshold_reasons(held: bool) -> None:
    r = configure(runner("SELL" if held else "BUY"), history_mode="verified_execution")
    policy = r.decision_policy.to_dict()
    policy["thresholds"] = {"buy": "0.99", "sell": "0.99"}
    row = report_backtest(
        replace(r, decision_policy=DecisionPolicy.from_dict(policy)), valuation_policy()
    ).details.to_dict()["timeline"][0]
    assert row["action"] == ("HOLD" if held else "WAIT")
    assert row["conditions"]["exit" if held else "entry"]["code"] == (
        "exit_threshold_not_met" if held else "entry_threshold_not_met"
    )
    assert row["order_id"] is None and not row["executions"]


def test_context_mismatch_and_absent_exit_are_not_profit_taking() -> None:
    r = round_trip()
    policy = r.decision_policy.to_dict()
    policy["thresholds"]["sell"] = "0.99"
    rows = report_backtest(
        replace(r, decision_policy=DecisionPolicy.from_dict(policy)), valuation_policy()
    ).details.to_dict()["timeline"]
    assert rows[-1]["action"] == "HOLD"
    assert rows[-1]["conditions"]["entry"]["code"] == "existing_position"
    assert rows[-1]["conditions"]["exit"]["code"] == "exit_context_mismatch"
    assert "no profit-taking rule" in rows[-1]["conditions"]["exit"]["text"]


def test_insufficient_cash_and_admission_rejection_are_distinct() -> None:
    r = round_trip()
    policy = r.decision_policy.to_dict()
    policy["sizing"]["buy_budget_krw"] = "20000"
    blocked = report_backtest(
        replace(r, decision_policy=DecisionPolicy.from_dict(policy)), valuation_policy()
    ).details.to_dict()["timeline"][0]
    assert blocked["decision_status"] == "blocked"
    assert blocked["conditions"]["entry"]["code"] == "insufficient_cash"
    config = r.config.to_dict()
    config["decision_steps"][0]["admission_policy"]["max_order_quantity"] = 1
    rejected = report_backtest(
        replace(r, config=FrozenJSON.freeze(config)), valuation_policy()
    ).details.to_dict()["timeline"][0]
    assert rejected["action"] == "BUY"
    assert rejected["admission"]["status"] == "rejected"
    assert rejected["reservation"]["status"] == "not_attempted"
    assert rejected["order_id"] is None


def test_pending_rejection_does_not_invent_exit_or_fill() -> None:
    r = round_trip()
    policy = r.execution_policy.to_dict()
    policy["slippage_rate"] = "0.2"
    result = report_backtest(
        replace(r, execution_policy=FrozenJSON.freeze(policy)), valuation_policy()
    )
    d = result.details.to_dict()
    assert result.performance.to_dict()["fill_count"] == 0
    assert d["timeline"][1]["conditions"]["entry"]["code"] == "pending_order"
    assert d["terminal_orders"][0]["status"] == "open"
    assert d["terminal_orders"][0]["executions"][0]["status"] == "rejected"
    assert d["terminal_orders"][0]["executions"][0]["reason"] == "reservation_shortfall"
    assert d["positions"] == []


def test_no_later_event_leaves_terminal_reservation_open() -> None:
    r = runner()
    r = configure(
        r, history_mode="verified_execution", end_at=r.tape.events[0].to_dict()["available_at"]
    )
    result = report_backtest(r, valuation_policy())
    order = result.details.to_dict()["terminal_orders"][0]
    assert order["status"] == "open" and order["remaining_quantity"] == 10
    assert order["outcome_reason"]["code"] == "no_later_market_event_in_run"
    assert order["executions"] == []
    assert result.details.to_dict()["timeline"][0]["executions"] == []


def test_partial_exit_uses_existing_realized_and_unrealized_values() -> None:
    r = round_trip()
    policy = r.decision_policy.to_dict()
    policy["sizing"].update(sell_mode="partial", partial_kind="quantity", partial_value=5)
    result = report_backtest(
        replace(r, decision_policy=DecisionPolicy.from_dict(policy)), valuation_policy()
    )
    pos = result.details.to_dict()["positions"][0]
    p = result.performance.to_dict()
    assert pos["status"] == "partial_exit" and pos["quantity"] == 5
    assert pos["exited_at"] is None and len(pos["sales"]) == 1
    assert pos["realized_net_pnl"] == p["open_episode_realized_pnl"] == "85.28"
    assert pos["unrealized_pnl"] == p["unrealized_pnl"] == "-5.41"


def test_initial_acquisition_time_and_stale_marks_remain_unknown() -> None:
    r = configure(runner("SELL"), history_mode="verified_execution")
    initial = report_backtest(r, valuation_policy()).details.to_dict()["positions"][0]
    assert initial["acquired_at"] is None
    assert initial["holding_duration"]["reason"] == "acquisition_time_unknown"
    assert initial["acquisition"]["fill"] is None
    result = report_backtest(round_trip(), valuation_policy(max_age_seconds=0))
    last = result.details.to_dict()["positions"][-1]
    assert last["unrealized_pnl"] is None
    assert last["unavailable"]["unrealized_pnl"] == "stale_mark"
    assert duration(None, "2026-01-01T00:00:00Z")["elapsed_microseconds"] is None


def test_unreleased_analysis_is_not_exposed_as_a_model_explanation() -> None:
    r = configure(runner(), history_mode="verified_execution")
    plan = r.tape.release_plan.to_dict()
    plan["analysis_releases"][0]["available_at"] = "2026-01-06T00:00:00Z"
    result = report_backtest(replan(r, plan), valuation_policy()).details.to_dict()["timeline"][0]
    assert result["action"] is None and result["score"] is None
    assert result["feature_contributions"] is None
    assert result["reasons"][0]["code"] == "analysis_not_released"
    assert result["unavailable"]["decision_id"]


@pytest.mark.parametrize("field", ["totals", "timeline", "sources"])
def test_tampered_report_rejected(
    example: tuple[BacktestRunner, ReportedBacktest], field: str
) -> None:
    r, result = example
    d = result.details.to_dict()
    del d[field]
    with pytest.raises(ValueError, match="report_projection_conflict"):
        validate_report(FrozenJSON.freeze(d), r, valuation_policy())


def test_missing_source_is_not_replaced_by_report_fields(
    example: tuple[BacktestRunner, ReportedBacktest],
) -> None:
    r, result = example
    with pytest.raises(ValueError, match="analysis_missing"):
        validate_report(result.details, replace(r, analyses=()), valuation_policy())
    other = result.details.to_dict()
    other["sources"]["performance_hash"] = "0" * 64
    with pytest.raises(ValueError, match="report_performance_mismatch"):
        format_report(result.performance, FrozenJSON.freeze(other))


def test_session_duration_requires_explicit_calendar_contract() -> None:
    start, end = "2026-01-06T00:00:00Z", "2026-01-08T00:00:00Z"
    assert session_duration(start, end, [], {})["count"] is None
    market = {
        "calendar_verified": True,
        "calendar_coverage": {"start": "2026-01-05", "end": "2026-01-09"},
        "sessions": ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"],
    }
    decision = {"decision_id": "synthetic-calendar-fixture", "input": {"market": market}}
    result = session_duration(start, end, [decision], {})
    assert result["count"] == 2 and result["source"]["market_hash"]
    assert session_duration(start, end, [decision], {"research_calendar": {}})["count"] is None


def test_actual_public_feature_contributions_render_without_causal_claims(tmp_path: Path) -> None:
    from tests.backtest.test_public_inference import scored_runner

    result = report_backtest(scored_runner(tmp_path), valuation_policy())
    first = result.details.to_dict()["timeline"][0]
    assert len(first["feature_contributions"]) == 8
    assert first["linear_intercept"] is not None
    text = format_report(result.performance, result.details)
    assert "feature=" in text and "contribution=" in text
    assert "not economic causation" in text
    assert "SYNTHETIC HISTORICAL" in text
    (tmp_path / "example-report.txt").write_text(text)
    (tmp_path / "example-report.json").write_text(result.details.payload_json)


def test_historical_contract_labels_and_unavailable_opportunity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Existing fake-price/controlled-analysis historical contract fixture, not real performance.
    from tests.backtest.test_historical_service import change, execute, setup_request

    request = setup_request(tmp_path, monkeypatch)
    request = change(request, decision_dates=["2026-01-09", "2026-01-10", "2026-01-13"])
    result = execute(request, tmp_path).document.to_dict()
    d = result["detailed_report"]
    assert d["data_origin"] == "real" and d["usage_restriction"] == "research_only"
    assert [row["trading_date"] for row in d["timeline"]] == [
        "2026-01-09",
        "2026-01-10",
        "2026-01-13",
    ]
    missing = d["timeline"][1]
    assert missing["decision_cutoff"] is None and missing["action"] is None
    assert missing["opportunity_time_basis"] == "historical_input_release_assumption"
    assert missing["reasons"][0]["code"] == "missing_decision_bar"
    assert missing["source"]["historical_input_document_hash"]
    assert FrozenJSON.freeze(d).identifier == result["detailed_report_hash"]
    assert result["performance"]["fill_count"] == 2
    assert "Detailed decision timeline" in result["report"]


def test_missing_timestamp_in_source_evidence_fails_existing_validation(
    example: tuple[BacktestRunner, ReportedBacktest],
) -> None:
    from donghak_stock_vision.backtest.availability import FrozenAnalysis

    r, _ = example
    original = r.analyses[0]
    corrupted = original.bundle.to_dict()
    del corrupted["signal"]["anchor_at"]
    with pytest.raises(ValueError, match="artifact_checksum_mismatch"):
        FrozenAnalysis(original.manifest, original.release_plan, FrozenJSON.freeze(corrupted))


def test_real_calendar_is_not_qualified_by_a_virtual_boolean() -> None:
    assert (
        session_duration(
            "2026-01-05T00:00:00Z", "2026-01-08T00:00:00Z", [], {}, data_origin="real"
        )["reason"]
        == "independent_calendar_attestation_unavailable"
    )
