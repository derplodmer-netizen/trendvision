"""Signal engine against a fake BinoDex market: candles that trend, then settle."""
from sqlalchemy import select

from app import db
from app.binodex.market import BinaryPair, Candle
from app.models import Signal
from app.signal_engine import SignalEngine, stats


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeMarket:
    def __init__(self, clock, pairs, slope=0.00002):
        self.clock, self.pairs, self.slope = clock, pairs, slope
        self.chart_calls = 0
        self.override = {}            # asset -> price function of candle open time (ms)

    async def get_pairs(self):
        return self.pairs

    def price(self, aid, t_ms):
        f = self.override.get(aid)
        if f:
            return f(t_ms)
        n = (t_ms - 1_799_000_000_000) // 5000          # uptrend with pullbacks: +2, +2, -1.5 units
        return round(1.0 + 0.00001 * ((n // 3) * 2.5 + (0, 2, 4)[n % 3]), 6)

    async def get_chart(self, aid, interval, start_ms, limit):
        self.chart_calls += 1
        now_ms = int(self.clock() * 1000)
        last_open = now_ms - now_ms % 5000
        first = max(start_ms - start_ms % 5000, last_open - 5000 * (limit - 1))
        return [Candle(t, 0, 0, 0, self.price(aid, t)) for t in range(first, last_open + 1, 5000)]


def pair(aid, payout=88, otc=True, open_=True, tf=(15, 3600)):
    return BinaryPair(id=aid, symbol=f"P{aid}", type="currency", digits=5, payout=payout, max_payout=payout,
                      min_timeframe=tf[0], max_timeframe=tf[1], scheduled_until=0 if open_ else 1, is_otc=otc)


def engine(cfg, clock, market):
    return SignalEngine(market, cfg, wall=clock, mono=clock)


async def test_eligible_filters(cfg, database):
    clock = Clock()
    e = engine(cfg, clock, FakeMarket(clock, []))
    e.pairs = {p.id: p for p in [pair(1), pair(2, payout=70), pair(3, open_=False), pair(4, tf=(60, 3600)),
                                 pair(5, otc=False)]}
    assert [p.id for p in e.eligible(30)] == [1, 5]            # 4 does not allow 30 s
    assert [p.id for p in e.eligible(60)] == [1, 4, 5]
    cfg.signal_otc = "otc"
    assert [p.id for p in e.eligible(60)] == [1, 4]


def flat(t):
    return 1.0 + 0.0001 * ((t // 5000) % 2)                    # zig-zag: indicators do not agree


async def test_request_pair_ok_pending_and_settled(cfg, database):
    clock = Clock()
    m = FakeMarket(clock, [pair(1), pair(2)])
    e = engine(cfg, clock, m)
    r = await e.request(7, 1, 60)
    assert r.status == "ok" and r.signal.direction == "up" and r.signal.user_id == 7 and r.signal.source == "request"
    assert (await e.request(7, 1, 60)).status == "pending"     # one open signal per pair per user
    assert (await e.request(8, 1, 60)).status == "ok"          # other users are not limited by it
    assert (await e.request(7, 1, 45)).status == "bad_expiry"
    assert (await e.request(7, 99, 60)).status == "pair_unavailable"
    clock.t += 65
    await e.poll_all()
    rows = await e.settle()
    assert {x.result for x in rows} == {"win"}
    async with db.session() as s:
        assert (await stats(s, 7))["winrate"] == 100
    assert (await e.request(7, 1, 60)).status != "pending"     # settled: the pair is free again


async def test_request_no_entry_offers_alternative_and_auto(cfg, database):
    clock = Clock()
    m = FakeMarket(clock, [pair(1, payout=90), pair(2)])
    m.override[1] = flat
    e = engine(cfg, clock, m)
    await e.refresh_pairs()
    await e.poll(2)
    r = await e.request(7, 1, 60)
    assert r.status == "no_entry" and r.alternative.id == 2
    r = await e.request(7, None, 60)                            # auto skips the flat pair
    assert r.status == "ok" and r.signal.asset_id == 2
    m.override[2] = flat
    e2 = engine(cfg, Clock(clock.t + 1000), m)
    assert (await e2.request(9, None, 60)).status == "no_entry"
    async with db.session() as s:
        assert len((await s.execute(select(Signal))).scalars().all()) == 1     # nothing stored for "no entry"


async def test_broadcast_strong_only_with_daily_cap_and_gap(cfg, database):
    clock = Clock()
    m = FakeMarket(clock, [pair(1), pair(2)])
    e = engine(cfg, clock, m)
    sent = []

    async def on_sig(s):
        sent.append(s)
    e.on_signal = on_sig
    cfg.broadcast_min_strength = 0.99
    await e.step()
    assert sent == []                                           # not strong enough
    cfg.broadcast_min_strength = 0.3
    await e.step()
    assert len(sent) == 1 and sent[0].source == "broadcast" and sent[0].user_id is None
    await e.step()
    assert len(sent) == 1                                       # BROADCAST_GAP_MIN
    from datetime import timedelta
    for _ in range(5):
        async with db.session() as s:
            for row in (await s.execute(select(Signal))).scalars():
                row.created_at -= timedelta(minutes=cfg.broadcast_gap_min + 1)
            await s.commit()
        await e.step()
    assert len(sent) == cfg.broadcast_per_day                   # daily cap
    cfg.quiet_from, cfg.quiet_to = "00:00", "23:59"
    async with db.session() as s:
        for row in (await s.execute(select(Signal))).scalars():
            row.created_at -= timedelta(days=2)
        await s.commit()
    await e.step()
    assert len(sent) == cfg.broadcast_per_day                   # quiet hours: nothing, quota untouched


async def test_loss_refund_and_void(cfg, database):
    clock = Clock()
    m = FakeMarket(clock, [pair(1)])
    e = engine(cfg, clock, m)
    await e.refresh_pairs()
    await e.poll(1)
    now = clock()
    entry = e.quotes[1].current
    async with db.session() as s:
        for d in ("down", "up"):
            s.add(Signal(asset_id=1, symbol="P1", direction=d, expiry_sec=60, payout=88, strength=0.7,
                         entry_price=entry, entry_ts=now, expires_ts=now + 60))
        s.add(Signal(asset_id=9, symbol="P9", direction="up", expiry_sec=60, payout=88, strength=0.7,
                     entry_price=1.0, entry_ts=now, expires_ts=now + 60))
        await s.commit()
    m.override[1] = lambda t: entry                                     # price stands still
    clock.t += 65
    await e.poll(1)
    rows = await e.settle()
    assert sorted(r.result for r in rows) == ["refund", "refund"]
    clock.t += 200
    rows = await e.settle()
    assert [r.result for r in rows] == ["void"]                        # no quotes for asset 9 ever
