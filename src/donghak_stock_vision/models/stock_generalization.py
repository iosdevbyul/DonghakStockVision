"""Same-time, unseen-symbol assessment; no held-out score enters search memory."""

from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import Sample, digest
from donghak_stock_vision.models import research_v2
from donghak_stock_vision.models.diagnostics import metrics
from donghak_stock_vision.models.metrics import evaluate_metrics
from donghak_stock_vision.signals.dataset import counts

SPACE = "multi_stock_generalization_v1"


def catalog(horizons: list[int]) -> list[dict[str, Any]]:
    permitted = [
        c
        for c in research_v2.catalog(horizons)
        if c["candidate"]["parameters"].get("solver") != "liblinear"
    ]
    all_configs = [c | dict(stock_policy=p) for c in permitted for p in ("pooled", "heldout")]
    # Predeclared arm: matched feature/stock-policy controls before adaptive variety.
    anchors = [
        c
        for h in horizons
        for m in ("v2_legacy", "v2_multi")
        for p in ("pooled", "heldout")
        for c in all_configs
        if c["goal"]["horizon"] == h
        and c["mask"] == m
        and c["stock_policy"] == p
        and c["candidate"]["name"] == "v2_forest3_32"
    ]
    used = {digest(c) for c in anchors}
    return anchors + [c for c in all_configs if digest(c) not in used]


def validate_scope(spec: dict[str, Any], tickers: list[str]) -> None:
    require(
        10 <= len(tickers) <= 25 and tickers == sorted(set(tickers)), "multi_stock_cohort_bound"
    )
    held = spec["heldout_tickers"]
    require(
        type(held) is list
        and 2 <= len(held) <= len(tickers) // 3
        and held == sorted(set(held))
        and set(held) < set(tickers),
        "multi_stock_heldout_scope",
    )
    require(
        spec["budget"] == 12 and spec["policy"] in ("adaptive", "predeclared"),
        "multi_stock_fixed_budget_policy",
    )


def selection(plan: dict[str, Any], done: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    owners = {c["experiment_id"]: c["config"] for c in plan["configurations"]}
    require(
        all(k in owners and v["config"] == owners[k] for k, v in done.items()),
        "multi_stock_memory_owner",
    )
    if len(done) >= plan["experiment_budget"]:
        return None
    choices = [c for c in plan["configurations"] if c["experiment_id"] not in done]
    if not choices:
        return None
    h = min(
        sorted({c["config"]["goal"]["horizon"] for c in choices}),
        key=lambda h: sum(v["config"]["goal"]["horizon"] == h for v in done.values()),
    )
    choices = [c for c in choices if c["config"]["goal"]["horizon"] == h]
    if plan["selection_policy"] == "predeclared":
        policy = choices[0]["config"]["stock_policy"]
    else:
        policy = min(
            ("pooled", "heldout"),
            key=lambda p: sum(
                v["config"]["goal"]["horizon"] == h and v["config"]["stock_policy"] == p
                for v in done.values()
            ),
        )
    choices = [c for c in choices if c["config"]["stock_policy"] == policy]
    history = [
        v | dict(experiment_id=k)
        for k, v in done.items()
        if v["config"]["goal"]["horizon"] == h and v["config"]["stock_policy"] == policy
    ]

    def order(memory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = research_v2.rank(choices, memory)
        policies = {v["config"]["stock_policy"] for v in memory}
        return sorted(ranked, key=lambda c: c["config"]["stock_policy"] in policies)

    ranked = choices if plan["selection_policy"] == "predeclared" else order(history)
    chosen = ranked[0]
    parent = max(
        (v for v in history if v["state"] == "completed"),
        key=lambda v: v["stability_ap"] - v["largest_train_gap"],
        default=None,
    )
    return chosen | dict(
        selection_reason="multi_stock_" + plan["selection_policy"],
        parent_experiment=None if parent is None else parent["experiment_id"],
        adaptation=dict(
            history_ids=sorted(v["experiment_id"] for v in history),
            selected_with_memory=chosen["experiment_id"],
            selected_without_memory=order([])[0]["experiment_id"],
            choice_changed=chosen["experiment_id"] != order([])[0]["experiment_id"],
            heldout_metrics_used=False,
            reference_populations_used_for_ranking=False,
            alternatives=[c["experiment_id"] for c in ranked[1:4]],
        ),
    )


def regime(sample: Sample) -> tuple[str, ...]:
    v = sample.values
    return (
        "up_trend" if v[9] > 0.03 else "down_trend" if v[9] < -0.03 else "sideways",
        "high_volatility"
        if v[4] >= 0.025
        else "low_volatility"
        if v[4] <= 0.01
        else "normal_volatility",
        "volume_surge" if v[6] >= 0.6931471805599453 else "normal_volume",
    )


def describe(rows: list[Sample], scores: list[float]) -> dict[str, Any]:
    labels = [int(s.label) for s in rows if s.label is not None]
    c = counts(rows)
    valid = bool(rows) and c["dates"] >= 20 and min(c["positive"], c["negative"]) >= 10
    return dict(
        metrics=metrics(labels, scores),
        calibration_bins=evaluate_metrics(labels, scores)["calibration_bins"],
        counts=c,
        status="internal_descriptive_sufficient" if valid else "insufficient",
        independently_qualified=False,
        calibration=None if not rows else "uncalibrated",
    )


def assessments(
    rows: list[Sample], scores: list[float], held: set[str], unseen: bool
) -> dict[str, Any]:
    def select(predicate: Any) -> dict[str, Any]:
        pairs = [(s, p) for s, p in zip(rows, scores, strict=True) if predicate(s)]
        return describe([s for s, _ in pairs], [p for _, p in pairs])

    names = sorted({name for s in rows for name in regime(s)})
    return dict(
        unseen_at_fit=unseen,
        seen_symbols=select(lambda s: s.ticker not in held),
        heldout_symbols=select(lambda s: s.ticker in held),
        ticker_distribution={
            t: select(lambda s, t=t: s.ticker == t) for t in sorted({s.ticker for s in rows})
        },
        input_regimes={n: select(lambda s, n=n: n in regime(s)) for n in names},
        heldout_regimes={
            n: select(lambda s, n=n: s.ticker in held and n in regime(s)) for n in names
        },
        periods={
            q: select(
                lambda s, q=q: f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}" == q
            )
            for q in sorted(
                {f"{s.trading_date.year}-Q{(s.trading_date.month - 1) // 3 + 1}" for s in rows}
            )
        },
        official_market_regime="UNKNOWN",
        regime_basis="causal_stock_local_features_only",
        future_returns_used_for_regimes=False,
    )
