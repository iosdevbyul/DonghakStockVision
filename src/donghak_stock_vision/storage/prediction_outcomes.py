"""Read-only case replay from an existing frozen historical evaluation.

Recomputed scores are retrospective, never contemporaneously recorded predictions.
Only validation cases are exposed; source replay retains the original evaluation.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from donghak_stock_vision.data.learning import THRESHOLD, Sample, digest, timestamp
from donghak_stock_vision.data.research_split import ExplicitResearchSplit
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.service import EvaluationService
from donghak_stock_vision.storage.analysis import ReadOnlyAnalysisStore


def prediction_outcomes(
    path: Path,
    *,
    evaluation_id: str,
    model_version: str,
    snapshot_id: str,
    direction: str,
    offset: int = 0,
    limit: int = 100,
    max_source_bars: int = 100_000,
) -> dict[str, Any]:
    """Independently replay owners, then page the original validation cohort.

    Content IDs must come from previously retained original records. They do not
    authenticate historical publication or training custody. The source limit is
    cooperative: JSON is decoded before its row count can be inspected.
    """
    if direction not in {"up", "down"}:
        raise ValueError("direction must be up or down")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 250:
        raise ValueError("invalid output page")
    if type(max_source_bars) is not int or not 1 <= max_source_bars <= 2_000_000:
        raise ValueError("invalid replay budget")
    for identifier in (evaluation_id, model_version, snapshot_id):
        if (
            not isinstance(identifier, str)
            or len(identifier) != 64
            or any(c not in "0123456789abcdef" for c in identifier)
        ):
            raise ValueError("original lowercase content IDs required")
    if not path.is_file():
        raise ValueError("analysis database does not exist")
    store = ReadOnlyAnalysisStore(path)
    try:
        store.connection.execute("BEGIN")
        original = store.get("evaluation", evaluation_id)
        if (
            original.get("mode") != "historical_research"
            or original.get("operational_status") != "unregistered"
            or original.get("usage_restriction") not in {"research_only", "synthetic_test_only"}
        ):
            raise ValueError("frozen historical research evaluation required")
        if (
            original.get("model_version") != model_version
            or original.get("snapshot_id") != snapshot_id
        ):
            raise ValueError("original model/snapshot reference mismatch")
        snapshot = store.get("snapshot", snapshot_id)
        if sum(len(rows) for rows in snapshot["rows"].values()) > max_source_bars:
            raise ValueError("source replay budget exceeded")
        replay = EvaluationService(store).evaluate(
            model_version, "historical_research", persist=False
        )
        if replay["evaluation_id"] != evaluation_id:
            raise ValueError("frozen evaluation reproduction mismatch")
        model = store.get("model", model_version)
        dataset = store.get("dataset", model["dataset_id"])
        entry = model["directions"][direction]
        eligible = [
            Sample.from_dict(row)
            for row in dataset["split"]["partitions"]["validation"]
            if row["direction"] == direction
        ]
        scored: dict[str, float] = {}
        if entry["parameters"] is not None:
            scores = [score(entry["parameters"], sample.values)[0] for sample in eligible]
            if scores != entry["validation_scores"]:
                raise ValueError("stored prediction scores do not reproduce")
            scored = {
                digest(sample.to_dict()): p for sample, p in zip(eligible, scores, strict=True)
            }
        eligible_ids = {digest(sample.to_dict()) for sample in eligible}
        start = timestamp(dataset["split"]["validation_start"])
        end = timestamp(dataset["split"]["test_start"])
        configuration = (
            ExplicitResearchSplit.from_dict(dataset["split"]["configuration"])
            if "configuration" in dataset["split"]
            else None
        )
        cases = []
        statuses: Counter[str] = Counter()
        for row in dataset["samples"]:
            sample = Sample.from_dict(row)
            if configuration is not None:
                if (
                    not configuration.validation.start
                    <= sample.trading_date
                    <= configuration.validation.end
                ):
                    continue
            elif not start <= timestamp(sample.anchor_at) < end:
                continue
            identifier = digest(sample.to_dict())
            p = scored.get(identifier)
            actual = None
            if sample.direction != direction:
                status = "context_mismatch"
            elif sample.label is None:
                status = f"unavailable:{sample.label_status}"
            elif identifier not in eligible_ids:
                status = "purged_at_split_boundary"
            elif p is None:
                status = "model_unavailable"
                actual = sample.label
            else:
                status = "evaluated"
                actual = sample.label
            statuses[status] += 1
            cases.append(
                {
                    "sampleId": identifier,
                    "ticker": sample.ticker,
                    "tradingDate": sample.trading_date.isoformat(),
                    "anchorAt": timestamp(sample.anchor_at).isoformat(),
                    "predictionRecordedAt": None,
                    "inputHash": sample.input_hash,
                    "featureHash": digest(list(sample.values)),
                    "status": status,
                    "score": p,
                    "predictedLabel": int(p >= THRESHOLD) if p is not None else None,
                    "actualLabel": actual,
                    "correct": (int(p >= THRESHOLD) == actual) if p is not None else None,
                    "labelStart": sample.label_start.isoformat()
                    if actual is not None and sample.label_start is not None
                    else None,
                    "labelEnd": sample.label_end.isoformat()
                    if actual is not None and sample.label_end is not None
                    else None,
                    "locallyRecordedLabelAvailableAt": timestamp(
                        sample.label_available_at
                    ).isoformat()
                    if actual is not None and sample.label_available_at is not None
                    else None,
                }
            )
        cases.sort(key=lambda row: (row["anchorAt"], row["ticker"]))
        total = len(cases)
        selected = cases[offset : offset + limit]
        result = {
            "schemaVersion": "phase6_prediction_outcomes_v1",
            "mode": "retrospective_frozen_validation_replay",
            "evaluationId": evaluation_id,
            "modelVersion": model_version,
            "snapshotId": snapshot_id,
            "datasetId": model["dataset_id"],
            "dataOrigin": model["data_origin"],
            "usageRestriction": model["usage_restriction"],
            "direction": direction,
            "partition": "validation",
            "horizonSessions": model["horizon_sessions"],
            "sessionBasis": model["session_basis"],
            "anchorPolicy": model["anchor_policy"],
            "labelVersion": model["label_version"],
            "threshold": THRESHOLD,
            "totalCases": total,
            "evaluatedCases": statuses["evaluated"],
            "statusCounts": dict(sorted(statuses.items())),
            "metrics": {
                key: value
                for key, value in entry["validation"].items()
                if key not in {"by_ticker", "by_date_count"}
            }
            if scored
            else None,
            "offset": offset,
            "limit": limit,
            "nextOffset": offset + len(selected) if offset + len(selected) < total else None,
            "cases": selected,
            "forwardValidated": False,
            "pitQualified": False,
            "executionAuthorized": False,
        }
        return {**result, "contentFingerprint": digest(result)}
    finally:
        store.close()
