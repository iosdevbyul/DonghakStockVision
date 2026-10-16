"""Metadata evidence/compatibility tests; market bars are isolated synthetic fixtures."""

import gzip
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from donghak_stock_vision.api import security_metadata as module
from donghak_stock_vision.api.app import create_app
from donghak_stock_vision.api.exporter import (
    build_snapshot,
    load_artifact,
    snapshot_bytes,
)
from donghak_stock_vision.api.ohlcv import OhlcvMarketReader
from donghak_stock_vision.api.security_metadata import (
    MetadataError,
    SecurityMetadataReader,
    SecurityMetadataSnapshot,
    build_metadata,
    diagnose,
    export_metadata,
    fingerprint,
    metadata_bytes,
)
from donghak_stock_vision.api.snapshot import ResearchAPISnapshot, SnapshotReader
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.security_metadata_cli import main
from tests.api.test_exporter import make_market_db, repository_root
from tests.api.test_ohlcv import make_database


@pytest.fixture(scope="module")
def example(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[ResearchAPISnapshot, SecurityMetadataSnapshot]:
    root = repository_root()
    research = build_snapshot(
        market_db=make_market_db(tmp_path_factory.mktemp("metadata-market")),
        root=root,
        publication_path=root / "docs/research-api/model-publication.json",
    )
    return research, build_metadata(root, research)


def envelope(value: dict[str, Any]) -> str:
    return json.dumps({"artifact": value, "fingerprint": digest(value)})


def test_reproduce_993_871_122_without_security_name_fallback(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
) -> None:
    research, metadata = example
    report = diagnose(metadata, research)
    assert report["universe"] == 993 and report["legacyNamed"] == 871
    assert report["legacyMissing"] == report["missingWithoutExactCorporationMapping"] == 122
    assert report["missingEnglishNameWithExactMapping"] == 0
    assert report["currentSecurityNamesVerified"] == 0
    assert report["securityTypes"] == {"common": 0, "preferred": 0, "other": 0, "unknown": 993}
    assert report["missingWithHistoricalBoundaryOnly"] == 9
    mapping = load_artifact(
        repository_root() / "docs/evidence/baseline-dart/artifacts/dart-corporation-mapping.json"
    ).artifact
    missing = [r["ticker"] for r in mapping["securities"] if not r["matches"]]
    assert [s["ticker"] for s in report["missing"]] == missing
    assert all(
        s.securityName is None and s.securityNameEnglish is None for s in metadata.securities
    )
    assert all(s.historicalIdentityVerified is False for s in metadata.securities)


def test_corporation_is_not_security_and_suffix_is_not_an_issuer_join(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
) -> None:
    _, metadata = example
    securities = {s.ticker: s for s in metadata.securities}
    corporations = {c.corporationId: c for c in metadata.corporations}
    parent = securities["000070"]
    assert corporations[parent.corporationId or ""].koreanName == "삼양홀딩스"
    assert securities["000075"].corporationId is None
    assert securities["000075"].corporationRelationship == "unavailable"
    for ticker in ("00088K", "00104K", "33626L"):
        assert securities[ticker].ticker == ticker
        assert securities[ticker].securityType == "unknown"
        assert securities[ticker].securityName is None
    assert len(metadata.corporations) == 871
    assert all(c.verifiedAsOf is None for c in metadata.corporations)
    assert corporations[parent.corporationId or ""].sourceModifiedDate is not None


def test_historical_boundaries_do_not_supply_current_names_or_effective_dates(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
) -> None:
    _, metadata = example
    boundary = next(b for b in metadata.historicalBoundaries if b.ticker == "000547")
    assert boundary.effectiveBoundary.isoformat() == "2023-07-17"
    assert boundary.scope == "exact_boundary_only_not_current_name_or_full_period"
    security = next(s for s in metadata.securities if s.ticker == boundary.ticker)
    assert security.securityType == "unknown" and security.securityName is None
    assert security.verifiedAsOf is None and security.corporationId is None
    ref = next(r for r in metadata.sources if r.evidenceId == boundary.evidenceReference)
    assert ref.publisher == "KRX KIND" and ref.verifiedAsOf is None


def test_deterministic_replay_preserves_original_snapshot_bytes_and_names(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
) -> None:
    research, metadata = example
    before = snapshot_bytes(research)
    repeated = build_metadata(repository_root(), research)
    assert repeated == metadata
    assert metadata_bytes(repeated) == metadata_bytes(metadata)
    assert fingerprint(repeated) == fingerprint(metadata)
    assert snapshot_bytes(research) == before
    output = tmp_path / "metadata.json"
    assert export_metadata(repository_root(), research, output) == fingerprint(metadata)
    assert output.read_bytes() == metadata_bytes(metadata)
    original = output.read_bytes()
    with pytest.raises(MetadataError, match="metadata_output_already_exists"):
        export_metadata(repository_root(), research, output)
    assert output.read_bytes() == original


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "missing_corporation",
        "missing_evidence",
        "name_without_evidence",
        "type_without_evidence",
        "invalid_ticker",
    ],
)
def test_contract_rejects_invalid_references_and_unverified_assertions(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    kind: str,
) -> None:
    _, metadata = example
    value = metadata.model_dump(mode="json")
    security = value["securities"][0]
    if kind == "duplicate":
        value["securities"].insert(0, security)
    elif kind == "missing_corporation":
        value["corporations"] = []
    elif kind == "missing_evidence":
        value["sources"] = []
    elif kind == "name_without_evidence":
        security["securityName"] = "Unverified candidate"
    elif kind == "type_without_evidence":
        security["securityType"] = "preferred"
    else:
        security["ticker"] = "00088k"
    with pytest.raises(ValidationError):
        SecurityMetadataSnapshot.model_validate(value)


