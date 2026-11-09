"""Read-only 005930 pilot gate over existing KRX preflight and source custody.

This never fetches data, grants permission, or promotes fixture/locally retained
provider bytes to independently authenticated market observations.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.schema import DailyBar
from donghak_stock_vision.storage.krx_readiness import preflight
from donghak_stock_vision.storage.krx_scheduler import SchedulerConfig
from donghak_stock_vision.storage.operational_capture import verify_stored_capture
from donghak_stock_vision.storage.operational_market import OperationalMarketStore

TICKER = "005930"
MARKET = "KOSPI"
ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
VERSION = "krx_005930_pilot_preflight_v1"


@dataclass(frozen=True)
class PilotEvidence:
    capture_root: Path
    plan: ForwardCapturePlan
    plan_pin: str
    attempt: str
    capture_pin: str
    batch_pin: str
    authorization: FrozenJSON | None = None
    authorization_policy: str = "independent_review_v1"
    allow_synthetic_fixture: bool = False


def audit_stored_005930(storage_root: Path, source: PilotEvidence) -> FrozenJSON:
    """Reverify original capture, exact stored batch, and linked 005930 source row."""
    require(type(source) is PilotEvidence, "pilot_original_owner_required")
    require(source.plan.market == MARKET, "pilot_market_mismatch")
    completion = verify_stored_capture(
        storage_root,
        source.capture_root,
        source.plan,
        source.plan_pin,
        source.attempt,
        source.capture_pin,
        source.batch_pin,
        authorization=source.authorization,
        authorization_policy=source.authorization_policy,
        allow_synthetic_fixture=source.allow_synthetic_fixture,
    )
    result = completion.to_dict()
    with closing(OperationalMarketStore(storage_root)) as store:
        require(store.validate().to_dict()["valid"], "pilot_operational_storage_invalid")
        row = store.connection.execute(
            "SELECT o.payload,s.raw_id FROM market_observations o "
            "JOIN observation_sources s ON s.market=o.market "
            "AND s.trading_date=o.trading_date AND s.ticker=o.ticker "
            "JOIN ingestion_batches b ON b.raw_id=s.raw_id "
            "WHERE o.market=? AND o.trading_date=? AND o.ticker=? AND b.batch_id=?",
            (MARKET, source.plan.day.isoformat(), TICKER, result["batch_id"]),
        ).fetchone()
        require(row is not None, "pilot_005930_missing_from_verified_batch")
        bar = DailyBar.from_dict(FrozenJSON(row[0]).to_dict())
        require(
            bar.ticker == TICKER
            and bar.market == MARKET
            and bar.trading_date == source.plan.day
            and bar.provider == "krx"
            and bar.adjustment == "unadjusted",
            "pilot_observation_identity_mismatch",
        )
    return FrozenJSON.freeze(
        {
            "version": "krx_005930_stored_source_audit_v1",
            "ticker": TICKER,
            "market": MARKET,
            "trading_date": source.plan.day.isoformat(),
            "source_origin": result["origin"],
            "original_capture_pin": source.capture_pin,
            "verified_batch_pin": result["batch_pin"],
            "batch_id": result["batch_id"],
            "locally_verified": True,
            "independently_authenticated_market_origin": False,
            "genuine_forward_evidence": False,
            "performance_approved": False,
            "network_requests": 0,
        }
    )


def pilot_preflight(
    config: SchedulerConfig,
    at: datetime,
    config_path: Path,
    *,
    evidence: PilotEvidence | None = None,
) -> FrozenJSON:
    """Recompute native KRX preflight; never convert it to live-call authorization."""
    base = preflight(config, at, config_path)
    info = base.to_dict()
    request = info["request"]
    exact = bool(
        request is not None
        and request["endpoint"] == ENDPOINT
        and request["method"] == "GET"
        and request["parameters"]
        == {"basDd": str(info["selected_trading_date"]).replace("-", "")}
        and request["requests_maximum"] == 1
        and request["retries"] == 0
        and request["concurrency"] == 1
        and 0 < request["max_seconds"] <= 30
        and 0 < request["max_response_bytes"] <= 2 * 1024 * 1024
    )
    blocked = list(info["blockers"])
    if not exact:
        blocked.append("005930_pilot_request_contract_not_verified")
    if info["origin"] != "provider_https":
        blocked.append("real_provider_origin_not_verified")
    stored = None
    if evidence is not None:
        stored = audit_stored_005930(config.path("operational_root"), evidence).to_dict()
    else:
        blocked.append("original_005930_capture_and_stored_readback_not_supplied")
    # A full-market KRX daily endpoint returns multiple symbols. Only the
    # selected 005930 observation is reviewed; never rewrite the request scope.
    blocked.append("separate_explicit_live_005930_operator_authorization_required")
    return FrozenJSON.freeze(
        {
            "version": VERSION,
            "pilot_ticker": TICKER,
            "market": MARKET,
            "endpoint": ENDPOINT,
            "method": "GET",
            "provider_scope": "full_market_response_with_005930_selected_for_readback",
            "as_of": info["checked_at"],
            "native_preflight_pin": base.identifier,
            "native_preflight": info,
            "request_contract_verified": exact,
            "configured_provider_ready_for_separate_review": bool(
                exact
                and info["origin"] == "provider_https"
                and info["ready_for_manual_provider_capture"]
            ),
            "stored_005930": stored,
            "live_pilot_authorized": False,
            "genuine_forward_evidence": False,
            "performance_approved": False,
            "execution_authorized": False,
            "blockers": sorted(set(blocked)),
            "network_requests": 0,
        }
    )
