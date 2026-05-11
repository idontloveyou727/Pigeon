from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TornItem:
    id: int
    qty: int
    uid: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TornItem":
        return cls(
            id=int(payload["id"]),
            qty=int(payload.get("qty", 0)),
            uid=_optional_int(payload.get("uid")),
        )


@dataclass(frozen=True)
class TornLogEntry:
    id: str
    timestamp: int
    details_id: int | None
    title: str | None
    category: str | None
    sender: int | None
    message: str | None
    items: list[TornItem]
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TornLogEntry":
        details = payload.get("details") or {}
        data = payload.get("data") or {}
        items_payload = data.get("items") or []

        return cls(
            id=str(payload["id"]),
            timestamp=int(payload["timestamp"]),
            details_id=_optional_int(details.get("id")),
            title=_optional_str(details.get("title")),
            category=_optional_str(details.get("category")),
            sender=_optional_int(data.get("sender")),
            message=_optional_str(data.get("message")),
            items=[TornItem.from_payload(item_payload) for item_payload in items_payload],
            raw=payload,
        )

    def total_qty_for_item(self, item_id: int) -> int:
        return sum(item.qty for item in self.items if item.id == item_id)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
