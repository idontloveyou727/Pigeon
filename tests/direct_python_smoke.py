from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".python_deps"))

from pigeon_ingest.models import TornLogEntry
from pigeon_ingest.storage import IngestStorage


async def main() -> None:
    database_path = Path(tempfile.gettempdir()) / "direct_python_smoke.sqlite3"
    if database_path.exists():
        database_path.unlink()

    storage = IngestStorage(database_path)
    await storage.open()
    try:
        log = TornLogEntry.from_payload(
            {
                "id": "direct-1",
                "timestamp": 1778385315,
                "details": {"id": 4103, "title": "Item receive", "category": "Item sending"},
                "data": {"sender": 1234, "items": [{"id": 206, "qty": 3, "uid": 1}], "message": None},
                "params": {"italic": 1, "color": "green"},
            }
        )
        result = await storage.record_deposit_from_log(log, item_id=206, unit_value=800000)
        snapshot = await storage.get_balance(1234)
        print("direct-python-ok", result.after_balance if result else None, snapshot.balance)
    finally:
        await storage.close()
        if database_path.exists():
            database_path.unlink()


if __name__ == "__main__":
    asyncio.run(main())
