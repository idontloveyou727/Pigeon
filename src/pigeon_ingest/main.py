from __future__ import annotations

import argparse
import asyncio
import logging

from .config import IngestConfig
from .ingest_service import IngestService
from .storage import IngestStorage
from .torn_client import TornClient


async def _async_main(run_once: bool) -> None:
    config = IngestConfig.from_env()
    storage = IngestStorage(config.database_path)
    client = TornClient(config.api_key, config.base_url, config.timeout_seconds)
    service = IngestService(config, storage, client)

    await storage.open()
    try:
        if run_once:
            await service.run_once()
        else:
            await service.run_forever()
    finally:
        await client.close()
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Torn API ingest service")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_async_main(run_once=args.once))


if __name__ == "__main__":
    main()
