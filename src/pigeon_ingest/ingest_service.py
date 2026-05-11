from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import IngestConfig
from .models import TornLogEntry
from .storage import IngestStorage, PollRun
from .torn_client import TornClient


class IngestService:
    def __init__(
        self,
        config: IngestConfig,
        storage: IngestStorage,
        client: TornClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    async def run_once(self) -> None:
        run = await self._storage.start_run(self._config.log_id, self._config.limit)
        await self._execute_run(run)

    async def run_forever(self) -> None:
        while True:
            cycle_started = asyncio.get_running_loop().time()
            run = await self._storage.start_run(self._config.log_id, self._config.limit)
            await self._execute_run(run)
            elapsed = asyncio.get_running_loop().time() - cycle_started
            sleep_for = max(0.0, self._config.poll_interval_seconds - elapsed)
            if sleep_for:
                await asyncio.sleep(sleep_for)

    async def _execute_run(self, run: PollRun) -> None:
        fetched_count = 0
        inserted_count = 0
        duplicate_count = 0
        deposit_count = 0
        deposit_amount = 0
        try:
            logs = await self._client.fetch_user_logs(self._config.log_id, self._config.limit)
            fetched_count = len(logs)
            self._logger.info(
                "Fetched %s Torn logs for log_id=%s limit=%s",
                fetched_count,
                self._config.log_id,
                self._config.limit,
            )
            for log_entry in logs:
                inserted = await self._storage.store_log(log_entry, self._config.log_id)
                if inserted:
                    inserted_count += 1
                    self._log_log_entry(log_entry)
                    deposit_result = await self._storage.record_deposit_from_log(
                        log_entry,
                        item_id=self._config.deposit_item_id,
                        unit_value=self._config.deposit_unit_value,
                    )
                    if deposit_result is not None:
                        deposit_count += 1
                        deposit_amount += deposit_result.ledger_entry.delta
                        self._logger.info(
                            "Applied deposit log_id=%s user_id=%s qty=%s amount=%s before=%s after=%s",
                            deposit_result.ledger_entry.source_log_id,
                            deposit_result.ledger_entry.user_id,
                            deposit_result.ledger_entry.source_qty,
                            deposit_result.ledger_entry.delta,
                            deposit_result.before_balance,
                            deposit_result.after_balance,
                        )
                else:
                    duplicate_count += 1
                    self._logger.debug("Skipped duplicate Torn log id=%s", log_entry.id)

            await self._storage.finish_run(
                run,
                status="success",
                fetched_count=fetched_count,
                inserted_count=inserted_count,
                duplicate_count=duplicate_count,
                deposit_count=deposit_count,
                deposit_amount=deposit_amount,
            )
        except Exception as exc:  # pragma: no cover - logged and persisted
            self._logger.exception("Ingest run failed")
            await self._storage.finish_run(
                run,
                status="failed",
                fetched_count=fetched_count,
                inserted_count=inserted_count,
                duplicate_count=duplicate_count,
                deposit_count=deposit_count,
                deposit_amount=deposit_amount,
                error_message=str(exc),
            )

    def _log_log_entry(self, log_entry: TornLogEntry) -> None:
        payload: dict[str, Any] = {
            "log_id": log_entry.id,
            "timestamp": log_entry.timestamp,
            "details_id": log_entry.details_id,
            "title": log_entry.title,
            "category": log_entry.category,
            "sender": log_entry.sender,
            "item_count": len(log_entry.items),
            "deposit_item_qty": log_entry.total_qty_for_item(self._config.deposit_item_id),
        }
        self._logger.info("Stored Torn log %s", payload)