@pytest.mark.parametrize(
    "forgery",
    ["fingerprint", "rehash_name", "rehash_relationship", "wrong_expected", "missing_boundary"],
)
def test_reader_fails_closed_even_with_rehashed_fabricated_facts(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
    forgery: str,
) -> None:
    research, metadata = example
    value = metadata.model_dump(mode="json")
    target = next(s for s in value["securities"] if s["ticker"] == "000075")
    if forgery == "rehash_name":
        target.update(
            securityName="삼양홀딩스우",
            nameStatus="verified_in_source_snapshot",
            nameEvidenceReference="dart:corpCode",
        )
    elif forgery == "rehash_relationship":
        parent = next(s for s in value["securities"] if s["ticker"] == "000070")
        target.update(
            corporationId=parent["corporationId"],
            corporationRelationship="verified_exact_dart_stock_code",
            relationshipEvidenceReference="dart:corpCode",
        )
    elif forgery == "missing_boundary":
        value["historicalBoundaries"] = []
    payload = json.loads(envelope(value))
    expected = payload["fingerprint"]
    if forgery == "fingerprint":
        payload["artifact"]["limitations"] = []
    if forgery == "wrong_expected":
        expected = "0" * 64
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(MetadataError, match="fingerprint_mismatch|source_replay_mismatch"):
        SecurityMetadataReader.from_path(
            path, expected_fingerprint=expected, root=repository_root(), research=research
        )


@pytest.mark.parametrize("corruption", ["raw", "mapping", "missing_raw", "historical_raw"])
def test_original_source_tampering_is_not_replaced_by_metadata_claims(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
    corruption: str,
) -> None:
    research, _ = example
    root = repository_root()
    dart = Path("docs/evidence/baseline-dart")
    lifecycle = Path("docs/evidence/baseline-security-lifecycle")
    for rel in [
        dart / "artifacts/dart-corporation-mapping.json",
        dart / "raw/corpCode.zip",
        lifecycle / "artifacts/historical-security-identities.json",
    ]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, tmp_path / rel)
    if corruption == "mapping":
        p = tmp_path / dart / "artifacts/dart-corporation-mapping.json"
        value = json.loads(p.read_text())["artifact"]
        value["securities"][0]["matches"][0]["corp_name"] = "Forged company"
        p.write_text(envelope(value))
    elif corruption == "historical_raw":
        source = load_artifact(
            root / lifecycle / "artifacts/historical-security-identities.json"
        ).artifact["boundary_identities"][0]["source"]
        p = tmp_path / lifecycle / "sources" / (source["document_reference"] + ".html.gz")
        p.parent.mkdir(parents=True)
        p.write_bytes(gzip.compress(b"changed source"))
    else:
        p = tmp_path / dart / "raw/corpCode.zip"
        if corruption == "missing_raw":
            p.unlink()
        else:
            p.write_bytes(b"changed source")
    with pytest.raises(
        (MetadataError, OSError), match="fingerprint_mismatch|replay_mismatch|No such file"
    ):
        build_metadata(tmp_path, research)


