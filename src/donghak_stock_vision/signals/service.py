"""Research replay and strict PIT inference; operational queries never fall back."""

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from donghak_stock_vision.data.learning import (
    THRESHOLD,
    AnalysisError,
    Mode,
    digest,
    event_contract,
    timestamp,
)
from donghak_stock_vision.data.schema import SEOUL
from donghak_stock_vision.data.snapshot import capture, snapshot_bars
from donghak_stock_vision.features.engine import feature_vector
from donghak_stock_vision.models.linear import score
from donghak_stock_vision.models.service import load_model
from donghak_stock_vision.signals.dataset import context
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.base import MarketDataStore


class SignalService:
    def __init__(self, store: AnalysisStore) -> None:
        self.store = store

    def research(
        self, version: str, snapshot_id: str, tickers: list[str], anchor_date: date
    ) -> list[dict[str, Any]]:
        model = load_model(self.store, version, "historical_research")
        snapshot = self.store.get("snapshot", snapshot_id)
        if model["snapshot_id"] != snapshot_id:
            raise AnalysisError("snapshot_mismatch")
        anchor = datetime.combine(anchor_date, time(16), SEOUL)
        return [
            self._infer(model, version, snapshot, snapshot_id, ticker, anchor) for ticker in tickers
        ]

    def point_in_time(
        self,
        market: MarketDataStore,
        version: str,
        tickers: list[str],
        as_of: datetime,
        quality: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        model = load_model(self.store, version, "point_in_time")
        as_of = timestamp(as_of)
        if self.store.created_at("model", version) > as_of:
            return [
                self._failure(model, version, t, as_of, "model_not_available_as_of")
                for t in tickers
            ]
        end = as_of.astimezone(SEOUL).date() - timedelta(days=1)
        try:
            start = (
                min(date.fromisoformat(quality["coverage"][t]["start"]) for t in tickers)
                if quality
                else end - timedelta(days=400)
            )
            snapshot = capture(
                market,
                tickers,
                start,
                end,
                as_of,
                "point_in_time",
                quality=quality,
                synthetic=model["data_origin"] == "synthetic",
            )
            if snapshot["profiles"] != model["profiles"] or not set(snapshot["markets"]) <= set(
                model["markets"]
            ):
                raise AnalysisError("price_basis_mismatch")
        except (AnalysisError, KeyError) as error:
            status = str(error) if isinstance(error, AnalysisError) else "quality_unverified"
            return [self._failure(model, version, t, as_of, status) for t in tickers]
        snapshot_id = self.store.put("snapshot", snapshot)
        return [self._infer(model, version, snapshot, snapshot_id, t, as_of) for t in tickers]

    def _base(
        self, model: dict[str, Any], version: str, ticker: str, anchor: datetime
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "model_version": version,
            "model_created_at": self.store.created_at("model", version).isoformat(),
            **{k: model[k] for k in ("mode", "data_origin", "usage_restriction", "anchor_policy")},
            **event_contract(model["session_basis"]),
            "anchor_at": anchor.isoformat(),
            "snapshot_as_of": None,
            "data_as_of": anchor.isoformat() if model["mode"] == "point_in_time" else None,
            "snapshot_id": None,
            "last_trading_date": None,
            "latest_collected_at": None,
            "input_hash": None,
            "input_status": "ok",
            "quality_flags": model["quality_flags"],
            "context": None,
            "up_score": None,
            "down_score": None,
            "up_status": "context_mismatch",
            "down_status": "context_mismatch",
            "evaluation_status": {
                k: v["evaluation_status"] for k, v in model["directions"].items()
            },
            "operational_status": "unregistered",
            "signal_state": "no_context",
            "reasons": {},
        }

    def _save(self, result: dict[str, Any]) -> dict[str, Any]:
        result_id = self.store.put("signal", result)
        return {**result, "analysis_id": result_id, "analyzed_at": datetime.now(UTC).isoformat()}

    def _failure(
        self, model: dict[str, Any], version: str, ticker: str, anchor: datetime, status: str
    ) -> dict[str, Any]:
        result = self._base(model, version, ticker, anchor)
        result.update(
            input_status=status, signal_state=status, up_status=status, down_status=status
        )
        return self._save(result)

    def _infer(
        self,
        model: dict[str, Any],
        version: str,
        snapshot: dict[str, Any],
        snapshot_id: str,
        ticker: str,
        anchor: datetime,
    ) -> dict[str, Any]:
        result = self._base(model, version, ticker, anchor)
        result.update(
            snapshot_id=snapshot_id,
            snapshot_as_of=snapshot["cutoff"],
            quality_flags=snapshot["quality_flags"],
        )
        pit = model["mode"] == "point_in_time"
        day = anchor.astimezone(SEOUL).date()
        bars = [bar for bar in snapshot_bars(snapshot, ticker) if bar.trading_date <= day]
        try:
            if not bars:
                raise AnalysisError("insufficient_history")
            last = bars[-1]
            result.update(
                last_trading_date=last.trading_date.isoformat(),
                latest_collected_at=max(b.collected_at for b in bars[-11:]).isoformat(),
                input_hash=digest([b.to_dict() for b in bars[-12:]]),
            )
            if pit and (day - last.trading_date).days > 7:
                raise AnalysisError("stale_data")
            if not pit and last.trading_date != day:
                raise AnalysisError("missing_anchor_bar")
            if pit:
                expected = [d for d in snapshot["quality"]["sessions"] if d < day.isoformat()]
                if expected and last.trading_date.isoformat() < max(expected):
                    raise AnalysisError("missing_verified_session")
            values = feature_vector(bars, snapshot, anchor if pit else None)
            direction = context(values)
            result["context"] = (
                "prior_decline"
                if direction == "up"
                else ("prior_rise" if direction == "down" else "flat")
            )
            if direction is not None:
                entry = model["directions"][direction]
                if entry["parameters"] is None:
                    result[f"{direction}_status"] = "insufficient_model_data"
                    result["signal_state"] = "insufficient_model_data"
                else:
                    prediction, reasons = score(entry["parameters"], values)
                    result[f"{direction}_score"] = prediction
                    result[f"{direction}_status"] = "scored"
                    result["signal_state"] = direction if prediction >= THRESHOLD else "neutral"
                    result["reasons"] = {direction: reasons}
        except AnalysisError as error:
            result.update(
                input_status=str(error),
                signal_state=str(error),
                up_status=str(error),
                down_status=str(error),
            )
        return self._save(result)


class SignalQueryService:
    def __init__(self, store: AnalysisStore) -> None:
        self.store = store

    def _latest(
        self,
        version: str,
        mode: Mode,
        *,
        as_of: datetime | None = None,
        ticker: str | None = None,
        snapshot_id: str | None = None,
        anchor_range: tuple[date, date] | None = None,
    ) -> list[dict[str, Any]]:
        load_model(self.store, version, mode)
        latest: dict[str, dict[str, Any]] = {}
        ranged: list[dict[str, Any]] = []
        if anchor_range and anchor_range[0] > anchor_range[1]:
            raise AnalysisError("invalid_anchor_range")
        for artifact_id, row in self.store.all("signal"):
            if row["model_version"] != version or row["mode"] != mode:
                continue
            if ticker and row["ticker"] != ticker:
                continue
            if snapshot_id and row["snapshot_id"] != snapshot_id:
                continue
            if as_of and timestamp(row["anchor_at"]) > timestamp(as_of):
                continue
            candidate = {
                **row,
                "analysis_id": artifact_id,
                "analyzed_at": self.store.created_at("signal", artifact_id).isoformat(),
            }
            if anchor_range:
                day = timestamp(row["anchor_at"]).astimezone(SEOUL).date()
                if anchor_range[0] <= day <= anchor_range[1]:
                    ranged.append(candidate)
                continue
            previous = latest.get(row["ticker"])
            if previous is None or (timestamp(candidate["anchor_at"]), candidate["analyzed_at"]) > (
                timestamp(previous["anchor_at"]),
                previous["analyzed_at"],
            ):
                latest[row["ticker"]] = candidate
        return sorted(
            ranged if anchor_range else latest.values(),
            key=lambda row: (row["ticker"], row["anchor_at"]),
        )

    def get_research_analyses(
        self,
        model_version: str,
        snapshot_id: str,
        direction: str = "all",
        ticker: str | None = None,
        limit: int = 100,
        *,
        anchor_range: tuple[date, date] | None = None,
    ) -> list[dict[str, Any]]:
        model = load_model(self.store, model_version, "historical_research")
        if model["snapshot_id"] != snapshot_id:
            raise AnalysisError("snapshot_mismatch")
        rows = self._latest(
            model_version,
            "historical_research",
            ticker=ticker,
            snapshot_id=snapshot_id,
            anchor_range=anchor_range,
        )
        return self._rank(rows, direction, limit)

    def get_point_in_time_analyses(
        self, model_version: str, as_of: datetime, ticker: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self._latest(model_version, "point_in_time", as_of=as_of, ticker=ticker)
        result = []
        for row in rows:
            row = self._refresh_stale(row, as_of)
            if row["up_score"] is not None and row["down_score"] is not None:
                row = {
                    **row,
                    "input_status": "contract_conflict",
                    "signal_state": "contract_conflict",
                    "up_score": None,
                    "down_score": None,
                    "up_status": "contract_conflict",
                    "down_status": "contract_conflict",
                    "reasons": {},
                }
            result.append(row)
        return result

    @staticmethod
    def _refresh_stale(row: dict[str, Any], as_of: datetime) -> dict[str, Any]:
        if (
            row["last_trading_date"]
            and (
                timestamp(as_of).astimezone(SEOUL).date()
                - date.fromisoformat(row["last_trading_date"])
            ).days
            > 7
        ):
            return {
                **row,
                "input_status": "stale_data",
                "signal_state": "stale_data",
                "up_score": None,
                "down_score": None,
                "up_status": "stale_data",
                "down_status": "stale_data",
                "reasons": {},
            }
        return row

    @staticmethod
    def _rank(rows: list[dict[str, Any]], direction: str, limit: int) -> list[dict[str, Any]]:
        if direction not in {"up", "down", "all"} or limit < 1:
            raise AnalysisError("invalid_query")
        if direction == "all":
            return rows[:limit]
        selected = [r for r in rows if r[f"{direction}_score"] is not None]
        return sorted(selected, key=lambda row: (-row[f"{direction}_score"], row["ticker"]))[:limit]

    def _registered(self, version: str, direction: str, as_of: datetime) -> bool:
        model = load_model(self.store, version)
        if (
            model["mode"] != "point_in_time"
            or model["data_origin"] != "real"
            or (model["usage_restriction"] != "pit_review_required")
        ):
            return False
        entry = model["directions"][direction]
        if (
            entry["evaluation_status"] != "validation_qualified"
            or entry["test_status"] != "evaluated"
        ):
            return False
        row = self.store.connection.execute(
            "SELECT registered_at,evidence FROM operational_registry "
            "WHERE model_version=? AND direction=?",
            (version, direction),
        ).fetchone()
        if row is None or timestamp(row[0]) > timestamp(as_of):
            return False
        try:
            evidence = json.loads(row[1])
            return bool(
                evidence["reviewer"]
                and evidence["artifact_hash"] == version
                and timestamp(evidence["reviewed_at"]) <= timestamp(row[0])
                and all(
                    evidence.get(key) is True
                    for key in (
                        "approved",
                        "real_data_quality_verified",
                        "pit_verified",
                        "final_test_reviewed",
                    )
                )
                and evidence.get("evidence_hashes")
                and not evidence.get("revoked")
            )
        except (ValueError, TypeError, KeyError):
            return False

    def operational(
        self,
        direction: str,
        as_of: datetime,
        model_version: str | None = None,
        ticker: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        as_of = timestamp(as_of)
        if direction not in {"up", "down"} or limit < 1:
            raise AnalysisError("invalid_query")
        if model_version is None:
            rows = self.store.connection.execute(
                "SELECT model_version FROM operational_registry WHERE direction=? "
                "ORDER BY registered_at DESC",
                (direction,),
            ).fetchall()
            model_version = next(
                (r[0] for r in rows if self._registered(r[0], direction, as_of)), None
            )
            if model_version is None:
                return {"scope": "operational", "reason": "no_registered_model", "items": []}
        if not self._registered(model_version, direction, as_of):
            raise AnalysisError("registration_forbidden")
        latest = self.get_point_in_time_analyses(model_version, as_of, ticker)
        eligible = []
        for row in latest:
            if row["up_score"] is not None and row["down_score"] is not None:
                continue  # contract_conflict: never merge conditional predictions
            if row["input_status"] == "ok" and row["signal_state"] == direction:
                eligible.append({**row, "operational_status": "registered"})
        return {
            "scope": "operational",
            "model_version": model_version,
            "items": self._rank(eligible, direction, limit),
        }

    def get_latest_up_signals(
        self, as_of: datetime, model_version: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        return self.operational("up", as_of, model_version, limit=limit)

    def get_latest_down_signals(
        self, as_of: datetime, model_version: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        return self.operational("down", as_of, model_version, limit=limit)

    def get_latest_analysis(
        self, ticker: str, as_of: datetime, model_version: str
    ) -> dict[str, Any]:
        if not any(self._registered(model_version, d, as_of) for d in ("up", "down")):
            raise AnalysisError("registration_forbidden")
        rows = self.get_point_in_time_analyses(model_version, as_of, ticker)
        for row in rows:
            row["operational_status"] = "registered"
            for direction in ("up", "down"):
                if not self._registered(model_version, direction, as_of):
                    row[f"{direction}_score"] = None
                    row[f"{direction}_status"] = "unregistered"
                    row["reasons"].pop(direction, None)
                    if row["signal_state"] == direction:
                        row["signal_state"] = "unregistered"
        return {"scope": "operational", "items": rows}
