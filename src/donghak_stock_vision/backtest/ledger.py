"""Pure, immutable cash-equity ledger. No fills inferred and no persistent writes."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any

from donghak_stock_vision.backtest.contracts import RunManifest, VirtualFill, VirtualOrder
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import (
    fields,
    instant,
    integer,
    money,
    require,
    sha,
    text,
    utc,
)
from donghak_stock_vision.data.learning import digest

TERMINAL = {"filled", "cancelled", "expired", "rejected"}


@dataclass(frozen=True)
class LedgerPolicy:
    document: FrozenJSON

    def __post_init__(self) -> None:
        p = fields(
            self.document.to_dict(),
            "version currency cost_method settlement costs "
            "cost_quantum cost_rounding reservation_shortfall expiry_boundary",
        )
        text(p["version"])
        require(p["expiry_boundary"] == "exclusive", "unsupported_expiry_boundary")
        require(p["currency"] == "KRW", "unsupported_currency")
        require(p["cost_method"] == "average", "unsupported_cost_method")
        require(p["settlement"] == "immediate_virtual", "unsupported_settlement")
        require(
            p["costs"] == "supplied_fill_buy_capitalize_sell_expense", "unsupported_cost_policy"
        )
        require(p["reservation_shortfall"] == "reject", "unsupported_shortfall_policy")
        p["cost_quantum"] = money(p["cost_quantum"], positive=True)
        q = Decimal(p["cost_quantum"])
        require(q.as_tuple().digits == (1,), "power_of_ten_quantum_required")
        require(p["cost_rounding"] in {"half_up", "down"}, "unsupported_rounding")

        object.__setattr__(self, "document", FrozenJSON.freeze(p))

    @property
    def identifier(self) -> str:
        return self.document.identifier


@dataclass(frozen=True)
class LedgerCommand:
    document: FrozenJSON

    def __post_init__(self) -> None:
        v = fields(
            self.document.to_dict(),
            "event_id kind order_id sequence effective_at known_at source_id data",
        )
        text(v["event_id"])
        require(
            v["kind"]
            in {"initialize", "reserve_buy", "reserve_sell", "amend_cash", "release", "fill"},
            "unsupported_ledger_event",
        )
        integer(v["sequence"])
        sha(v["source_id"])
        require(isinstance(v["data"], dict), "event_data_required")
        for key in ("effective_at", "known_at"):
            v[key] = instant(v[key])
        require(utc(v["effective_at"]) <= utc(v["known_at"]), "event_time_order")
        if v["kind"] == "initialize":
            require(v["order_id"] is None, "initialization_order_forbidden")
        else:
            sha(v["order_id"])
        object.__setattr__(self, "document", FrozenJSON.freeze(v))

    @property
    def identifier(self) -> str:
        return self.document.identifier


def amount(value: Decimal) -> str:
    return money(format(value, "f"), signed=True)


def provenance(manifest: RunManifest, record: dict[str, Any]) -> None:
    m = manifest.to_dict()
    require(record["run_id"] == manifest.identifier, "run_mismatch")
    for key in ("information_mode", "data_origin", "usage_restriction"):
        require(record[key] == m[key], "mixed_provenance")


@dataclass(frozen=True)
class LedgerState:
    manifest: RunManifest
    tape_hash: str
    policy: LedgerPolicy
    events: tuple[LedgerCommand, ...]
    snapshot: FrozenJSON

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        sha(self.tape_hash)

    @property
    def identifier(self) -> str:
        return digest(
            {
                "manifest": self.manifest.identifier,
                "tape": self.tape_hash,
                "policy": self.policy.identifier,
                "events": [e.identifier for e in self.events],
                "state": self.snapshot.to_dict(),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot.to_dict()


def boundary(manifest: RunManifest, event: dict[str, Any], cutoff: str) -> None:
    m = manifest.to_dict()
    known = utc(event["known_at"])
    start, end = utc(m["start_at"]), utc(m["end_at"])
    require(
        start <= known <= end
        if m["time_basis"]["boundary"] == "inclusive"
        else start < known < end,
        "event_outside_run",
    )
    require(known <= utc(cutoff) <= end, "event_not_known_at_cutoff")


def initialize(
    manifest: RunManifest,
    tape_hash: str,
    policy: LedgerPolicy,
    event: LedgerCommand,
    *,
    cutoff: str,
) -> LedgerState:
    e = event.document.to_dict()
    require(e["kind"] == "initialize", "initialization_required")
    fields(e["data"], "")
    boundary(manifest, e, cutoff)
    sha(tape_hash)
    a = manifest.to_dict()["initial_account"]
    require(utc(a["received_at"]) <= utc(e["effective_at"]), "future_initial_account")
    require(a["reserved_cash_krw"] == "0", "initial_reservations_unsupported")
    positions = {}
    for row in a["positions"]:
        require(
            row["reserved_quantity"] == 0 and row["sellable_quantity"] == row["quantity"],
            "initial_locked_or_unsettled_position",
        )
        require(row["quantity"] > 0 or Decimal(row["cost_basis_krw"]) == 0, "cost_without_position")
        positions[row["ticker"]] = dict(row)
    state = {
        "scope": "research",
        "virtual": True,
        "operational_eligible": False,
        "executable": False,
        **{
            k: manifest.to_dict()[k]
            for k in ("execution_mode", "information_mode", "data_origin", "usage_restriction")
        },
        "account_id": a["account_id"],
        "revision": a["revision"],
        "available_cash_krw": a["available_cash_krw"],
        "reserved_cash_krw": "0",
        "total_cash_krw": a["available_cash_krw"],
        "positions": positions,
        "realized_pnl_krw": "0",
        "orders": {},
        "fills": {},
        "cost_charges": {},
        "last_event_id": e["event_id"],
        "last_sequence": e["sequence"],
        "known_at": e["known_at"],
        "effective_at": e["effective_at"],
    }
    return LedgerState(manifest, tape_hash, policy, (event,), FrozenJSON.freeze(state))


def cash_reserve(state: dict[str, Any], old: Decimal, new: Decimal) -> None:
    available = Decimal(state["available_cash_krw"]) + old - new
    reserved = Decimal(state["reserved_cash_krw"]) - old + new
    require(available >= 0 and reserved >= 0, "insufficient_cash")
    state.update(available_cash_krw=amount(available), reserved_cash_krw=amount(reserved))


def ceiling(data: dict[str, Any]) -> Decimal:
    fields(data, "max_notional_krw max_cost_krw")
    return Decimal(money(data["max_notional_krw"], positive=True)) + Decimal(
        money(data["max_cost_krw"])
    )


def reserve(state: dict[str, Any], original: LedgerState, e: dict[str, Any]) -> None:
    d = (
        fields(e["data"], "order max_notional_krw max_cost_krw")
        if e["kind"] == "reserve_buy"
        else fields(e["data"], "order")
    )
    contract = VirtualOrder.from_dict(d["order"])
    order = contract.to_dict()
    provenance(original.manifest, order)
    require(order["policy_id"] == original.policy.identifier, "order_policy_mismatch")
    require(e["source_id"] == order["decision_id"], "order_source_mismatch")
    require(contract.identifier == e["order_id"], "order_identity_mismatch")
    require(e["order_id"] not in state["orders"], "duplicate_order")
    require(
        all(
            x["original"]["idempotency_key"] != order["idempotency_key"]
            for x in state["orders"].values()
        ),
        "duplicate_order_key",
    )
    require(order["account_revision"] == state["revision"], "stale_account_revision")
    require(
        order["status"] == "open" and order["filled_quantity"] == order["cancelled_quantity"] == 0,
        "new_open_order_required",
    )
    require(
        order["reserved_cash_krw"] == "0" and order["reserved_quantity"] == 0,
        "pre_reserved_order_forbidden",
    )
    require(utc(order["accepted_at"]) <= utc(e["effective_at"]), "order_not_accepted")
    require(
        order["expires_at"] is None or utc(e["effective_at"]) < utc(order["expires_at"]),
        "order_expired",
    )
    if e["kind"] == "reserve_buy":
        require(order["side"] == "BUY", "order_side_mismatch")
        require(
            state["positions"].get(order["ticker"], {}).get("quantity", 0) == 0,
            "additional_buy_forbidden",
        )
        require(
            all(
                x["current"]["ticker"] != order["ticker"] or x["current"]["status"] in TERMINAL
                for x in state["orders"].values()
            ),
            "active_ticker_order",
        )
        value = ceiling({k: d[k] for k in ("max_notional_krw", "max_cost_krw")})
        cash_reserve(state, Decimal(0), value)
        order["reserved_cash_krw"] = amount(value)
    else:
        require(order["side"] == "SELL", "order_side_mismatch")
        p = state["positions"].get(order["ticker"])
        require(
            p is not None and p["sellable_quantity"] >= order["quantity"], "insufficient_shares"
        )
        p["sellable_quantity"] -= order["quantity"]
        p["reserved_quantity"] += order["quantity"]
        order["reserved_quantity"] = order["quantity"]
    state["orders"][e["order_id"]] = {"original": contract.to_dict(), "current": order}


def release(state: dict[str, Any], order: dict[str, Any], e: dict[str, Any]) -> None:
    d = fields(e["data"], "status")
    require(d["status"] in {"cancelled", "expired", "rejected"}, "invalid_release_status")
    if d["status"] == "rejected":
        require(order["filled_quantity"] == 0, "cannot_reject_filled_order")
    if d["status"] == "expired":
        require(
            order["expires_at"] is not None and utc(e["effective_at"]) >= utc(order["expires_at"]),
            "premature_expiry",
        )
    cash_reserve(state, Decimal(order["reserved_cash_krw"]), Decimal(0))
    if order["side"] == "SELL":
        p = state["positions"][order["ticker"]]
        p["sellable_quantity"] += order["reserved_quantity"]
        p["reserved_quantity"] -= order["reserved_quantity"]
    order.update(
        status=d["status"],
        cancelled_quantity=order["remaining_quantity"],
        remaining_quantity=0,
        reserved_quantity=0,
        reserved_cash_krw="0",
    )
    if d["status"] == "cancelled":
        order["cancelled_at"] = e["effective_at"]


def fill(
    state: dict[str, Any], original: LedgerState, order: dict[str, Any], e: dict[str, Any]
) -> None:
    d = fields(e["data"], "fill")
    f = VirtualFill.from_dict(d["fill"])
    v = f.to_dict()
    provenance(original.manifest, v)
    require(e["source_id"] == v["source_event_id"], "fill_source_mismatch")
    require(
        v["order_id"] == e["order_id"] and v["ticker"] == order["ticker"], "fill_order_mismatch"
    )
    require(v["policy_id"] == order["policy_id"], "fill_policy_mismatch")
    require(
        v["fill_at"] == e["effective_at"]
        and v["fill_known_at"] == e["known_at"]
        and v["sequence"] == e["sequence"],
        "fill_event_time_mismatch",
    )
    require(utc(v["fill_at"]) >= utc(order["accepted_at"]), "fill_before_acceptance")
    require(
        order["expires_at"] is None or utc(v["fill_at"]) < utc(order["expires_at"]),
        "fill_after_expiry",
    )
    require(v["cost_charge_id"] not in state["cost_charges"], "duplicate_cost_charge")
    n = v["quantity"]
    require(n <= order["remaining_quantity"], "overfill")
    notional, fee, tax = (Decimal(v[k]) for k in ("notional_krw", "fee_krw", "tax_krw"))
    require(notional == Decimal(v["price_krw"]) * n, "notional_mismatch")
    ticker = order["ticker"]
    if order["side"] == "BUY":
        cost = notional + fee + tax
        reserved = Decimal(order["reserved_cash_krw"])
        require(cost <= reserved, "reservation_shortfall")
        remaining = reserved - cost
        state["reserved_cash_krw"] = amount(Decimal(state["reserved_cash_krw"]) - cost)
        order["reserved_cash_krw"] = amount(remaining)
        p = state["positions"].setdefault(
            ticker,
            {
                "ticker": ticker,
                "quantity": 0,
                "sellable_quantity": 0,
                "reserved_quantity": 0,
                "cost_basis_krw": "0",
            },
        )
        p["quantity"] += n
        p["sellable_quantity"] += n
        p["cost_basis_krw"] = amount(Decimal(p["cost_basis_krw"]) + cost)
        if n == order["remaining_quantity"]:
            cash_reserve(state, remaining, Decimal(0))
            order["reserved_cash_krw"] = "0"
        else:
            require(remaining > 0, "remaining_buy_unreserved")
    else:
        p = state["positions"][ticker]
        require(
            n <= order["reserved_quantity"] <= p["reserved_quantity"] <= p["quantity"],
            "insufficient_reserved_shares",
        )
        proceeds = notional - fee - tax
        require(proceeds >= 0, "negative_sale_proceeds")
        settings = original.policy.document.to_dict()
        basis = Decimal(p["cost_basis_krw"])
        allocated = (
            basis
            if n == p["quantity"]
            else (basis * n / p["quantity"]).quantize(
                Decimal(settings["cost_quantum"]),
                rounding=ROUND_HALF_UP if settings["cost_rounding"] == "half_up" else ROUND_DOWN,
            )
        )
        require(0 <= allocated <= basis, "invalid_cost_allocation")
        p["quantity"] -= n
        p["reserved_quantity"] -= n
        p["cost_basis_krw"] = amount(basis - allocated)
        order["reserved_quantity"] -= n
        state["available_cash_krw"] = amount(Decimal(state["available_cash_krw"]) + proceeds)
        state["realized_pnl_krw"] = amount(
            Decimal(state["realized_pnl_krw"]) + proceeds - allocated
        )
    order["filled_quantity"] += n
    order["remaining_quantity"] -= n
    order["status"] = "filled" if order["remaining_quantity"] == 0 else "partial"
    state["fills"][f.identifier] = v
    state["cost_charges"][v["cost_charge_id"]] = f.identifier


def invariants(state: dict[str, Any]) -> None:
    available, reserved = Decimal(state["available_cash_krw"]), Decimal(state["reserved_cash_krw"])
    require(available >= 0 and reserved >= 0, "negative_cash")
    state["total_cash_krw"] = amount(available + reserved)
    require(
        sum(Decimal(o["current"]["reserved_cash_krw"]) for o in state["orders"].values())
        == reserved,
        "cash_reservation_mismatch",
    )
    for ticker, p in state["positions"].items():
        for key in ("quantity", "sellable_quantity", "reserved_quantity"):
            integer(p[key])
        require(
            p["sellable_quantity"] + p["reserved_quantity"] == p["quantity"], "share_conservation"
        )
        require(
            sum(
                o["current"]["reserved_quantity"]
                for o in state["orders"].values()
                if o["current"]["ticker"] == ticker
            )
            == p["reserved_quantity"],
            "share_reservation_mismatch",
        )
        money(p["cost_basis_krw"])
    for entry in state["orders"].values():
        VirtualOrder.from_dict(entry["current"])


def apply(state: LedgerState, event: LedgerCommand, *, cutoff: str) -> LedgerState:
    e = event.document.to_dict()
    boundary(state.manifest, e, cutoff)
    for previous in state.events:
        if previous.document.to_dict()["event_id"] == e["event_id"]:
            require(previous == event, "event_id_conflict")
            return state
    current = state.to_dict()
    if e["kind"] == "fill":
        candidate = VirtualFill.from_dict(fields(e["data"], "fill")["fill"])
        if candidate.identifier in current["fills"]:
            require(candidate.to_dict()["order_id"] == e["order_id"], "fill_order_mismatch")
            require(
                candidate.to_dict()["fill_known_at"] == e["known_at"]
                and candidate.to_dict()["fill_at"] == e["effective_at"]
                and candidate.to_dict()["sequence"] == e["sequence"],
                "fill_event_time_mismatch",
            )
            require(
                candidate.to_dict()["source_event_id"] == e["source_id"], "fill_source_mismatch"
            )
            return LedgerState(
                state.manifest,
                state.tape_hash,
                state.policy,
                (*state.events, event),
                state.snapshot,
            )
    require(e["sequence"] > current["last_sequence"], "sequence_reversed")
    require(utc(e["known_at"]) >= utc(current["known_at"]), "known_time_reversed")
    require(
        utc(e["effective_at"]) >= utc(current["effective_at"]),
        "effective_time_reversed",
    )
    with localcontext(Context(prec=80)):
        if e["kind"] in {"reserve_buy", "reserve_sell"}:
            reserve(current, state, e)
        else:
            require(e["order_id"] in current["orders"], "missing_order")
            order = current["orders"][e["order_id"]]["current"]
            require(order["status"] in {"open", "partial"}, "order_state_blocked")
            if e["kind"] == "amend_cash":
                require(order["side"] == "BUY", "cash_amend_requires_buy")
                value = ceiling(e["data"])
                cash_reserve(current, Decimal(order["reserved_cash_krw"]), value)
                order["reserved_cash_krw"] = amount(value)
            elif e["kind"] == "release":
                release(current, order, e)
            else:
                require(e["kind"] == "fill", "unexpected_initialization")
                fill(current, state, order, e)
        invariants(current)
    current.update(
        revision=integer(current["revision"] + 1),
        last_sequence=e["sequence"],
        last_event_id=e["event_id"],
        known_at=e["known_at"],
        effective_at=e["effective_at"],
    )
    return LedgerState(
        state.manifest,
        state.tape_hash,
        state.policy,
        (*state.events, event),
        FrozenJSON.freeze(current),
    )


def checkpoint(state: LedgerState) -> FrozenJSON:
    return FrozenJSON.freeze(
        {
            "version": 1,
            "manifest": state.manifest.to_dict(),
            "manifest_hash": state.manifest.identifier,
            "tape_hash": state.tape_hash,
            "policy": state.policy.document.to_dict(),
            "events": [e.document.to_dict() for e in state.events],
            "events_hash": digest([e.identifier for e in state.events]),
            "state": state.to_dict(),
            "state_hash": state.identifier,
        }
    )


def restore(document: FrozenJSON) -> LedgerState:
    c = fields(
        document.to_dict(),
        "version manifest manifest_hash tape_hash policy events events_hash state state_hash",
    )
    require(c["version"] == 1 and type(c["version"]) is int, "checkpoint_version")
    manifest = RunManifest.from_dict(c["manifest"])
    require(manifest.identifier == c["manifest_hash"], "checkpoint_manifest_mismatch")
    events = tuple(LedgerCommand(FrozenJSON.freeze(e)) for e in c["events"])
    require(bool(events), "checkpoint_events_missing")
    require(
        digest([e.identifier for e in events]) == c["events_hash"], "checkpoint_events_mismatch"
    )
    state = initialize(
        manifest,
        c["tape_hash"],
        LedgerPolicy(FrozenJSON.freeze(c["policy"])),
        events[0],
        cutoff=events[0].document.to_dict()["known_at"],
    )
    for event in events[1:]:
        state = apply(state, event, cutoff=event.document.to_dict()["known_at"])
    require(
        state.to_dict() == c["state"] and state.identifier == c["state_hash"],
        "checkpoint_state_mismatch",
    )
    return state
