"""BinoDex Partner API (binopartner.com, read-only): the same numbers as the partner cabinet.

Docs: https://binopartner.com/ru/admin/api-docs. Every call is a GET with `Authorization: Bearer <binp_… key>`;
the answer is `{"code", "message", "data"}`. Counts only (no deposit sums), plus one trader's own balance.

Used for:
- the admin overview: clicks, registrations, FTD and deposits overall and today, as in the cabinet;
- one user's card: is his BinoDex account really attributed to this partner, his deposits and trades.

The key lives only in the server's .env (BINODEX_PARTNER_API_KEY) and is never sent to the mini app.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("binodex.partner")
DEFAULT_URL = "https://api.binopartner.com/v1"


class PartnerApiError(Exception):
    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.message, self.status, self.reason = message, status, reason


class PartnerApi:
    def __init__(self, key: str, base_url: str = DEFAULT_URL, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None, cache_sec: float = 60) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                       headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        self.cache_sec = cache_sec
        self._cache: dict[str, tuple[float, object]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, cache: bool = False):
        if cache:
            hit = self._cache.get(path)
            if hit and time.monotonic() - hit[0] < self.cache_sec:
                return hit[1]
        try:
            r = await self._http.get(path)
        except httpx.HTTPError as exc:
            raise PartnerApiError(f"партнёрский API недоступен: {exc.__class__.__name__}") from exc
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            raise PartnerApiError(str(body.get("message") or f"HTTP {r.status_code}"), r.status_code,
                                  str(body.get("reason") or ""))
        data = body.get("data") if isinstance(body, dict) else None
        if cache:
            self._cache[path] = (time.monotonic(), data)
        return data

    async def overall(self) -> dict:
        return await self._get("/partner-api/stats/overall", cache=True) or {}

    async def today(self) -> dict:
        return await self._get("/partner-api/stats/today", cache=True) or {}

    async def ref_links(self) -> list:
        return await self._get("/partner-api/ref", cache=True) or []

    async def trader(self, binodex_user_id: int) -> dict | None:
        """One referred trader; None when BinoDex says he is not attributed to this partner (404)."""
        try:
            return await self._get(f"/partner-api/stats/trader/{int(binodex_user_id)}")
        except PartnerApiError as exc:
            if exc.status == 404:
                return None
            raise
