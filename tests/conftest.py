from collections.abc import Iterator
from pathlib import Path

import pytest

from donghak_stock_vision.storage.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    instance = SQLiteStore(tmp_path / "market.sqlite3")
    yield instance
    instance.close()


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail ordinary tests if code accidentally attempts a real TCP connection."""
    if request.node.get_closest_marker("live"):
        return

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in offline tests")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.socket.connect_ex", forbidden)
