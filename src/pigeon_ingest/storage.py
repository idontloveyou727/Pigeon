from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid
from pathlib import Path

import aiosqlite

from .ledger import BalanceChangeResult, BalanceSnapshot, LedgerEntry
from .models import TornLogEntry


@dataclass(frozen=True)
class PollRun:
    id: int


class IngestStorage:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._database_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON")
        await self._initialize_schema()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _initialize_schema(self) -> None:
        assert self._connection is not None
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_log_id INTEGER NOT NULL,
                limit_value INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                deposit_count INTEGER NOT NULL DEFAULT 0,
                deposit_amount INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_logs (
                id TEXT PRIMARY KEY,
                source_log_id INTEGER NOT NULL,
                log_timestamp INTEGER NOT NULL,
                details_id INTEGER,
                title TEXT,
                category TEXT,
                sender INTEGER,
                message TEXT,
                raw_json TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_log_items (
                log_id TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                uid INTEGER,
                sender INTEGER,
                PRIMARY KEY (log_id, item_index),
                FOREIGN KEY (log_id) REFERENCES raw_logs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_raw_logs_timestamp ON raw_logs(log_timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_raw_logs_details_id ON raw_logs(details_id);
            CREATE INDEX IF NOT EXISTS idx_raw_log_items_item_id ON raw_log_items(item_id);

            CREATE TABLE IF NOT EXISTS user_balances (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS balance_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_key TEXT NOT NULL UNIQUE,
                entry_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                source_log_id TEXT,
                source_item_id INTEGER,
                source_qty INTEGER,
                unit_value INTEGER,
                reason TEXT,
                actor TEXT,
                reference_entry_id INTEGER,
                reference_note TEXT,
                FOREIGN KEY (reference_entry_id) REFERENCES balance_ledger(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_ledger_reference_entry
                ON balance_ledger(reference_entry_id)
                WHERE reference_entry_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_balance_ledger_user_id ON balance_ledger(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_balance_ledger_entry_type ON balance_ledger(entry_type);
            """
        )
        await self._connection.commit()

    async def start_run(self, source_log_id: int, limit_value: int) -> PollRun:
        assert self._connection is not None
        started_at = _utc_now()
        cursor = await self._connection.execute(
            """
            INSERT INTO ingest_runs (source_log_id, limit_value, started_at, status)
            VALUES (?, ?, ?, 'running')
            """,
            (source_log_id, limit_value, started_at),
        )
        await self._connection.commit()
        return PollRun(id=cursor.lastrowid)

    async def finish_run(
        self,
        run: PollRun,
        *,
        status: str,
        fetched_count: int,
        inserted_count: int,
        duplicate_count: int,
        deposit_count: int,
        deposit_amount: int,
        error_message: str | None = None,
    ) -> None:
        assert self._connection is not None
        await self._connection.execute(
            """
            UPDATE ingest_runs
            SET finished_at = ?, status = ?, fetched_count = ?, inserted_count = ?, duplicate_count = ?, deposit_count = ?, deposit_amount = ?, error_message = ?
            WHERE id = ?
            """,
            (
                _utc_now(),
                status,
                fetched_count,
                inserted_count,
                duplicate_count,
                deposit_count,
                deposit_amount,
                error_message,
                run.id,
            ),
        )
        await self._connection.commit()

    async def store_log(self, log_entry: TornLogEntry, source_log_id: int) -> bool:
        assert self._connection is not None
        received_at = _utc_now()
        cursor = await self._connection.execute(
            """
            INSERT INTO raw_logs (
                id, source_log_id, log_timestamp, details_id, title, category, sender, message, raw_json, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                log_entry.id,
                source_log_id,
                log_entry.timestamp,
                log_entry.details_id,
                log_entry.title,
                log_entry.category,
                log_entry.sender,
                log_entry.message,
                json.dumps(log_entry.raw, ensure_ascii=False, separators=(",", ":")),
                received_at,
            ),
        )
        inserted = cursor.rowcount == 1
        if inserted:
            await self._store_items(log_entry)
            await self._connection.commit()
        return inserted

    async def _store_items(self, log_entry: TornLogEntry) -> None:
        assert self._connection is not None
        for item_index, item in enumerate(log_entry.items):
            await self._connection.execute(
                """
                INSERT INTO raw_log_items (log_id, item_index, item_id, qty, uid, sender)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    log_entry.id,
                    item_index,
                    item.id,
                    item.qty,
                    item.uid,
                    log_entry.sender,
                ),
            )

    async def get_balance(self, user_id: int) -> BalanceSnapshot:
        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT user_id, balance, updated_at
            FROM user_balances
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            current_time = _utc_now()
            return BalanceSnapshot(user_id=user_id, balance=0, updated_at=current_time)

        return BalanceSnapshot(
            user_id=int(row["user_id"]),
            balance=int(row["balance"]),
            updated_at=str(row["updated_at"]),
        )

    async def list_recent_ledger(self, limit: int = 10) -> list[LedgerEntry]:
        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT id, entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                   source_qty, unit_value, reason, actor, reference_entry_id, reference_note
            FROM balance_ledger
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [_ledger_entry_from_row(row) for row in rows]

    async def get_ledger_entry(self, entry_id: int) -> LedgerEntry | None:
        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT id, entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                   source_qty, unit_value, reason, actor, reference_entry_id, reference_note
            FROM balance_ledger
            WHERE id = ?
            """,
            (entry_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _ledger_entry_from_row(row)

    async def record_deposit_from_log(
        self,
        log_entry: TornLogEntry,
        *,
        item_id: int,
        unit_value: int,
    ) -> BalanceChangeResult | None:
        return await self.record_deposit(
            source_log_id=log_entry.id,
            user_id=log_entry.sender,
            qty=log_entry.total_qty_for_item(item_id),
            item_id=item_id,
            unit_value=unit_value,
            actor="ingest-service",
            reason=f"Auto deposit for Torn item {item_id}",
        )

    async def record_deposit(
        self,
        *,
        source_log_id: str,
        user_id: int | None,
        qty: int,
        item_id: int,
        unit_value: int,
        actor: str,
        reason: str | None = None,
    ) -> BalanceChangeResult | None:
        assert self._connection is not None
        if user_id is None:
            return None

        if qty <= 0:
            return None

        amount = qty * unit_value
        entry_key = f"torn-log:{source_log_id}:item:{item_id}"
        current_time = _utc_now()
        before_balance = (await self.get_balance(user_id)).balance

        await self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self._connection.execute(
                """
                INSERT INTO balance_ledger (
                    entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                    source_qty, unit_value, reason, actor, reference_entry_id, reference_note
                )
                VALUES (?, 'deposit', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(entry_key) DO NOTHING
                """,
                (
                    entry_key,
                    user_id,
                    amount,
                    current_time,
                    source_log_id,
                    item_id,
                    qty,
                    unit_value,
                    reason or f"Deposit for Torn item {item_id}",
                    actor,
                ),
            )
            if cursor.rowcount != 1:
                await self._connection.rollback()
                return None

            await self._connection.execute(
                """
                INSERT INTO user_balances (user_id, balance, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance = balance + excluded.balance,
                    updated_at = excluded.updated_at
                """,
                (user_id, amount, current_time, current_time),
            )
            ledger_entry = await self._fetch_ledger_by_key(entry_key)
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise

        if ledger_entry is None:
            return None

        after_balance = before_balance + amount
        return BalanceChangeResult(
            ledger_entry=ledger_entry,
            before_balance=before_balance,
            after_balance=after_balance,
        )

    async def adjust_balance(
        self,
        *,
        user_id: int,
        delta: int,
        actor: str,
        reason: str | None = None,
        reference_entry_id: int | None = None,
        entry_type: str = "manual_adjustment",
        reference_note: str | None = None,
    ) -> BalanceChangeResult:
        assert self._connection is not None
        current_time = _utc_now()
        before_balance = (await self.get_balance(user_id)).balance
        entry_key = f"{entry_type}:{user_id}:{current_time}:{uuid.uuid4().hex}"

        await self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self._connection.execute(
                """
                INSERT INTO balance_ledger (
                    entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                    source_qty, unit_value, reason, actor, reference_entry_id, reference_note
                )
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    entry_key,
                    entry_type,
                    user_id,
                    delta,
                    current_time,
                    reason,
                    actor,
                    reference_entry_id,
                    reference_note,
                ),
            )
            ledger_id = cursor.lastrowid
            await self._connection.execute(
                """
                INSERT INTO user_balances (user_id, balance, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance = balance + excluded.balance,
                    updated_at = excluded.updated_at
                """,
                (user_id, delta, current_time, current_time),
            )
            ledger_entry = await self.get_ledger_entry(int(ledger_id))
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise

        if ledger_entry is None:
            raise RuntimeError("Ledger entry was not persisted")

        return BalanceChangeResult(
            ledger_entry=ledger_entry,
            before_balance=before_balance,
            after_balance=before_balance + delta,
        )

    async def rollback_ledger_entry(
        self,
        *,
        entry_id: int,
        actor: str,
        reason: str | None = None,
    ) -> BalanceChangeResult | None:
        target = await self.get_ledger_entry(entry_id)
        if target is None:
            return None

        existing_reversal = await self._fetch_reversal_for_entry(entry_id)
        if existing_reversal is not None:
            return None

        reversal_reason = reason or f"Rollback of ledger entry {entry_id}"
        return await self.adjust_balance(
            user_id=target.user_id,
            delta=-target.delta,
            actor=actor,
            reason=reversal_reason,
            reference_entry_id=target.id,
            entry_type="rollback",
            reference_note=target.entry_key,
        )

    async def _fetch_ledger_by_key(self, entry_key: str) -> LedgerEntry | None:
        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT id, entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                   source_qty, unit_value, reason, actor, reference_entry_id, reference_note
            FROM balance_ledger
            WHERE entry_key = ?
            """,
            (entry_key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _ledger_entry_from_row(row)

    async def _fetch_reversal_for_entry(self, entry_id: int) -> LedgerEntry | None:
        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT id, entry_key, entry_type, user_id, delta, created_at, source_log_id, source_item_id,
                   source_qty, unit_value, reason, actor, reference_entry_id, reference_note
            FROM balance_ledger
            WHERE reference_entry_id = ?
            """,
            (entry_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _ledger_entry_from_row(row)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_entry_from_row(row: aiosqlite.Row) -> LedgerEntry:
    return LedgerEntry(
        id=int(row["id"]),
        entry_key=str(row["entry_key"]),
        entry_type=str(row["entry_type"]),
        user_id=int(row["user_id"]),
        delta=int(row["delta"]),
        created_at=str(row["created_at"]),
        source_log_id=str(row["source_log_id"]) if row["source_log_id"] is not None else None,
        source_item_id=int(row["source_item_id"]) if row["source_item_id"] is not None else None,
        source_qty=int(row["source_qty"]) if row["source_qty"] is not None else None,
        unit_value=int(row["unit_value"]) if row["unit_value"] is not None else None,
        reason=str(row["reason"]) if row["reason"] is not None else None,
        actor=str(row["actor"]) if row["actor"] is not None else None,
        reference_entry_id=int(row["reference_entry_id"]) if row["reference_entry_id"] is not None else None,
        reference_note=str(row["reference_note"]) if row["reference_note"] is not None else None,
    )
