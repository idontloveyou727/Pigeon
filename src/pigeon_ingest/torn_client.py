from __future__ import annotations

from collections.abc import Sequence

import httpx

from .models import TornLogEntry


class TornClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={
                "accept": "application/json",
                "Authorization": f"ApiKey {api_key}",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_user_logs(self, log_id: int, limit: int) -> list[TornLogEntry]:
        response = await self._client.get("/user/log", params={"log": log_id, "limit": limit})
        response.raise_for_status()
        payload = response.json()
        raw_logs: Sequence[dict] = payload.get("log") or []
        return [TornLogEntry.from_payload(log_payload) for log_payload in raw_logs]
