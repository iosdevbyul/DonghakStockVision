"""R4 selection on development-only projections."""

from typing import Any

from donghak_stock_vision.models import grounded_memory, predictive_research, research_v2
from donghak_stock_vision.models.failure_feedback import evidence, readout

HORIZONS = (1, 5)
BLOCKS = (("2023-08-01", "2023-10-13"), ("2023-10-16", "2023-12-28"))
ROUNDS = 2
ARM_SECONDS = 100
BA_MARGIN = predictive_research.POLICY["balanced_accuracy_margin"]


def partitions(rows: list[dict[str, Any]], groups: list[list[str]]) -> dict[str, Any]:
    panels = {
        f"ticker_group_{i + 1}": [r for r in rows if r["ticker"] in names]
        for i, names in enumerate(groups)
    }
    panels.update(
        {
            f"time_block_{i + 1}": [
                r for r in rows if first <= r["input_date"] <= r["target_date"] <= last
            ]
            for i, (first, last) in enumerate(BLOCKS)
        }
    )
    return {
        key: dict(
            metrics=readout(items, "model"),
            evidence=evidence(items),
            trading_dates=len({r["input_date"] for r in items}),
        )
        for key, items in panels.items()
    }


def stable(
    trial: dict[str, Any],
    control: dict[str, Any],
    horizon: int,
) -> dict[str, Any]:
    baseline, candidate = control["summary"]["metrics"], trial["summary"]["metrics"]
    panels = {}
    for key, panel in trial["blocks"].items():
        other = control["blocks"][key]
        m, b = panel["metrics"], other["metrics"]
        enough = predictive_research.meets_population(
            panel
        ) and predictive_research.meets_population(other)
        delta = (
            m["balanced_accuracy"] - b["balanced_accuracy"]
            if m["balanced_accuracy"] is not None and b["balanced_accuracy"] is not None
            else None
        )
        pass_block = bool(
            enough
            and delta is not None
            and delta >= 0
            and m["accuracy"] >= b["accuracy"]
            and m["brier_score"] <= b["brier_score"]
            and predictive_research.direction_guard(b, m, horizon)
        )
        panels[key] = dict(
            n=m["n"],
            anchors=panel["evidence"]["nonoverlapping_calendar_anchors"],
            sufficient=enough,
            ba_delta=delta,
            accuracy=m["accuracy"],
            fpr=m["fpr"],
            recall=m["recall"],
            passed=pass_block,
        )
    deltas = [p["ba_delta"] for p in panels.values() if p["ba_delta"] is not None]
    enough_overall = predictive_research.meets_population(trial["summary"])
    passed = bool(
        enough_overall
        and all(p["passed"] for p in panels.values())
        and candidate["balanced_accuracy"] >= baseline["balanced_accuracy"] + BA_MARGIN
        and candidate["accuracy"] >= baseline["accuracy"]
        and candidate["pr_auc_ap"] >= baseline["pr_auc_ap"]
        and candidate["brier_score"] <= baseline["brier_score"]
        and predictive_research.direction_guard(baseline, candidate, horizon)
    )
    return dict(
        passed=passed,
        panels=panels,
        worst_ba_delta=min(deltas, default=None),
        ba_delta_spread=max(deltas) - min(deltas) if deltas else None,
        evidence_sufficient=enough_overall and all(p["sufficient"] for p in panels.values()),
    )


def choose(
    options: list[dict[str, Any]],
    own: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    control: dict[str, Any],
    horizon: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    order = research_v2.rank(options, own)
    if not memory:
        return order[0], dict(counterfactual=order[0]["experiment_id"], referenced=[])
    reference = list({v["experiment_id"]: v for v in memory + own}.values())
    values = {v["experiment_id"]: v for v in grounded_memory.value_rows(options, reference)}
    base = control["summary"]["metrics"]
    costs = {}
    for i, option in enumerate(order):
        key = option["experiment_id"]
        cfg = option["config"]
        similar = [
            item
            for item in reference
            if item["config"]["candidate"]["family"] == cfg["candidate"]["family"]
            and item["config"]["mask"] == cfg["mask"]
        ]
        bad_direction = [
            item
            for item in similar
            if item["state"] == "completed"
            and not predictive_research.direction_guard(base, item["summary"]["metrics"], horizon)
        ]
        weak_blocks = [
            item
            for item in similar
            if item["state"] == "completed"
            and item["experiment_id"] != control["experiment_id"]
            and (d := stable(item, control, horizon)["worst_ba_delta"]) is not None
            and d < 0
        ]
        evidence_rows = bad_direction + weak_blocks
        failed_runs = [item for item in similar if item["state"] == "failed"]
        value = values[key]
        actual_cost = min((it.get("fit_seconds", 0.0) for it in similar), default=0.0)
        costs[key] = dict(
            penalty=3 * len(bad_direction) + len(weak_blocks) + 3 * len(failed_runs),
            information=value["score"],
            cost_proxy=value["cost_proxy"],
            recent_fit_seconds=actual_cost,
            rule_position=i,
            references=sorted({it["experiment_id"] for it in evidence_rows + failed_runs}),
        )
    best = min(
        order,
        key=lambda c: (
            costs[c["experiment_id"]]["penalty"],
            -costs[c["experiment_id"]]["information"],
            costs[c["experiment_id"]]["recent_fit_seconds"],
            costs[c["experiment_id"]]["rule_position"],
        ),
    )
    return best, dict(
        counterfactual=order[0]["experiment_id"],
        referenced=costs[best["experiment_id"]]["references"],
        candidate_evidence=costs,
    )
