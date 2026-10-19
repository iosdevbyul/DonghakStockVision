"""Explicit offline publication and bounded read-only local archive access.

A trusted operator pins the index SHA outside the writable publication directory.
Hashes bind bytes; the operator's pin, not possession of a hash, grants publication.
No online cache, source DB access, inference, pickle loading or replay occurs here.
"""

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from donghak_stock_vision.api.historical_portfolio_projection import (
    PROJECTION,
    REPORT,
    RESTRICTIONS,
    RESULT,
    projection,
)
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.receipt_archive import DEFAULT_LIMITS, VERSION, ReceiptArchive
from donghak_stock_vision.data.learning import canonical

INDEX = "historical_portfolio_publications_v1"
MANIFEST = "historical_portfolio_publication_v1"
MAX_PUBLICATIONS = 128
INDEX_BYTES = 128 * 1024
MANIFEST_BYTES = 16 * 1024
PROJECTION_BYTES = 4 * 1024 * 1024
RESPONSE_BYTES = 256 * 1024
MAX_LIMIT = 20
MAX_OFFSET = 100_000
ID = re.compile(r"^[a-f0-9]{64}$")
LOCATOR = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,160}$")


class PublicationError(ValueError):
    """Stable diagnostics intentionally contain no paths or upstream exception text."""

    def __init__(self, code: str, status: int = 503) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: dict[str, Any]) -> bytes:
    return canonical(value).encode("utf-8")


def check(condition: bool, code: str, status: int = 503) -> None:
    if not condition:
        raise PublicationError(code, status)


