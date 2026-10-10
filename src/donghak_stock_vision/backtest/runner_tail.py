"""Pinned offline runner-window evidence; never execute or authorize a backtest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from donghak_stock_vision.backtest.baseline_costs import BaselineCostPolicy
from donghak_stock_vision.backtest.baseline_policy import BaselineBacktestPolicy, baseline_rules
from donghak_stock_vision.backtest.baseline_runner import VERSION as RUNNER_VERSION
from donghak_stock_vision.backtest.corporate_action_coverage import VERIFIED_STATUSES
from donghak_stock_vision.backtest.dart_evidence import (
    security_code_valid,
    source_valid,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.evidence_package import (
    HOLDING_SESSIONS,
    PRECEDING_CONTEXT_SESSIONS,
    AnchorRange,
    derive_boundaries,
)
from donghak_stock_vision.backtest.security_lifecycle import LifecycleReason
from donghak_stock_vision.backtest.validation import ContractError, fields, require, sha
from donghak_stock_vision.data.learning import digest

VERSION = "baseline_runner_tail_v1"
PINS_VERSION = "baseline_runner_tail_pins_v1"
# Existing KIND calendar and KRX settlement-rule publishers; not DART filing hosts.
CALENDAR_SOURCE_HOSTS = {"kind.krx.co.kr", "global.krx.co.kr"}
INPUT_VERSIONS = {
    "approval": "baseline_run_approval_v1",
    "universe": None,  # Existing frozen universe is unwrapped and unversioned.
    "calendar": "kind_cash_calendar_v1",
    "calendar_verification": "official_calendar_comparison_v1",
    "boundaries": None,  # Existing exact-boundaries artifact is unversioned.
    "settlement": None,  # Existing settlement-calendar artifact is unversioned.
    "lifecycle": "baseline_security_lifecycle_v1",
    "lifecycle_manifest": "baseline_security_lifecycle_v1",
    "lifecycle_reconciliation": "security_lifecycle_reconciliation_v1",
    "parent_gaps": "missing_session_review_v1",
    "corporate_coverage": "corporate_action_final_coverage_v1",
}
CA_STATUSES = VERIFIED_STATUSES | {
    "MISSING_COVERAGE_RECORD",
    "BLOCKED_ON_IDENTITY_MAPPING",
    "BLOCKED_ON_SOURCE_COVERAGE",
    "BLOCKED_ON_EFFECTIVE_DATE",
}


def _day(value: str) -> date:
    result = date.fromisoformat(value)
    require(result.isoformat() == value, "canonical_date_required")
    return result


def _state(value: Any, blocked: str) -> bool:
    require(value in ("VERIFIED", blocked), "unknown_evidence_status")
    return bool(value == "VERIFIED")


def _check_status(value: dict[str, Any], category: str) -> bool:
    require(value["category"] == category, "evidence_category_mismatch")
    verified = _state(value["status"], f"BLOCKED_ON_{category}")
    require(type(value["reasons"]) is list, "evidence_reasons_required")
    require(verified == (not value["reasons"]), "inconsistent_evidence_status")
    return verified


@dataclass(frozen=True)
class RunnerTailWindow:
    """Session partitions only. Methods do not create signals, intents or fills."""

    warmup: tuple[date, ...]
    signals: tuple[date, ...]
    execution: tuple[date, ...]
    settlement_only: tuple[date, ...]

    def __post_init__(self) -> None:
        require(len(self.warmup) == PRECEDING_CONTEXT_SESSIONS, "eleven_warmup_sessions_required")
        require(len(self.execution) == HOLDING_SESSIONS, "five_execution_sessions_required")
        require(bool(self.signals) and len(self.settlement_only) == 2, "complete_window_required")
        all_days = self.warmup + self.signals + self.execution + self.settlement_only
        require(all(type(day) is date for day in all_days), "date_required")
        require(all_days == tuple(sorted(set(all_days))), "nonincreasing_or_overlapping_window")

    def permits_signal(self, day: date) -> bool:
        return day in self.signals

    def entry_candidate(self, anchor: date) -> date:
        """Exactly one next session; this is a temporal candidate, never a fill approval."""
        require(self.permits_signal(anchor), "outside_signal_anchor_window")
        sessions = self.signals + self.execution
        return sessions[sessions.index(anchor) + 1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "warmup_session_count": len(self.warmup),
            "warmup_first": self.warmup[0].isoformat(),
            "warmup_last": self.warmup[-1].isoformat(),
            "warmup_sessions": [day.isoformat() for day in self.warmup],
            "signal_start": self.signals[0].isoformat(),
            "signal_end": self.signals[-1].isoformat(),
            "signal_session_count": len(self.signals),
            "signal_sessions": [day.isoformat() for day in self.signals],
            "execution_tail_start": self.execution[0].isoformat(),
            "execution_tail_end": self.execution[-1].isoformat(),
            "execution_tail_sessions": [day.isoformat() for day in self.execution],
            "settlement_only_sessions": [day.isoformat() for day in self.settlement_only],
            "settlement_tail_end": self.settlement_only[-1].isoformat(),
            "final_possible_execution_date": self.execution[-1].isoformat(),
            "final_settlement_date": self.settlement_only[-1].isoformat(),
            "last_anchor_entry_candidate": self.entry_candidate(self.signals[-1]).isoformat(),
            "entry_retry_authorized": False,
            "warmup_generates_signals": False,
            "tail_generates_signals": False,
        }


def _load(
    inputs: Mapping[str, FrozenJSON],
    pins: FrozenJSON,
    expected_pins_fingerprint: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    require(pins.identifier == sha(expected_pins_fingerprint), "independent_pins_mismatch")
    p = fields(
        pins.to_dict(), "version artifacts parent_integrated_fingerprint parent_mapping_fingerprint"
    )
    require(p["version"] == PINS_VERSION, "unsupported_runner_tail_pins_version")
    require(set(inputs) == set(INPUT_VERSIONS), "runner_tail_inputs_missing_or_unknown")
    require(set(p["artifacts"]) == set(INPUT_VERSIONS), "runner_tail_pins_missing_or_unknown")
    sha(p["parent_integrated_fingerprint"])
    sha(p["parent_mapping_fingerprint"])
    data = {}
    for name, version in INPUT_VERSIONS.items():
        expected = sha(p["artifacts"][name])
        value = inputs[name].to_dict()
        if name not in {"approval", "universe"}:
            require(type(value.get("artifact")) is dict, "wrapped_evidence_required")
            require(
                value.get("fingerprint") == digest(value["artifact"]),
                "artifact_fingerprint_mismatch",
            )
            value = value["artifact"]
        require(digest(value) == expected, "independently_reviewed_artifact_mismatch")
        if version is None:
            require("version" not in value, "unsupported_legacy_artifact_version")
        else:
            require(value.get("version") == version, "unsupported_evidence_version")
        data[name] = value
    return data, p


def _corporate(
    data: dict[str, Any], universe: tuple[str, ...], window: RunnerTailWindow
) -> list[str]:
    for key in (
        "universe_count",
        "reviewed_security_count",
        "historical_identity_verified_count",
        "complete_corporate_coverage_count",
        "missing_security_count",
        "blocked_security_count",
        "verified_economic_event_count",
    ):
        require(type(data[key]) is int and data[key] >= 0, "invalid_coverage_count")
    require(set(data["statuses"]) == set(universe), "corporate_action_universe_mismatch")
    statuses = data["statuses"]
    require(
        all(state in CA_STATUSES for state in statuses.values()), "unknown_corporate_action_status"
    )
    require(
        set(data["blockers"])
        <= {
            "BLOCKED_ON_HISTORICAL_IDENTITY",
            "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
        },
        "unknown_corporate_action_blocker",
    )
    for key in (
        "corporate_action_gate_verified",
        "prices_adjusted",
        "prediction_or_backtest_invoked",
    ):
        require(type(data[key]) is bool, "corporate_action_boolean_required")
    missing = sorted(t for t, state in statuses.items() if state == "MISSING_COVERAGE_RECORD")
    blocked = sorted(t for t, state in statuses.items() if state not in VERIFIED_STATUSES)
    require(
        data["missing_securities"] == missing and data["blocked_securities"] == blocked,
        "corporate_action_inventory_mismatch",
    )
    require(
        set(data["record_fingerprints"]) == set(universe) - set(missing),
        "corporate_action_record_inventory_mismatch",
    )
    for pin in data["record_fingerprints"].values():
        sha(pin)
    complete = (
        data["corporate_action_gate_verified"] is True
        and not data["blockers"]
        and not blocked
        and data["universe_count"] == len(universe)
        and data["reviewed_security_count"] == len(universe)
        and data["complete_corporate_coverage_count"] == len(universe)
        and data["missing_security_count"] == 0
        and data["blocked_security_count"] == 0
        and _day(data["required_start"]) <= window.warmup[0]
        and _day(data["required_end"]) >= window.execution[-1]
        and data["prices_adjusted"] is False
        and data["prediction_or_backtest_invoked"] is False
    )
    reasons = [] if complete else ["BLOCKED_ON_CORPORATE_ACTION_EVIDENCE"]
    if data["historical_identity_verified_count"] != len(universe) or any(
        state in {"BLOCKED_ON_IDENTITY_MAPPING", "MISSING_COVERAGE_RECORD"}
        for state in statuses.values()
    ):
        reasons.append("BLOCKED_ON_HISTORICAL_IDENTITY")
    return reasons


def _lifecycle(
    data: dict[str, dict[str, Any]], pins: dict[str, Any], universe: tuple[str, ...]
) -> bool:
    integrated, manifest, reconciliation, parent = (
        data[key]
        for key in ("lifecycle", "lifecycle_manifest", "lifecycle_reconciliation", "parent_gaps")
    )
    pin = pins["artifacts"]
    require(
        integrated["manifest_fingerprint"] == pin["lifecycle_manifest"],
        "lifecycle_manifest_link_mismatch",
    )
    require(
        integrated["parent_integrated_fingerprint"]
        == manifest["parent_integrated_fingerprint"]
        == pins["parent_integrated_fingerprint"],
        "lifecycle_parent_link_mismatch",
    )
    require(
        manifest["parent_mapping_fingerprint"] == pins["parent_mapping_fingerprint"],
        "lifecycle_mapping_link_mismatch",
    )
    require(manifest["parent_gap_fingerprint"] == pin["parent_gaps"], "lifecycle_gap_link_mismatch")
    require(
        manifest["artifact_fingerprints"]["missing-session-reconciliation"]
        == pin["lifecycle_reconciliation"],
        "lifecycle_reconciliation_link_mismatch",
    )
    require(
        parent["parent_calendar_fingerprint"] == pin["calendar_verification"],
        "gap_calendar_link_mismatch",
    )
    require(
        integrated["corporate_action"] == "BLOCKED_ON_CORPORATE_ACTION_EVIDENCE",
        "lifecycle_cannot_approve_corporate_actions",
    )
    statuses = [
        _state(integrated[key], blocked)
        for key, blocked in (
            ("exchange_calendar", "BLOCKED_ON_CALENDAR_EVIDENCE"),
            ("security_calendar", "BLOCKED_ON_CALENDAR_EVIDENCE"),
            ("settlement_mapping", "BLOCKED_ON_SETTLEMENT_EVIDENCE"),
        )
    ]
    require(reconciliation["status"] in ("VERIFIED", "BLOCKED"), "unknown_lifecycle_status")
    official_missing = data["calendar_verification"]["per_security"]["details"]["missing_sessions"]
    parent_missing, unresolved = {}, {}
    explained_count = 0
    for row in parent["securities"]:
        ticker = row["ticker"]
        require(ticker in universe and ticker not in parent_missing, "invalid_lifecycle_universe")
        explained, absent = row["explained"], row["unexplained"]
        require(
            len(set(absent)) == len(absent) and not set(explained).intersection(absent),
            "duplicate_gap_explanation",
        )
        parent_missing[ticker] = sorted([*explained, *absent])
        if absent:
            unresolved[ticker] = sorted(absent)
        for claim in explained.values():
            require(source_valid(claim["source"]), "official_lifecycle_source_required")
            require(claim["reason"] in tuple(LifecycleReason), "unknown_lifecycle_reason")
        explained_count += len(explained)
        require(row["bar_absence_used_as_lifecycle_evidence"] is False, "absence_is_not_evidence")
    require(parent_missing == official_missing, "lifecycle_gap_inventory_mismatch")
    require(
        reconciliation["input_gap_fingerprint"] == digest(unresolved),
        "reconciliation_input_link_mismatch",
    )
    expected = {(t, d) for t, days in unresolved.items() for d in days}
    seen: set[tuple[str, str]] = set()
    verified = 0
    for row in reconciliation["sessions"]:
        pair = row["ticker"], row["date"]
        require(pair in expected and pair not in seen, "unexpected_lifecycle_session")
        seen.add(pair)
        require(row["status"] in ("VERIFIED", "BLOCKED"), "unknown_lifecycle_session_status")
        require(
            type(row["verified"]) is bool and row["verified"] == (row["status"] == "VERIFIED"),
            "inconsistent_lifecycle_session",
        )
        require(
            row["reason"] in (*tuple(LifecycleReason), "UNEXPLAINED_DATA_GAP"),
            "unknown_lifecycle_reason",
        )
        if row["verified"]:
            require(
                row["reason"] != "UNEXPLAINED_DATA_GAP" and row["blocker"] is None,
                "unexplained_lifecycle_session",
            )
            require(
                source_valid(row["source"]) and bool(row["security_identity"]),
                "official_lifecycle_source_required",
            )
            sha(row["evidence_fingerprint"])
            verified += 1
    require(seen == expected, "missing_lifecycle_reconciliation_session")
    require(
        parent["explained"] == explained_count
        and parent["unresolved"] == len(expected)
        and parent["total_missing"] == explained_count + len(expected),
        "parent_gap_count_mismatch",
    )
    require(
        reconciliation["total"] == len(expected)
        and reconciliation["explained"] == verified
        and reconciliation["unexplained"] == len(expected) - verified,
        "reconciliation_count_mismatch",
    )
    require(
        manifest["starting_unexplained_sessions"] == len(expected)
        and manifest["starting_unresolved_securities"] == len(unresolved)
        and manifest["newly_explained"] == verified
        and manifest["unexplained"] == len(expected) - verified,
        "lifecycle_manifest_count_mismatch",
    )
    return (
        all(statuses)
        and reconciliation["status"] == "VERIFIED"
        and verified == len(expected)
        and reconciliation["unresolved_securities"] == []
        and integrated["policy_override"] is False
        and integrated["prediction_or_backtest_invoked"] is False
        and reconciliation["bar_absence_used_as_evidence"] is False
        and reconciliation["price_adjustment_performed"] is False
    )


def _verify(inputs: Mapping[str, FrozenJSON], pins: FrozenJSON, expected_pin: str) -> FrozenJSON:
    data, p = _load(inputs, pins, expected_pin)
    approval, calendar, comparison = (
        data[key] for key in ("approval", "calendar", "calendar_verification")
    )
    anchors = AnchorRange(_day(approval["start"]), _day(approval["end"]))
    require(not anchors.approval().reasons, "unapproved_signal_range")
    require(
        type(data["universe"]["schema_version"]) is int
        and data["universe"]["schema_version"] == 1
        and data["universe"]["count"] == 993,
        "unsupported_frozen_universe_schema",
    )
    universe = tuple(data["universe"]["tickers"])
    require(
        len(universe) == 993
        and universe == tuple(sorted(set(universe)))
        and all(security_code_valid(t) for t in universe),
        "locked_993_universe_required",
    )
    require(digest(data["universe"]) == approval["universe_sha256"], "locked_universe_mismatch")
    sha(approval["market_sha256"])
    baseline = BaselineBacktestPolicy(BaselineCostPolicy(Decimal(1), "down_per_component_KRW"))
    locked = approval["model_and_policy_pins"]
    require(
        locked["baseline_policy"] == baseline.fingerprint
        and locked["quality_policy"] == baseline.quality_policy.fingerprint
        and locked["cost_policy"] == baseline.require_costs().fingerprint
        and locked["runner_version"] == RUNNER_VERSION,
        "locked_policy_fingerprint_changed",
    )
    require(
        baseline_rules()["maximum_holding_sessions"] == HOLDING_SESSIONS, "holding_contract_changed"
    )
    exclusions = tuple(_day(day) for day in approval["excluded_dates"])
    require(exclusions == tuple(sorted(set(exclusions))), "canonical_exclusions_required")
    pin = p["artifacts"]
    require(
        bool(calendar["source_urls"])
        and all(urlsplit(url).hostname in CALENDAR_SOURCE_HOSTS for url in calendar["source_urls"])
        and urlsplit(calendar["rule_source"]["url"]).hostname == "global.krx.co.kr",
        "official_calendar_and_rule_sources_required",
    )
    sha(calendar["rule_source"]["raw_sha256"])
    require(calendar["research_exclusions_are_holidays"] is False, "exclusion_is_not_holiday")
    frozen_calendar = FrozenJSON.freeze(calendar)
    derived = derive_boundaries(anchors, exclusions, frozen_calendar, pin["calendar"]).to_dict()
    blockers: list[str] = []
    window = None
    if derived["status"] != "VERIFIED":
        blockers.append("BLOCKED_ON_CALENDAR_EVIDENCE")
    else:
        require(
            data["boundaries"]["status"] in ("VERIFIED", "BLOCKED_ON_CALENDAR_EVIDENCE"),
            "unknown_boundary_status",
        )
        if derived != data["boundaries"]:
            blockers.append("BLOCKED_ON_RUN_WINDOW")
        signals = tuple(
            _day(day)
            for day, state in sorted(calendar["days"].items())
            if state == "session"
            and anchors.start.isoformat() <= day <= anchors.end.isoformat()
            and _day(day) not in exclusions
        )
        window = RunnerTailWindow(
            tuple(map(_day, derived["warmup_sessions"])),
            signals,
            tuple(map(_day, derived["execution_tail"])),
            tuple(map(_day, derived["settlement_tail"])),
        )
        calendar_ok = _check_status(comparison["aggregate"], "CALENDAR_EVIDENCE")
        _check_status(comparison["per_security"], "CALENDAR_EVIDENCE")
        require(
            set(comparison["per_security"]["reasons"]) <= {"unexplained_missing_ticker_sessions"},
            "unsupported_security_calendar_condition",
        )
        for layer in ("aggregate", "per_security"):
            require(
                comparison[layer]["details"]["calendar_pin"] == pin["calendar"],
                "comparison_calendar_link_mismatch",
            )
            require(
                comparison[layer]["details"]["exclusions"] == approval["excluded_dates"],
                "research_exclusion_mismatch",
            )
        required_sessions = window.warmup + window.signals + window.execution
        required_dates = {day.isoformat() for day in required_sessions}
        for ticker, missing in comparison["per_security"]["details"]["missing_sessions"].items():
            require(
                ticker in universe
                and missing == sorted(set(missing))
                and set(missing) <= required_dates,
                "invalid_security_gap_scope",
            )
        ordering = comparison["ordering"]
        calendar_ok = calendar_ok and (
            comparison["start"] == window.warmup[0].isoformat()
            and comparison["end"] == window.execution[-1].isoformat()
            and _day(calendar["effective_start"]) <= window.warmup[0]
            and _day(calendar["effective_end"]) >= window.settlement_only[-1]
            and ordering["date_count"] == len(required_sessions)
            and all(
                ordering[key] == 0
                for key in (
                    "duplicates",
                    "nonincreasing",
                    "malformed",
                    "out_of_range",
                    "unexpected_ticker",
                    "metadata_mismatch",
                )
            )
            and comparison["aggregate"]["details"]["missing_sessions"] == {}
            and comparison["database"]["integrity"] == ["ok"]
        )
        require(
            comparison["database"]["sha256_before"]
            == comparison["database"]["sha256_after"]
            == approval["market_sha256"],
            "source_dataset_mismatch",
        )
        if not calendar_ok:
            blockers.append("BLOCKED_ON_CALENDAR_EVIDENCE")
        settlement = data["settlement"]
        fields(
            settlement,
            "calendar_fingerprint official_rule_source verification possible_trade_sessions",
        )
        require(
            settlement["calendar_fingerprint"]
            == pin["calendar"]
            == settlement["verification"]["details"]["calendar_pin"],
            "settlement_calendar_link_mismatch",
        )
        require(
            settlement["official_rule_source"] == calendar["rule_source"],
            "settlement_rule_link_mismatch",
        )
        settlement_ok = _check_status(settlement["verification"], "SETTLEMENT_EVIDENCE")
        if not (
            settlement_ok
            and settlement["verification"]["details"]["mapping"] == derived["settlement_mapping"]
            and settlement["possible_trade_sessions"] == len(derived["settlement_mapping"])
        ):
            blockers.append("BLOCKED_ON_SETTLEMENT_EVIDENCE")
        if not _lifecycle(data, p, universe):
            blockers.append("BLOCKED_ON_SECURITY_LIFECYCLE")
        blockers.extend(_corporate(data["corporate_coverage"], universe, window))
    result = (
        window.to_dict()
        if window
        else {
            "warmup_session_count": None,
            "warmup_first": None,
            "warmup_last": None,
            "warmup_sessions": [],
            "signal_start": approval["start"],
            "signal_end": approval["end"],
            "signal_session_count": None,
            "signal_sessions": [],
            "execution_tail_start": None,
            "execution_tail_end": None,
            "execution_tail_sessions": [],
            "settlement_only_sessions": [],
            "settlement_tail_end": None,
            "final_possible_execution_date": None,
            "final_settlement_date": None,
            "last_anchor_entry_candidate": None,
            "entry_retry_authorized": False,
            "warmup_generates_signals": False,
            "tail_generates_signals": False,
        }
    )
    return FrozenJSON.freeze(
        {
            **result,
            "version": VERSION,
            "pins_fingerprint": pins.identifier,
            "approval_fingerprint": pin["approval"],
            "universe_fingerprint": approval["universe_sha256"],
            "universe_count": len(universe),
            "market_db_fingerprint": approval["market_sha256"],
            "calendar_fingerprint": pin["calendar"],
            "settlement_fingerprint": pin["settlement"],
            "lifecycle_fingerprint": pin["lifecycle"],
            "corporate_action_coverage_fingerprint": pin["corporate_coverage"],
            "baseline_policy_fingerprint": baseline.fingerprint,
            "quality_policy_fingerprint": baseline.quality_policy.fingerprint,
            "cost_policy_fingerprint": baseline.require_costs().fingerprint,
            "runner_version": RUNNER_VERSION,
            "blockers": sorted(set(blockers)),
            "runner_tail_integrated": not blockers,
            "real_backtest_invoked": False,
            "model_fit_invoked": False,
            "prediction_invoked": False,
            "performance_calculated": False,
            "prices_adjusted": False,
            "run_input_frozen": False,
            "backtest_authorized": False,
        }
    )


def verify_runner_tail(
    inputs: Mapping[str, FrozenJSON],
    *,
    pins: FrozenJSON,
    expected_pins_fingerprint: str,
) -> FrozenJSON:
    """Validate pinned upstream artifacts, not raw data or self-declared approvals.

    Missing/mismatched structure raises ContractError. Well-formed unmet evidence
    yields explicit blockers. Neither result publishes readiness or calls a runner.
    """
    try:
        return _verify(inputs, pins, expected_pins_fingerprint)
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise ContractError("malformed_runner_tail_evidence") from None
