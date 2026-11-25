"""Follow-up reminders for people who stopped on a funnel step.

Chains (minutes after the moment the person reached the step):
    start — pressed Start, not subscribed to the channel:     15 min, 3 h
    reg   — subscribed (bot open), not registered on BinoDex:  1 h, 1 day, 3 days
    dep   — account confirmed, no deposit yet:                 30 min, 6 h, 1 day, 3 days, 7 days

Rules: a chain stops as soon as the step is done (the person moves to another stage); at most
REMINDERS_PER_DAY reminders per person per 24 h; nothing in quiet hours; when several reminders became due
at once (night, downtime) only the latest is sent and the older ones are marked skipped. Each reminder re-shows
the person's current step with the reminder text on top, so the buttons to act are right there.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from . import db
from .config import Settings, get_settings
from .models import ReminderLog, User, utcnow
from .services import log_event
from .signal_engine import quiet_now, stats
from .texts import get_text

log = logging.getLogger("reminders")

CHAINS: dict[str, list[tuple[int, str]]] = {
    "start": [(15, "rem_start_1"), (180, "rem_start_2")],
    "reg": [(60, "rem_reg_1"), (1440, "rem_reg_2"), (4320, "rem_reg_3")],
    "dep": [(30, "rem_dep_1"), (360, "rem_dep_2"), (1440, "rem_dep_3"), (4320, "rem_dep_4"), (10080, "rem_dep_5")],
}


def stage(u: User, cfg: Settings) -> tuple[str, datetime] | None:
    """Which chain applies to this person now, and since when."""
    if u.blocked:
        return None
    if u.step == "welcome":
        if cfg.gate and u.subscribed_at is None:
            return "start", u.created_at
        return "reg", u.created_at
    if u.step in ("value", "register", "manual"):
        return "reg", u.subscribed_at or u.created_at
    if u.step == "deposit" and u.ftd_at is None:
        return "dep", u.linked_at or u.registered_at or u.created_at
    return None


def due(chain: str, since: datetime, now: datetime, done: set[str]) -> tuple[str | None, list[str]]:
    """(the reminder to send now, older ones to mark skipped)."""
    ready = [key for minutes, key in CHAINS[chain] if key not in done and since + timedelta(minutes=minutes) <= now]
    if not ready:
        return None, []
    return ready[-1], ready[:-1]


async def stats_line(s) -> str:
    st = await stats(s, 1)
    n = st["win"] + st["loss"]
    if not n:
        return ""
    return f"\n\n📊 За последние сутки: {st['win']} из {n} сигналов в плюс."


async def run_once(bot, now: datetime | None = None) -> int:
    """One pass over everyone who is mid-funnel. Returns how many reminders were sent."""
    from .bot import show                      # late import: bot.py imports this module's caller
    cfg = get_settings()
    if not cfg.reminders_enabled or quiet_now(cfg):
        return 0
    now = now or utcnow()
    sent = 0
    async with db.session() as s:
        line = await stats_line(s)
        users = (await s.execute(select(User).where(User.blocked.is_(False), User.step.in_(
            ("welcome", "value", "register", "manual", "deposit"))))).scalars().all()
        for u in users:
            st = stage(u, cfg)
            if st is None:
                continue
            chain, since = st
            done = set((await s.execute(select(ReminderLog.key).where(ReminderLog.user_id == u.id))).scalars())
            key, skipped = due(chain, since, now, done)
            if key is None:
                continue
            last_day = (await s.execute(select(func.count()).select_from(ReminderLog).where(
                ReminderLog.user_id == u.id, ReminderLog.skipped.is_(False),
                ReminderLog.sent_at >= now - timedelta(hours=24)))).scalar() or 0
            if last_day >= cfg.reminders_per_day:
                continue
            for k in skipped:
                s.add(ReminderLog(user_id=u.id, key=k, sent_at=now, skipped=True))
            note = await get_text(s, u.lang, key, u.ab, stats_line=line, min_dep=f"{cfg.min_deposit_usd:g}")
            screen = "register" if u.step == "manual" else u.step
            await show(bot, s, cfg, u, screen, note=note)
            s.add(ReminderLog(user_id=u.id, key=key, sent_at=now, skipped=False))
            if not u.blocked:
                await log_event(s, u, key)
                sent += 1
            await s.commit()
            await asyncio.sleep(0.05)
    sent += await retention(bot, cfg, now)
    if sent:
        log.info("reminders sent: %d", sent)
    return sent


IDLE = [(3, "rem_idle_1"), (7, "rem_idle_2"), (14, "rem_idle_3")]     # days without opening the bot


async def _sent_today(s, user_id: int, now: datetime) -> int:
    return (await s.execute(select(func.count()).select_from(ReminderLog).where(
        ReminderLog.user_id == user_id, ReminderLog.skipped.is_(False),
        ReminderLog.sent_at >= now - timedelta(hours=24)))).scalar() or 0


async def retention(bot, cfg: Settings, now: datetime) -> int:
    """Users with access: bring back the inactive ones; suggest a top-up when the BinoDex balance runs low."""
    from .bot import partner, show
    from .services import has_low_balance
    sent = 0
    async with db.session() as s:
        line = await stats_line(s)
        users = (await s.execute(select(User).where(User.step == "access", User.blocked.is_(False)))).scalars().all()
        checks = 0
        for u in users:
            if await _sent_today(s, u.id, now) >= cfg.reminders_per_day:
                continue
            # 1) inactive: a new cycle starts every time the person comes back (keys carry the last-seen date)
            cycle = u.last_seen.strftime("%y%m%d")
            done = set((await s.execute(select(ReminderLog.key).where(ReminderLog.user_id == u.id,
                                                                       ReminderLog.key.like(f"rem_idle_%@{cycle}")))).scalars())
            ready = [f"{k}@{cycle}" for d, k in IDLE if f"{k}@{cycle}" not in done
                     and u.last_seen + timedelta(days=d) <= now]
            if ready:
                for k in ready[:-1]:
                    s.add(ReminderLog(user_id=u.id, key=k, sent_at=now, skipped=True))
                key = ready[-1]
                note = await get_text(s, u.lang, key.split("@")[0], u.ab, stats_line=line)
                await show(bot, s, cfg, u, "menu", note=note)
                s.add(ReminderLog(user_id=u.id, key=key, sent_at=now))
                await log_event(s, u, key.split("@")[0])
                sent += 1
                await s.commit()
                continue
            # 2) low balance: Partner API trader card, at most every 12 h per person and a few calls per pass
            if partner is None or not u.acc_id or u.acc_id in cfg.test_map or checks >= 20:
                continue
            if u.checked_at is not None and u.checked_at > now - timedelta(hours=12):
                continue
            checks += 1
            low = await has_low_balance(s, u, partner, cfg.low_balance_usd)
            if not low:
                await s.commit()
                continue
            recent = (await s.execute(select(func.count()).select_from(ReminderLog).where(
                ReminderLog.user_id == u.id, ReminderLog.key.like("lowbal@%"),
                ReminderLog.sent_at >= now - timedelta(days=cfg.low_balance_every_days)))).scalar()
            if recent:
                await s.commit()
                continue
            note = await get_text(s, u.lang, "rem_lowbal", u.ab, low=f"{cfg.low_balance_usd:g}")
            await show(bot, s, cfg, u, "menu", note=note)
            s.add(ReminderLog(user_id=u.id, key=f"lowbal@{now:%y%m%d}", sent_at=now))
            await log_event(s, u, "rem_lowbal")
            sent += 1
            await s.commit()
            await asyncio.sleep(0.3)
    return sent


async def reminders_forever(bot, interval: float = 60.0) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_once(bot)
        except Exception:
            log.exception("reminders failed")
        await asyncio.sleep(interval)
