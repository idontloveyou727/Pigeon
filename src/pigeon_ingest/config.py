from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class IngestConfig:
    api_key: str
    log_id: int = 4103
    limit: int = 10
    poll_interval_seconds: float = 2.0
    deposit_item_id: int = 206
    deposit_unit_value: int = 800000
    base_url: str = "https://api.torn.com/v2"
    database_path: Path = Path("data/ingest.sqlite3")
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "IngestConfig":
        api_key = os.getenv("TORN_API_KEY", "").strip()
        if not api_key:
            raise ValueError("TORN_API_KEY is required")

        return cls(
            api_key=api_key,
            log_id=_read_int("TORN_LOG_ID", 4103),
            limit=_read_int("TORN_LIMIT", 10),
            poll_interval_seconds=_read_float("TORN_POLL_INTERVAL_SECONDS", 2.0),
            deposit_item_id=_read_int("TORN_DEPOSIT_ITEM_ID", 206),
            deposit_unit_value=_read_int("TORN_DEPOSIT_UNIT_VALUE", 800000),
            base_url=os.getenv("TORN_BASE_URL", "https://api.torn.com/v2").strip(),
            database_path=Path(os.getenv("TORN_DATABASE_PATH", "data/ingest.sqlite3")).expanduser(),
            timeout_seconds=_read_float("TORN_TIMEOUT_SECONDS", 15.0),
        )


def _read_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return int(raw_value)


def _read_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return float(raw_value)