def test_optional_endpoint_and_existing_stock_ohlcv_operations_contracts(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research, metadata = example
    reader = SnapshotReader(research)
    metadata_reader = SecurityMetadataReader(metadata, root=repository_root(), research=research)
    db = tmp_path / "ohlcv.sqlite3"
    market = OhlcvMarketReader(db, expected_sha256=make_database(db))
    before = TestClient(create_app(reader, ohlcv_reader=market))
    after = TestClient(
        create_app(reader, ohlcv_reader=market, security_metadata_reader=metadata_reader)
    )
    monkeypatch.setattr(
        module, "build_metadata", lambda *_: pytest.fail("request must not replay or read evidence")
    )
    for path in (
        "/api/v1/stocks",
        "/api/v1/stocks/005930",
        "/api/v1/stocks/005930/ohlcv",
        "/api/v1/operations/runs",
    ):
        assert before.get(path).json() == after.get(path).json()
    assert before.get("/api/v1/security-metadata").status_code == 503
    response = after.get("/api/v1/security-metadata")
    assert response.status_code == 200
    assert response.json() == json.loads(metadata_bytes(metadata))
    preferred = after.get("/api/v1/security-metadata/00088K")
    assert preferred.json()["security"]["ticker"] == "00088K"
    assert preferred.json()["security"]["corporationId"] is None
    assert preferred.json()["corporation"] is None
    assert after.get("/api/v1/security-metadata/999999").status_code == 404
    assert after.get("/api/v1/security-metadata/00088k").status_code == 422
    company = after.get("/api/v1/security-metadata/000070").json()["corporation"]
    assert company["koreanName"] == "삼양홀딩스"
    assert company["nameLanguages"] == ["ko", "en"]
    assert len(after.get("/api/v1/stocks").json()) == 993
    assert sum(s["name"] is None for s in after.get("/api/v1/stocks").json()) == 122


def test_reader_binding_rejects_different_research_snapshot(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
) -> None:
    research, metadata = example
    reader = SecurityMetadataReader(metadata, root=repository_root(), research=research)
    with pytest.raises(MetadataError, match="reader_mismatch"):
        create_app(security_metadata_reader=reader)
    value = research.model_dump(mode="json")
    value["stocks"][0]["name"] = "Different legacy name"
    changed = ResearchAPISnapshot.model_validate(value)
    with pytest.raises(MetadataError, match="legacy_stock_name_mismatch"):
        build_metadata(repository_root(), changed)
    with pytest.raises(MetadataError, match="reader_mismatch"):
        create_app(SnapshotReader(changed), security_metadata_reader=reader)


def test_cli_diagnosis_export_and_fail_closed_load(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    research, metadata = example
    snapshot = tmp_path / "research.json"
    snapshot.write_bytes(snapshot_bytes(research))
    args = ["--root", str(repository_root()), "--snapshot", str(snapshot)]
    main(args)
    assert json.loads(capsys.readouterr().out)["legacyMissing"] == 122
    output = tmp_path / "metadata.json"
    main([*args, "--output", str(output)])
    assert capsys.readouterr().out.strip() == "metadata_fingerprint=" + fingerprint(metadata)
    restored = SecurityMetadataReader.from_path(
        output,
        expected_fingerprint=fingerprint(metadata),
        root=repository_root(),
        research=research,
    )
    assert restored.snapshot == metadata
    with pytest.raises(SystemExit, match="metadata_output_already_exists"):
        main([*args, "--output", str(output)])


def test_one_corporation_many_securities_requires_explicit_raw_matches(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
) -> None:
    import io
    import zipfile

    from donghak_stock_vision.backtest.dart_evidence import corporation_mapping

    research, _ = example
    value = research.model_dump(mode="json")
    value["stocks"] = [s for s in value["stocks"] if s["ticker"] in {"000070", "000075"}]
    for stock in value["stocks"]:
        stock["name"] = "Synthetic corporation"
    value["coverage"].update(universe=2, currentIdentityVerified=2)
    research = ResearchAPISnapshot.model_validate(value)
    xml = (
        "<result>"
        + "".join(
            "<list><corp_code>12345678</corp_code><corp_name>합성 회사</corp_name>"
            "<corp_eng_name>Synthetic corporation</corp_eng_name>"
            f"<stock_code>{ticker}</stock_code><modify_date>20250101</modify_date></list>"
            for ticker in ("000070", "000075")
        )
        + "</result>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml.encode())
    raw = buffer.getvalue()
    mapping = corporation_mapping(raw, ("000070", "000075"))
    dart = tmp_path / "docs/evidence/baseline-dart"
    (dart / "raw").mkdir(parents=True)
    (dart / "artifacts").mkdir()
    (dart / "raw/corpCode.zip").write_bytes(raw)
    (dart / "artifacts/dart-corporation-mapping.json").write_text(envelope(mapping.to_dict()))
    history = tmp_path / "docs/evidence/baseline-security-lifecycle/artifacts"
    history.mkdir(parents=True)
    (history / "historical-security-identities.json").write_text(
        envelope(
            {
                "current_corporation_mapping_fingerprint": mapping.identifier,
                "boundary_identities": [],
            }
        )
    )
    metadata = build_metadata(tmp_path, research)
    assert len(metadata.corporations) == 1
    assert [s.corporationId for s in metadata.securities] == ["DART:12345678"] * 2
    assert all(s.securityName is None and s.securityType == "unknown" for s in metadata.securities)
    assert metadata.pitVerified is False


def test_optional_api_startup_requires_fingerprint_and_replays_sources(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from donghak_stock_vision.api_cli import main as api_main

    research, metadata = example
    research_path, metadata_path = tmp_path / "research.json", tmp_path / "metadata.json"
    research_path.write_bytes(snapshot_bytes(research))
    metadata_path.write_bytes(metadata_bytes(metadata))
    served: list[FastAPI] = []

    def serve(app: FastAPI, *, host: str, port: int) -> None:
        assert host == "127.0.0.1" and port == 8000
        served.append(app)

    monkeypatch.setattr("uvicorn.run", serve)
    args = ["--snapshot", str(research_path), "--security-metadata", str(metadata_path)]
    with pytest.raises(SystemExit) as incomplete:
        api_main(args)
    assert incomplete.value.code == 2
    api_main(
        [
            *args,
            "--security-metadata-fingerprint",
            fingerprint(metadata),
            "--metadata-evidence-root",
            str(repository_root()),
        ]
    )
    assert len(served) == 1
    assert TestClient(served[0]).get("/api/v1/security-metadata").status_code == 200


def test_research_universe_and_coverage_cannot_drift(
    example: tuple[ResearchAPISnapshot, SecurityMetadataSnapshot],
) -> None:
    research, _ = example
    value = research.model_dump(mode="json")
    value["coverage"]["universe"] = 992
    with pytest.raises(MetadataError, match="universe_count_mismatch"):
        build_metadata(repository_root(), ResearchAPISnapshot.model_validate(value))
    value["coverage"].update(universe=993, currentIdentityVerified=872)
    with pytest.raises(MetadataError, match="corporation_coverage_mismatch"):
        build_metadata(repository_root(), ResearchAPISnapshot.model_validate(value))
