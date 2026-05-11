from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    entry_key: str
    entry_type: str
    user_id: int
    delta: int
    created_at: str
    source_log_id: str | None = None
    source_item_id: int | None = None
    source_qty: int | None = None
    unit_value: int | None = None
    reason: str | None = None
    actor: str | None = None
    reference_entry_id: int | None = None
    reference_note: str | None = None


@dataclass(frozen=True)
class BalanceChangeResult:
    ledger_entry: LedgerEntry
    before_balance: int
    after_balance: int


@dataclass(frozen=True)
class BalanceSnapshot:
    user_id: int
    balance: int
    updated_at: str