class LocalFiles:
    """Flat operator-owned namespace; openat + O_NOFOLLOW prevents symlink races."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    @contextmanager
    def directory(self) -> Iterator[int]:
        fd: int | None = None
        try:
            check(not any(part in {".", ".."} for part in self.root.parts), "archive_unavailable")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(self.root.anchor, flags)
            for part in self.root.parts[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        except OSError as error:
            raise PublicationError("archive_unavailable") from error
        finally:
            if fd is not None:
                os.close(fd)

    def read(self, name: str, maximum: int) -> bytes:
        check(bool(LOCATOR.fullmatch(name)), "invalid_storage_locator")
        with self.directory() as directory:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    check(stat.S_ISREG(info.st_mode), "archive_unavailable")
                    check(info.st_size <= maximum, "oversized_result", 413)
                    raw = stream.read(maximum + 1)
                    check(len(raw) <= maximum, "oversized_result", 413)
                    return raw
            except OSError as error:
                raise PublicationError("archive_unavailable") from error

    def digest(self, name: str, maximum: int) -> str:
        """Online archive hashing uses one MiB chunks, never parses the graph."""
        check(bool(LOCATOR.fullmatch(name)), "invalid_storage_locator")
        with self.directory() as directory:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    check(stat.S_ISREG(info.st_mode), "archive_unavailable")
                    check(info.st_size <= maximum, "oversized_result", 413)
                    total, result = 0, hashlib.sha256()
                    while chunk := stream.read(min(1024 * 1024, maximum + 1 - total)):
                        total += len(chunk)
                        check(total <= maximum, "oversized_result", 413)
                        result.update(chunk)
                    return result.hexdigest()
            except OSError as error:
                raise PublicationError("archive_unavailable") from error

    def write(self, name: str, raw: bytes, *, replace: bool = False) -> None:
        check(bool(LOCATOR.fullmatch(name)), "invalid_storage_locator")
        with self.directory() as directory:
            # Unique temporary name, same directory. Never follow or overwrite a link.
            temporary = f"tmp-{os.getpid()}-{os.urandom(8).hex()}"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if replace:
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                else:
                    try:
                        os.link(
                            temporary,
                            name,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        check(self.read(name, len(raw)) == raw, "publication_manifest_mismatch")
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    @contextmanager
    def writer(self) -> Iterator[None]:
        # Same local kernel lock convention as existing evidence workflows.
        import fcntl

        with self.directory() as directory:
            fd = os.open(
                "writer.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            try:
                check(stat.S_ISREG(os.fstat(fd).st_mode), "archive_unavailable")
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)


def decode(raw: bytes, code: str) -> dict[str, Any]:
    try:
        return FrozenJSON(raw.decode("utf-8")).to_dict()
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PublicationError(code) from error


def read_index(files: LocalFiles, expected: str | None) -> dict[str, Any]:
    if expected is None:
        # Initialization must be explicit; never interpret corrupt existing storage as empty.
        with files.directory() as directory:
            try:
                os.stat("index.json", dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return {"version": INDEX, "entries": []}
        raise PublicationError("publication_manifest_mismatch")
    check(bool(ID.fullmatch(expected)), "publication_manifest_mismatch")
    raw = files.read("index.json", INDEX_BYTES)
    check(sha(raw) == expected, "publication_manifest_mismatch")
    index = decode(raw, "publication_manifest_mismatch")
    check(
        set(index) == {"version", "entries"} and index["version"] == INDEX,
        "publication_manifest_mismatch",
    )
    entries = index["entries"]
    check(
        isinstance(entries, list) and len(entries) <= MAX_PUBLICATIONS,
        "publication_manifest_mismatch",
    )
    ids = []
    for entry in entries:
        check(
            isinstance(entry, dict) and set(entry) == {"id", "manifestHash", "revoked"},
            "publication_manifest_mismatch",
        )
        check(
            isinstance(entry["id"], str)
            and bool(ID.fullmatch(entry["id"]))
            and isinstance(entry["manifestHash"], str)
            and bool(ID.fullmatch(entry["manifestHash"]))
            and type(entry["revoked"]) is bool,
            "publication_manifest_mismatch",
        )
        ids.append(entry["id"])
    check(ids == sorted(set(ids)), "publication_manifest_mismatch")
    return index


def safe_projection(value: dict[str, Any]) -> None:
    """Do not publish filesystem locators or arbitrary expanded source dictionaries."""
    check(value.get("schemaVersion") == PROJECTION, "report_projection_incomplete")
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            check(
                not any(
                    k in {"checkpoint_path", "api_key", "AUTH_KEY", "database_path"} for k in item
                ),
                "report_projection_incomplete",
            )
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            check(
                not item.startswith(("/", "file:", "sqlite:", "\\"))
                and not re.match(r"^[A-Za-z]:[\\/]", item),
                "report_projection_incomplete",
            )


def publish(
    root: Path,
    locator: str,
    *,
    expected_index_hash: str | None,
    expected_result_hash: str,
    expected_report_hash: str,
) -> tuple[str, str]:
    """Offline only. Source must be a flat file inside explicitly configured root.

    Returns opaque report ID and new index SHA to pin in the serving configuration.
    Full replay happens only here, never through an HTTP route.
    """
    files = LocalFiles(root)
    check(
        bool(ID.fullmatch(expected_result_hash)) and bool(ID.fullmatch(expected_report_hash)),
        "result_validation_required",
    )
    raw = files.read(locator, DEFAULT_LIMITS.transport_bytes)
    try:
        archive = ReceiptArchive.from_json(raw.decode("utf-8"))
        check(archive.document.to_dict().get("version") == VERSION, "unsupported_archive_version")
        check(
            archive.document.to_dict().get("metadata", {}).get("result_version") == RESULT,
            "unsupported_archive_version",
        )
        metadata = archive.document.to_dict()
        check(
            metadata.get("legacy_hash") == expected_result_hash
            and metadata.get("metadata", {}).get("detailed_report_hash") == expected_report_hash,
            "result_validation_required",
        )
        source = archive.expand_validated().to_dict()
        check(source["version"] == RESULT, "unsupported_archive_version")
        check(
            sha(encoded(source)) == expected_result_hash
            and source["detailed_report_hash"] == expected_report_hash,
            "result_validation_required",
        )
        view = projection(source, archive.transport_hash)
        safe_projection(view)
    except PublicationError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as error:
        raise PublicationError("result_validation_required") from error
    view_raw = encoded(view)
    check(len(view_raw) <= PROJECTION_BYTES, "oversized_result", 413)
    binding = {
        "archiveHash": sha(raw),
        "projectionHash": sha(view_raw),
        "resultHash": expected_result_hash,
        "reportHash": expected_report_hash,
    }
    report_id = sha(encoded(binding))
    with files.writer():
        index = read_index(files, expected_index_hash)
        existing = next((e for e in index["entries"] if e["id"] == report_id), None)
        if existing:
            check(not existing["revoked"], "report_unpublished", 404)
            # Recheck the existing complete publication; don't silently repair corrupt storage.
            PortfolioPublicationReader(root, expected_index_hash or "").summary(report_id)
            assert expected_index_hash is not None
            return report_id, expected_index_hash
        check(len(index["entries"]) < MAX_PUBLICATIONS, "oversized_result", 413)
        manifest = {
            "version": MANIFEST,
            "id": report_id,
            **binding,
            "archiveVersion": VERSION,
            "resultVersion": RESULT,
            "reportVersion": REPORT,
            "projectionVersion": PROJECTION,
            "archiveLocator": f"{report_id}.archive.json",
            "projectionLocator": f"{report_id}.projection.json",
            "publishedAt": datetime.now(UTC).isoformat(),
            "validation": "authoritative_replay_passed",
        }
        manifest_raw = encoded(manifest)
        files.write(manifest["archiveLocator"], raw)
        files.write(manifest["projectionLocator"], view_raw)
        files.write(f"{report_id}.manifest.json", manifest_raw)
        index["entries"].append(
            {"id": report_id, "manifestHash": sha(manifest_raw), "revoked": False}
        )
        index["entries"].sort(key=lambda e: e["id"])
        index_raw = encoded(index)
        check(len(index_raw) <= INDEX_BYTES, "oversized_result", 413)
        files.write("index.json", index_raw, replace=True)
    return report_id, sha(index_raw)


def revoke(root: Path, report_id: str, *, expected_index_hash: str) -> str:
    """Offline revocation; old readers immediately fail their pinned-index check."""
    files = LocalFiles(root)
    with files.writer():
        index = read_index(files, expected_index_hash)
        entry = next((e for e in index["entries"] if e["id"] == report_id), None)
        check(entry is not None, "report_not_found", 404)
        assert entry is not None
        entry["revoked"] = True
        raw = encoded(index)
        files.write("index.json", raw, replace=True)
    return sha(raw)


class PortfolioPublicationReader:
    """No mutable verification cache. Every query checks exact pinned current bytes.

    Trust assumes an operator-controlled root and external index pin. Concurrent
    changes yield either a verified byte snapshot or an unavailable response. This
    is integrity/publication access, not user authentication or investment approval.
    """

    def __init__(self, root: Path, expected_index_hash: str) -> None:
        self.files = LocalFiles(root)
        self.expected_index_hash = expected_index_hash
        read_index(self.files, expected_index_hash)

    def _load(self, report_id: str, index: dict[str, Any]) -> dict[str, Any]:
        check(bool(ID.fullmatch(report_id)), "report_not_found", 404)
        entry = next((e for e in index["entries"] if e["id"] == report_id), None)
        check(entry is not None, "report_not_found", 404)
        assert entry is not None
        check(not entry["revoked"], "report_unpublished", 404)
        manifest_raw = self.files.read(f"{report_id}.manifest.json", MANIFEST_BYTES)
        check(sha(manifest_raw) == entry["manifestHash"], "publication_manifest_mismatch")
        manifest = decode(manifest_raw, "publication_manifest_mismatch")
        check(
            set(manifest)
            == {
                "version",
                "id",
                "archiveHash",
                "projectionHash",
                "resultHash",
                "reportHash",
                "archiveVersion",
                "resultVersion",
                "reportVersion",
                "projectionVersion",
                "archiveLocator",
                "projectionLocator",
                "publishedAt",
                "validation",
            },
            "publication_manifest_mismatch",
        )
        try:
            published_at = datetime.fromisoformat(manifest["publishedAt"])
            check(published_at.utcoffset() is not None, "publication_manifest_mismatch")
        except (TypeError, ValueError) as error:
            raise PublicationError("publication_manifest_mismatch") from error
        check(
            manifest.get("id") == report_id and manifest.get("version") == MANIFEST,
            "publication_manifest_mismatch",
        )
        check(
            manifest.get("validation") == "authoritative_replay_passed",
            "result_validation_required",
        )
        check(
            (
                manifest.get("archiveVersion"),
                manifest.get("resultVersion"),
                manifest.get("reportVersion"),
                manifest.get("projectionVersion"),
            )
            == (VERSION, RESULT, REPORT, PROJECTION),
            "unsupported_archive_version",
        )
        check(
            manifest.get("archiveLocator") == f"{report_id}.archive.json"
            and manifest.get("projectionLocator") == f"{report_id}.projection.json",
            "invalid_storage_locator",
        )
        archive_hash = self.files.digest(manifest["archiveLocator"], DEFAULT_LIMITS.transport_bytes)
        check(archive_hash == manifest.get("archiveHash"), "archive_integrity_mismatch")
        view_raw = self.files.read(manifest["projectionLocator"], PROJECTION_BYTES)
        check(sha(view_raw) == manifest.get("projectionHash"), "publication_manifest_mismatch")
        binding = {
            k: manifest.get(k)
            for k in ("archiveHash", "projectionHash", "resultHash", "reportHash")
        }
        check(sha(encoded(binding)) == report_id, "publication_manifest_mismatch")
        view = decode(view_raw, "report_projection_incomplete")
        safe_projection(view)
        check(
            view["summary"]["resultHash"] == manifest["resultHash"]
            and view["summary"]["reportHash"] == manifest["reportHash"],
            "publication_manifest_mismatch",
        )
        view["summary"].update(
            {
                "reportId": report_id,
                "publishedAt": manifest["publishedAt"],
                "archiveVerification": "integrity_verified",
                "reportVerification": "offline_authoritative_replay_passed",
            }
        )
        return view

    def _view(self, report_id: str) -> dict[str, Any]:
        return self._load(report_id, read_index(self.files, self.expected_index_hash))

    @staticmethod
    def pagination(offset: int, limit: int) -> None:
        check(
            type(offset) is int
            and 0 <= offset <= MAX_OFFSET
            and type(limit) is int
            and 1 <= limit <= MAX_LIMIT,
            "invalid_pagination",
            422,
        )

    @staticmethod
    def response(value: dict[str, Any]) -> dict[str, Any]:
        value = {"schemaVersion": PROJECTION, **RESTRICTIONS, **value}
        check(len(encoded(value)) <= RESPONSE_BYTES, "oversized_result", 413)
        return value

    def collection(self, offset: int = 0, limit: int = 10) -> dict[str, Any]:
        self.pagination(offset, limit)
        index = read_index(self.files, self.expected_index_hash)
        entries = [e for e in index["entries"] if not e["revoked"]]
        items = []
        for entry in entries[offset : offset + limit]:
            summary = self._load(entry["id"], index)["summary"]
            items.append({**RESTRICTIONS, **selected_summary(summary)})
        return self.response(
            {"items": items, "total": len(entries), "offset": offset, "limit": limit}
        )

    def summary(self, report_id: str) -> dict[str, Any]:
        return self.response(self._view(report_id)["summary"])

    def ticker(
        self, report_id: str, ticker: str, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        self.pagination(offset, limit)
        check(bool(re.fullmatch(r"[0-9A-Z]{6}", ticker)), "invalid_ticker", 422)
        view = self._view(report_id)
        check(ticker in view["tickers"], "ticker_not_found", 404)
        rows = [x["evidence"] for x in view["trades"] if x["evidence"]["ticker"] == ticker]
        return self.response(
            {
                "reportId": report_id,
                **view["tickers"][ticker],
                "timeline": rows[offset : offset + limit],
                "total": len(rows),
                "offset": offset,
                "limit": limit,
            }
        )

    def trades(
        self, report_id: str, ticker: str | None = None, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        self.pagination(offset, limit)
        view = self._view(report_id)
        if ticker is not None:
            check(bool(re.fullmatch(r"[0-9A-Z]{6}", ticker)), "invalid_ticker", 422)
            check(ticker in view["tickers"], "ticker_not_found", 404)
        rows = [x for x in view["trades"] if ticker is None or x["evidence"]["ticker"] == ticker]
        return self.response(
            {
                "reportId": report_id,
                "items": rows[offset : offset + limit],
                "total": len(rows),
                "offset": offset,
                "limit": limit,
            }
        )


def selected_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        k: summary[k]
        for k in (
            "reportId",
            "resultSchemaVersion",
            "reportSchemaVersion",
            "modelFamily",
            "tickers",
            "period",
            "initialVirtualCash",
            "publishedAt",
            "archiveVerification",
            "reportVerification",
            "valuationCoverage",
            "portfolioValuationAvailable",
            "evidenceScope",
        )
    }
