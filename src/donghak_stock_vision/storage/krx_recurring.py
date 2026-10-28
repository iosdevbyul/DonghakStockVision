"""Prospective recurring owner-reviewed KOSPI scope over existing capture storage."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import fields, require, sha, text, utc
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan, FullMarketScope
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.storage.krx_authorization import (
    OPERATOR_ACK,
    bounded,
    endpoint,
    review_time,
    verify_operator_documents,
)

POLICY = "operator_full_market_recurring_v1"


def verify_recurring_authorization(
    plan: ForwardCapturePlan,
    attempt: str,
    reference: FrozenJSON,
    trust: FrozenJSON | None,
    at: datetime,
) -> FrozenJSON:
    plan.__post_init__()
    require(plan.full_market_scope is not None, "explicit_full_market_plan_required")
    require(trust is None, "recurring_operator_policy_is_not_independent_review")
    require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text(attempt)) is not None, "invalid_attempt_identity"
    )
    ref = fields(reference.to_dict(), "root grant_pin")
    root = Path(ref["root"])
    require(
        root.is_dir() and not root.is_symlink() and root.stat().st_mode & 0o077 == 0,
        "private_authorization_root_required",
    )
    policy = FrozenJSON(bounded(root / "recurring-policy.json", 65536).decode())
    require(policy.identifier == sha(ref["grant_pin"]), "recurring_policy_pin_mismatch")
    d = fields(
        policy.to_dict(),
        "version operator_id acknowledgement scope_acknowledgement reviewed_at "
        "valid_from valid_until "
        "approved_from approved_through service_id method permission terms specification endpoint "
        "capture_root transport document_origin max_observation_lag_days credential_sha256 uses "
        "full_market_scope daily_limit verified_service_daily_limit "
        "max_response_bytes max_seconds interval_seconds",
    )
    require(
        d["version"] == POLICY
        and d["acknowledgement"] == OPERATOR_ACK
        and d["scope_acknowledgement"] == "explicit_full_market_recurring_read_only",
        "new_full_market_operator_review_required",
    )
    text(d["operator_id"])
    require(
        d["service_id"] == "stk_bydd_trd"
        and d["method"] == "GET"
        and d["endpoint"] == endpoint(plan),
        "recurring_service_endpoint_mismatch",
    )
    scope = FullMarketScope.from_dict(d["full_market_scope"])
    require(scope == plan.full_market_scope, "recurring_scope_mismatch")
    require(
        d["uses"] == {"private_capture": True, "forward_signal": False, "paper": False}
        and all(type(x) is bool for x in d["uses"].values()),
        "recurring_read_only_required",
    )
    require(
        (d["transport"], d["document_origin"])
        in (
            ("provider_https", "operator_reviewed_source_copies"),
            ("provider_https", "operator_supplied_provider_originals"),
            ("mock_fixture", "synthetic_test_fixture"),
        ),
        "recurring_document_origin_required",
    )
    start, end, reviewed = (utc(d[n]) for n in ("valid_from", "valid_until", "reviewed_at"))
    require(
        start == scope.valid_from
        and end == scope.valid_until
        and start <= reviewed < end
        and all(
            utc(d[n]).isoformat() == d[n] for n in ("valid_from", "valid_until", "reviewed_at")
        ),
        "recurring_review_must_precede_capture",
    )
    from datetime import date

    first, last = (date.fromisoformat(d[n]) for n in ("approved_from", "approved_through"))
    require(
        first <= start.astimezone(SEOUL).date() <= last
        and first <= (end - timedelta(microseconds=1)).astimezone(SEOUL).date() <= last
        and first <= plan.day <= last
        and all(first <= day <= last for day in scope.permitted_days),
        "recurring_service_approval_outside_validity",
    )
    require(
        type(d["verified_service_daily_limit"]) is int
        and 1 <= d["verified_service_daily_limit"] <= 10000
        and type(d["daily_limit"]) is int
        and 1 <= d["daily_limit"] <= min(9500, d["verified_service_daily_limit"]),
        "recurring_daily_budget_invalid",
    )
    require(
        all(
            type(d[n]) is int and d[n] == getattr(plan, n)
            for n in ("daily_limit", "max_response_bytes", "max_seconds", "interval_seconds")
        ),
        "recurring_plan_budget_mismatch",
    )
    require(
        type(d["max_observation_lag_days"]) is int and 1 <= d["max_observation_lag_days"] <= 7,
        "recurring_observation_lag_invalid",
    )
    verify_operator_documents(d, root)
    require(
        plan.authorization_reference == policy.identifier
        and plan.terms_reference == d["terms"]["sha256"],
        "recurring_plan_policy_binding_mismatch",
    )
    require(
        Path(d["capture_root"]).is_absolute()
        and str(Path(d["capture_root"]).resolve()) == d["capture_root"],
        "canonical_reviewed_capture_root_required",
    )
    archive = Path(d["capture_root"])
    require(
        not archive.exists()
        or (archive.is_dir() and not archive.is_symlink() and archive.stat().st_mode & 0o077 == 0),
        "private_recurring_capture_root_required",
    )
    sha(d["credential_sha256"])
    review_time(policy, at)
    require(
        1 <= (utc(at).astimezone(SEOUL).date() - plan.day).days <= d["max_observation_lag_days"],
        "outside_recurring_forward_observation_lag",
    )
    return policy
