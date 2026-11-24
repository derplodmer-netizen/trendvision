"""Signals from BinoDex quotes: pick pairs, keep 5-second candles, publish a signal when indicators agree,
and settle every signal by the BinoDex price at its expiry.

Honesty rules:
* a signal is published only when the indicator agreement reaches SIGNAL_MIN_STRENGTH; otherwise nothing;
* every published signal gets a result (win / loss / refund on an equal price), or "void" when quotes for the
  expiry moment never arrived — void signals are shown but not counted in the win rate;
* the result is computed from BinoDex 5-second candles: the close of the first candle that ends at or after
  the expiry moment (up to 5 s later than the exact expiry).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Awaitable, Callable

from sqlalchemy import func, select

from . import db
from .analysis import analyze
from .binodex.errors import BinoDexApiError, BinoDexError
from .binodex.market import BinaryPair, MarketClient
from .config import Settings
from .models import Signal, utcnow

log = logging.getLogger("signals")

CANDLE_INTERVAL = "5s"
CANDLE_MS = 5_000
WARMUP = 300                     # analyze() looks at the last 240 points
VOID_AFTER_SEC = 120             # no quote for the expiry moment this long after it -> void


@dataclass
class Quotes:
    closes: deque = field(default_factory=lambda: deque(maxlen=900))   # (close_ts_sec, price) of closed candles
    current: float | None = None                                       # forming candle close
    seen: float = 0.0                                                  # monotonic time of the last good answer
    last_open_ms: int = 0

    def prices(self) -> list[float]:
        return [p for _, p in self.closes]

    def first_at_or_after(self, ts: float) -> tuple[float, float] | None:
        for t, p in self.closes:
            if t >= ts:
                return t, p
        return None


@dataclass
class Read:
    direction: str
    strength: float
    rsi: float
    ema_fast: float
    ema_slow: float
    momentum: float


@dataclass
class Request:
    status: str                       # ok | pending | no_entry | no_quotes | pair_unavailable | no_pairs | bad_expiry
    signal: Signal | None = None
    alternative: BinaryPair | None = None


OnSignal = Callable[[Signal], Awaitable[None]]
OnSettled = Callable[[list[Signal]], Awaitable[None]]


class SignalEngine:
    def __init__(self, client: MarketClient, cfg: Settings, wall=time.time, mono=time.monotonic) -> None:
        self.client, self.cfg = client, cfg
        self.wall, self.mono = wall, mono
        self.pairs: dict[int, BinaryPair] = {}
        self.pairs_at = 0.0
        self.quotes: dict[int, Quotes] = {}
        self.pinned: dict[int, float] = {}          # asset -> wall time until which quotes are kept live
        self.backoff_until = 0.0
        self.on_signal: OnSignal | None = None
        self.on_settled: OnSettled | None = None

    # ------------------------------------------------------------------ pairs
    def eligible(self, expiry: int | None = None) -> list[BinaryPair]:
        c = self.cfg
        exp = expiry or min(c.expiries)
        out = []
        for p in self.pairs.values():
            if c.signal_otc == "otc" and not p.is_otc or c.signal_otc == "market" and p.is_otc:
                continue
            if p.market_open and p.allows_expiry(exp) and p.payout >= c.signal_min_payout:
                out.append(p)
        return sorted(out, key=lambda p: (-p.payout, p.id))

    async def refresh_pairs(self, force: bool = False) -> None:
        if self.pairs and not force and self.mono() - self.pairs_at < 60:
            return
        self.pairs = {p.id: p for p in await self.client.get_pairs()}
        self.pairs_at = self.mono()

    def watchlist(self) -> list[int]:
        now = self.wall()
        self.pinned = {a: t for a, t in self.pinned.items() if t > now}
        top = [p.id for p in self.eligible()[: self.cfg.signal_watchlist]]
        return list(dict.fromkeys([*top, *self.pinned]))

    def fresh(self, asset_id: int) -> bool:
        q = self.quotes.get(asset_id)
        return bool(q and q.closes and self.mono() - q.seen <= max(20.0, 3 * self.cfg.signal_poll_sec))

    # ------------------------------------------------------------------ quotes
    async def poll(self, asset_id: int) -> None:
        q = self.quotes.setdefault(asset_id, Quotes())
        n = WARMUP + 2 if not q.closes else 6
        now_ms = int(self.wall() * 1000)
        candles = await self.client.get_chart(asset_id, CANDLE_INTERVAL, now_ms - CANDLE_MS * n, n + 1)
        if not candles:
            return
        forming, closed = candles[-1], candles[:-1]
        q.current, q.seen = forming.c, self.mono()
        for c in closed:
            if c.t > q.last_open_ms:
                q.closes.append(((c.t + CANDLE_MS) / 1000, c.c))
                q.last_open_ms = c.t

    async def ensure(self, asset_id: int) -> bool:
        """Quotes for this pair, loaded now if needed (a pair the user picked outside the watchlist)."""
        if not self.fresh(asset_id) and self.wall() >= self.backoff_until:
            try:
                await self.poll(asset_id)
            except BinoDexError as exc:
                log.info("chart %s: %s", asset_id, exc.message)
        self.pinned[asset_id] = max(self.pinned.get(asset_id, 0), self.wall() + 20 * 60)
        return self.fresh(asset_id)

    async def poll_all(self) -> None:
        if self.wall() < self.backoff_until:
            return
        for aid in self.watchlist():
            try:
                await self.poll(aid)
            except BinoDexApiError as exc:
                if exc.is_rate_limited:
                    self.backoff_until = self.wall() + 30
                    log.warning("binodex chart rate-limited: pause 30s")
                    return
                log.info("chart %s: %s", aid, exc.message)
            except BinoDexError as exc:
                log.info("chart %s: %s", aid, exc.message)

    # ------------------------------------------------------------------ analysis
    def read(self, p: BinaryPair):
        """(direction, strength) or None when there is not enough data."""
        if not self.fresh(p.id):
            return None
        sig = analyze(p.symbol, self.quotes[p.id].prices())
        if sig is None:
            return None
        return Read("up" if sig.direction == "buy" else "down", round(abs(sig.score), 3), sig.rsi,
                    sig.ema_fast, sig.ema_slow, sig.momentum)

    def best(self, pairs: list[BinaryPair], min_strength: float, exclude: set[int] = frozenset()):
        found = None
        for p in pairs:
            if p.id in exclude:
                continue
            r = self.read(p)
            if r and r.strength >= min_strength and (found is None or r.strength > found[1].strength):
                found = (p, r)
        return found

    async def _store(self, s, p: BinaryPair, r, expiry: int, source: str, user_id: int | None) -> Signal:
        now = self.wall()
        q = self.quotes[p.id]
        row = Signal(source=source, user_id=user_id, asset_id=p.id, symbol=p.symbol, is_otc=p.is_otc,
                     direction=r.direction, expiry_sec=expiry, payout=p.payout, strength=r.strength, rsi=r.rsi,
                     ema_fast=r.ema_fast, ema_slow=r.ema_slow, momentum=r.momentum,
                     entry_price=q.current if q.current is not None else q.closes[-1][1],
                     entry_ts=now, expires_ts=now + expiry)
        s.add(row)
        await s.flush()
        self.pinned[p.id] = max(self.pinned.get(p.id, 0), now + expiry + VOID_AFTER_SEC + 30)  # keep until settled
        log.info("signal %s %s %s %s %ss strength %.2f", row.id, source, row.symbol, row.direction, expiry, row.strength)
        return row

    async def request(self, user_id: int, asset_id: int | None, expiry: int) -> Request:
        """A user asked for a signal. auto (asset_id None) = the pair with the clearest entry right now."""
        c = self.cfg
        if expiry not in c.expiries:
            return Request("bad_expiry")
        try:
            await self.refresh_pairs()
        except BinoDexError as exc:
            log.warning("pairs: %s", exc.message)
        pairs = self.eligible(expiry)
        if not pairs:
            return Request("no_pairs")
        async with db.session() as s:
            mine = (await s.execute(select(Signal).where(Signal.user_id == user_id, Signal.result == "pending")
                                    .order_by(Signal.id.desc()))).scalars().all()
            if asset_id is None:
                for p in pairs[: c.signal_watchlist]:
                    await self.ensure(p.id)
                hit = self.best(pairs, c.signal_min_strength, exclude={m.asset_id for m in mine})
                if hit is None:
                    return Request("no_entry")
                row = await self._store(s, hit[0], hit[1], expiry, "request", user_id)
                await s.commit()
                return Request("ok", row)
            p = next((x for x in pairs if x.id == asset_id), None)
            if p is None:
                return Request("pair_unavailable")
            same = next((m for m in mine if m.asset_id == asset_id), None)
            if same is not None:
                return Request("pending", same)          # one open signal per pair per user
            if not await self.ensure(p.id):
                return Request("no_quotes")
            r = self.read(p)
            if r and r.strength >= c.signal_min_strength:
                row = await self._store(s, p, r, expiry, "request", user_id)
                await s.commit()
                return Request("ok", row)
            alt = self.best(pairs, c.signal_min_strength, exclude={asset_id} | {m.asset_id for m in mine})
            return Request("no_entry", alternative=alt[0] if alt else None)

    # ------------------------------------------------------------------ broadcast
    async def broadcast(self) -> Signal | None:
        """A strong entry for everyone with notifications on: at most BROADCAST_PER_DAY, BROADCAST_GAP_MIN apart."""
        c = self.cfg
        if not c.broadcast_enabled or c.broadcast_expiry_sec < 15 or quiet_now(c):
            return None          # nothing is spent from the daily quota at night
        async with db.session() as s:
            day_ago = utcnow() - timedelta(hours=24)
            rows = (await s.execute(select(Signal.created_at).where(Signal.source == "broadcast",
                                                                    Signal.created_at >= day_ago))).scalars().all()
            if len(rows) >= c.broadcast_per_day:
                return None
            if rows and max(rows) > utcnow() - timedelta(minutes=c.broadcast_gap_min):
                return None
            hit = self.best(self.eligible(c.broadcast_expiry_sec), c.broadcast_min_strength)
            if hit is None:
                return None
            row = await self._store(s, hit[0], hit[1], c.broadcast_expiry_sec, "broadcast", None)
            await s.commit()
            return row

    # ------------------------------------------------------------------ settlement
    async def settle(self) -> list[Signal]:
        now = self.wall()
        done = []
        async with db.session() as s:
            rows = (await s.execute(select(Signal).where(Signal.result == "pending",
                                                         Signal.expires_ts <= now))).scalars().all()
            for r in rows:
                q = self.quotes.get(r.asset_id)
                hit = q.first_at_or_after(r.expires_ts) if q else None
                if hit is not None and hit[0] - r.expires_ts <= 30:
                    r.close_price = hit[1]
                    path = [px for t, px in q.closes if r.entry_ts < t <= hit[0]] or [hit[1]]
                    sign = 1 if r.direction == "up" else -1
                    moves = [sign * (px - r.entry_price) / r.entry_price for px in path] if r.entry_price else [0.0]
                    r.best_move, r.worst_move = max(0.0, max(moves)), max(0.0, -min(moves))
                    if hit[1] == r.entry_price:
                        r.result = "refund"
                    else:
                        up = hit[1] > r.entry_price
                        r.result = "win" if up == (r.direction == "up") else "loss"
                elif now - r.expires_ts > VOID_AFTER_SEC:
                    r.result = "void"
                else:
                    continue
                r.settled_at = utcnow()
                done.append(r)
            await s.commit()
        return done

    # ------------------------------------------------------------------ loop
    async def step(self) -> None:
        try:
            await self.refresh_pairs()
        except BinoDexError as exc:
            log.warning("pairs: %s", exc.message)
        await self.poll_all()
        settled = await self.settle()
        if settled and self.on_settled:
            await self.on_settled(settled)
        new = await self.broadcast()
        if new is not None and self.on_signal:
            await self.on_signal(new)

    async def run_forever(self) -> None:
        import asyncio
        while True:
            started = self.mono()
            try:
                await self.step()
            except Exception:
                log.exception("signal step failed")
            await asyncio.sleep(max(1.0, self.cfg.signal_poll_sec - (self.mono() - started)))


def quiet_now(cfg: Settings, now=None) -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo(cfg.tz))
    hm = now.strftime("%H:%M")
    a, b = cfg.quiet_from, cfg.quiet_to
    return (a <= hm or hm < b) if a > b else (a <= hm < b)


# ---------------------------------------------------------------------- stats
async def stats(s, days: int | None, asset_id: int | None = None) -> dict:
    q = select(Signal.result, func.count()).group_by(Signal.result)
    if days is not None:
        q = q.where(Signal.created_at >= utcnow() - timedelta(days=days))
    if asset_id is not None:
        q = q.where(Signal.asset_id == asset_id)
    c = dict((await s.execute(q)).all())
    win, loss = c.get("win", 0), c.get("loss", 0)
    return {"win": win, "loss": loss, "refund": c.get("refund", 0), "void": c.get("void", 0),
            "pending": c.get("pending", 0), "winrate": round(win / (win + loss) * 100) if win + loss else None}


async def today_stats(s, tz: str) -> dict:
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    local = datetime.now(ZoneInfo(tz))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    c = dict((await s.execute(select(Signal.result, func.count()).where(Signal.created_at >= start)
                              .group_by(Signal.result))).all())
    win, loss = c.get("win", 0), c.get("loss", 0)
    return {"win": win, "loss": loss, "winrate": round(win / (win + loss) * 100) if win + loss else None}


async def recent(s, n: int = 10, settled_only: bool = True) -> list[Signal]:
    q = select(Signal).order_by(Signal.id.desc()).limit(n)
    if settled_only:
        q = q.where(Signal.result.in_(("win", "loss", "refund")))
    return list((await s.execute(q)).scalars())
