"""Public market endpoints of the BinoDex Broker API (no user token, no client secret needed):

    GET /broker/pairs/binary   list of binary pairs with payout, timeframes and schedule
    GET /broker/chart          candles (5s interval is the one confirmed live)

Shapes are copied from Kairo's BinoDex integration (taken there from the official broker-web).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import BinoDexApiError, BinoDexBadResponse, BinoDexUnavailable

log = logging.getLogger("binodex")

DEFAULT_API_URL = "https://api.binodex.app/v1"
MIN_TRADE_SEC = 15


def _req(data: Any, key: str, kind: type | tuple[type, ...], where: str):
    if not isinstance(data, dict) or key not in data:
        raise BinoDexBadResponse(f"{where}: field '{key}' is missing")
    value = data[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise BinoDexBadResponse(f"{where}: field '{key}' has type {type(value).__name__}")
    return value


@dataclass(frozen=True)
class BinaryPair:
    """BinaryPair in broker-web: element of GET /broker/pairs/binary.

    Live API (2026-11-03): market pairs have no `is_otc` key at all, OTC pairs carry
    `is_otc: true`, and the same symbol (e.g. EUR/USD) exists as both with different ids.
    `id` is the only unique key.
    """
    id: int
    symbol: str
    type: str
    digits: int
    payout: int
    max_payout: int
    min_timeframe: int
    max_timeframe: int
    scheduled_until: int
    is_otc: bool

    @classmethod
    def parse(cls, data: Any) -> "BinaryPair":
        where = "pairs/binary"
        return cls(id=_req(data, "id", int, where), symbol=_req(data, "symbol", str, where),
                   type=str(data.get("type") or ""), digits=int(data.get("digits") or 0),
                   payout=int(_req(data, "payout", float, where)),
                   max_payout=int(data.get("max_payout") or data["payout"]),
                   min_timeframe=int(_req(data, "min_timeframe", float, where)),
                   max_timeframe=int(_req(data, "max_timeframe", float, where)),
                   scheduled_until=int(data.get("scheduled_until") or 0),
                   is_otc=data.get("is_otc") is True)

    @property
    def market_open(self) -> bool:
        # broker-web trade-room.tsx: marketOpen = scheduled_until === 0
        return self.scheduled_until == 0

    def allows_expiry(self, seconds: int) -> bool:
        # Broker API doc: "The shortest OTC window is 15 seconds; a shorter one is refused", although the
        # live list showed min_timeframe 5 on OTC pairs (2026-11-03). The stricter of the two wins.
        return max(self.min_timeframe, MIN_TRADE_SEC) <= seconds <= self.max_timeframe


@dataclass(frozen=True)
class Candle:
    """[open_time_ms, open, high, low, close] — live API sends arrays without volume."""
    t: int
    o: float
    h: float
    l: float  # noqa: E741
    c: float


def parse_candles(data: Any) -> list[Candle]:
    if isinstance(data, dict):
        for key in ("candles", "data", "klines"):          # same fallbacks as broker-web lib/chart.ts
            if isinstance(data.get(key), list):
                return parse_candles(data[key])
        raise BinoDexBadResponse("chart: expected an array of candles")
    if not isinstance(data, list):
        raise BinoDexBadResponse("chart: expected an array of candles")
    out: list[Candle] = []
    for row in data:
        try:
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                t, o, h, lo, c = (float(x) for x in row[:5])
            elif isinstance(row, dict):
                t, o, h, lo, c = (float(row.get(a, row.get(b))) for a, b in
                                  (("t", "time"), ("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")))
            else:
                continue
        except (TypeError, ValueError):
            continue
        if c > 0 and t > 0:
            out.append(Candle(int(t), o, h, lo, c))
    out.sort(key=lambda x: x.t)
    return out


class MarketClient:
    def __init__(self, api_url: str = DEFAULT_API_URL, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=(api_url or DEFAULT_API_URL).rstrip("/"), timeout=timeout,
                                       transport=transport, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            res = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise BinoDexUnavailable("BinoDex недоступен") from exc
        try:
            data = res.json()
        except ValueError:
            data = None
        if res.is_success:
            if data is None:
                raise BinoDexBadResponse(f"{path}: non-JSON response")
            return data
        err = data.get("error") if isinstance(data, dict) else None
        message = (err.get("message") if isinstance(err, dict) else None) or f"Broker API {res.status_code}"
        if res.status_code >= 500:
            raise BinoDexUnavailable(message)
        raise BinoDexApiError(str(message), res.status_code, err.get("details") if isinstance(err, dict) else None)

    async def get_pairs(self) -> list[BinaryPair]:
        data = await self._get("/broker/pairs/binary")
        if not isinstance(data, list):
            raise BinoDexBadResponse("pairs/binary: expected an array")
        out = []
        for row in data:
            try:
                out.append(BinaryPair.parse(row))
            except BinoDexBadResponse as exc:
                log.warning("binodex pair skipped: %s", exc)
        return out

    async def get_chart(self, asset_id: int, interval: str, start_time_ms: int, limit: int) -> list[Candle]:
        params = {"asset_id": str(asset_id), "interval": interval, "limit": str(limit),
                  "start_time": str(int(start_time_ms))}
        return parse_candles(await self._get("/broker/chart", params=params))
