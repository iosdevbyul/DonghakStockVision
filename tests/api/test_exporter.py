import json
import sqlite3
from pathlib import Path

import pytest

from donghak_stock_vision.api.exporter import (
    SnapshotExportError,
    build_snapshot,
    export_snapshot,
    load_artifact,
    load_publication,
    read_market_snapshot,
    snapshot_bytes,
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def make_market_db(tmp_path: Path) -> Path:
    root = repository_root()
    mapping = load_artifact(
        root / "docs/evidence/baseline-dart/artifacts/dart-corporation-mapping.json"
    ).artifact
    tickers = [record["ticker"] for record in mapping["securities"]]
    path = tmp_path / "market.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE bars ("
            "ticker TEXT NOT NULL, trading_date TEXT NOT NULL, payload TEXT NOT NULL,"
            "PRIMARY KEY(ticker,trading_date))"
        )
        for ticker in tickers:
            payload = json.dumps(
                {
                    "ticker": ticker,
                    "trading_date": "2024-07-01",
                    "provider": "krx",
                    "market": "KOSPI",
                    "adjustment": "unadjusted",
                }
            )
            db.execute(
                "INSERT INTO bars(ticker,trading_date,payload) VALUES(?,?,?)",
                (ticker, "2024-07-01", payload),
            )
        db.commit()
    return path


def test_committed_evidence_artifacts_and_publication_validate() -> None:
    root = repository_root()

    calendar = load_artifact(
        root / "docs/evidence/baseline-official/artifacts/official-calendar.json"
    )
    publication = load_publication(root / "docs/research-api/model-publication.json")

    assert calendar.fingerprint == (
        "6501a189c73e8e69063276142fd592ed995fc35e35e05b0d87c048a51240dc7b"
    )
    assert tuple(model.id for model in publication.models) == ("up", "down")
    assert publication.models[0].oos.averagePrecision == 0.369409
    assert publication.models[1].oos.averagePrecision == 0.442473


def test_market_snapshot_is_read_only_and_scoped_to_expected_metadata(tmp_path: Path) -> None:
    market_db = make_market_db(tmp_path)
    before = market_db.read_bytes()

    snapshot = read_market_snapshot(market_db)

    assert snapshot.universe == 993
    assert snapshot.total_rows == 993
    assert snapshot.first_date == "2024-07-01"
    assert snapshot.last_date == "2024-07-01"
    assert market_db.read_bytes() == before


def test_snapshot_export_uses_reviewed_evidence_without_closing_blockers(tmp_path: Path) -> None:
    root = repository_root()
    market_db = make_market_db(tmp_path)

    snapshot = build_snapshot(
        market_db=market_db,
        root=root,
        publication_path=root / "docs/research-api/model-publication.json",
    )

    assert snapshot.coverage.universe == 993
    assert snapshot.coverage.currentIdentityVerified == 871
    assert snapshot.coverage.historicalIdentityVerified == 0
    assert snapshot.coverage.unresolvedSecurities == 43
    assert snapshot.coverage.unexplainedTickerSessions == 8_118
    assert snapshot.models[0].role == "baseline_executable"
    assert snapshot.models[1].role == "research_only"

    evidence = {item.id: item for item in snapshot.evidence}
    assert evidence["calendar"].state == "verified"
    assert evidence["settlement"].state == "verified"
    assert evidence["security-lifecycle"].state == "in_progress"
    assert evidence["corporate-actions"].state == "blocked"

    readiness = {item.label: item.state for item in snapshot.baselineBacktest.readiness}
    assert readiness["Runner tail integration"] == "blocked"
    assert readiness["Run input freeze"] == "blocked"
    assert readiness["Historical execution"] == "not_started"
    assert snapshot.baselineBacktest.performanceAvailable is False


def test_snapshot_bytes_are_deterministic(tmp_path: Path) -> None:
    root = repository_root()
    market_db = make_market_db(tmp_path)

    first = build_snapshot(
        market_db=market_db,
        root=root,
        publication_path=root / "docs/research-api/model-publication.json",
    )
    second = build_snapshot(
        market_db=market_db,
        root=root,
        publication_path=root / "docs/research-api/model-publication.json",
    )

    assert snapshot_bytes(first) == snapshot_bytes(second)


def test_export_refuses_to_replace_existing_snapshot(tmp_path: Path) -> None:
    root = repository_root()
    market_db = make_market_db(tmp_path)
    output = tmp_path / "snapshot.json"
    output.write_text("existing", encoding="utf-8")

    with pytest.raises(SnapshotExportError, match="already exists"):
        export_snapshot(
            market_db=market_db,
            root=root,
            publication_path=root / "docs/research-api/model-publication.json",
            output=output,
        )


def test_market_universe_must_match_reviewed_mapping(tmp_path: Path) -> None:
    root = repository_root()
    market_db = make_market_db(tmp_path)
    with sqlite3.connect(market_db) as db:
        first = db.execute("SELECT ticker FROM bars ORDER BY ticker LIMIT 1").fetchone()
        assert first is not None
        db.execute("DELETE FROM bars WHERE ticker=?", (first[0],))
        db.commit()

    with pytest.raises(SnapshotExportError, match="universe differs"):
        build_snapshot(
            market_db=market_db,
            root=root,
            publication_path=root / "docs/research-api/model-publication.json",
        )
