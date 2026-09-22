"""Environment configuration; .env is loaded by the CLI, never by library imports."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    api_key: str = field(repr=False)
    market: str = "KOSPI"
    request_interval: float = 1.0
    daily_request_limit: int = 9500

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.getenv("DSV_DB_PATH", ".data/market.sqlite3")),
            api_key=os.getenv("KRX_API_KEY", ""),
            market=os.getenv("DSV_KRX_MARKET", "KOSPI"),
            request_interval=float(os.getenv("DSV_REQUEST_INTERVAL", "1.0")),
            daily_request_limit=int(os.getenv("DSV_DAILY_REQUEST_LIMIT", "9500")),
        )
