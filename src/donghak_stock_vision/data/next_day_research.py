"""Explicitly separate delayed-input feature/label timing contract."""

REQUEST = "next_day_research_request_v2"
LABEL = "delayed_reversal_barrier_v2"
CONTRACT = dict(
    version="next_day_forward_contract_v2",
    feature_version="ohlcv_value_v1",
    label_version=LABEL,
    skipped_sessions=1,
    horizon_observations=5,
    target_rule="second_verified_session_after_input",
    deadline="target_date_00:00_Asia_Seoul",
    barrier_reference="target_session_open",
    feature_history="input_date_and_earlier_only",
    threshold=0.5,
    historical_availability="unverified_not_point_in_time",
)
