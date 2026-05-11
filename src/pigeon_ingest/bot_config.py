from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from .env import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    token: str
    database_path: Path = Path("data/ingest.sqlite3")
    admin_user_ids: frozenset[int] = frozenset()
    guild_id: int | None = None

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("DISCORD_TOKEN is required")

        return cls(
            token=token,
            database_path=Path(os.getenv("TORN_DATABASE_PATH", "data/ingest.sqlite3")).expanduser(),
            admin_user_ids=_parse_user_ids(os.getenv("DISCORD_ADMIN_USER_IDS", "")),
            guild_id=_parse_optional_int("DISCORD_GUILD_ID"),
        )


def _parse_user_ids(raw_value: str) -> frozenset[int]:
    values: set[int] = set()
    for chunk in raw_value.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        values.add(int(piece))
    return frozenset(values)


def _parse_optional_int(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    return int(raw_value)