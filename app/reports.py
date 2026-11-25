"""Evening report to the owner (ADMIN_IDS) at REPORT_TIME: the funnel of the day by traffic source, the step with the
biggest drop, signal results — plus a ready "results of the day" picture to post in the channel."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.types import BufferedInputFile
from sqlalchemy import func, select

from . import db
from .config import Settings, get_settings
from .models import KV, Event, Signal

log = logging.getLogger("reports")

STEPS = [("start", "старт"), ("subscribed", "подписка"), ("registered", "регистрация"), ("ftd", "депозит")]


def day_bounds(cfg: Settings, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Local today 00:00 .. now, as naive UTC, and the day label."""
    tz = ZoneInfo(cfg.tz)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)      # noqa: E731
    return to_utc(start), to_utc(local), local.strftime("%d.%m")


async def funnel(s, since: datetime, until: datetime) -> dict[str, dict[str, int]]:
    rows = (await s.execute(select(Event.tag, Event.name, func.count(func.distinct(Event.user_id)))
                            .where(Event.created_at >= since, Event.created_at < until,
                                   Event.name.in_([k for k, _ in STEPS]))
                            .group_by(Event.tag, Event.name))).all()
    out: dict[str, dict[str, int]] = {}
    for tag, name, n in rows:
        out.setdefault(tag or "без метки", {})[name] = n
    return out


def _chain(c: dict[str, int]) -> str:
    return " → ".join(str(c.get(k, 0)) for k, _ in STEPS)


async def signal_numbers(s, since: datetime, until: datetime):
    rows = (await s.execute(select(Signal.symbol, Signal.is_otc, Signal.result).where(
        Signal.created_at >= since, Signal.created_at < until))).all()
    win = sum(r.result == "win" for r in rows)
    loss = sum(r.result == "loss" for r in rows)
    refund = sum(r.result == "refund" for r in rows)
    pairs: dict[str, list[int]] = {}
    for sym, otc, res in rows:
        if res in ("win", "loss"):
            name = sym + (" OTC" if otc and "OTC" not in sym.upper() else "")
            w_l = pairs.setdefault(name, [0, 0])
            w_l[0 if res == "win" else 1] += 1
    top = sorted(((n, w, l) for n, (w, l) in pairs.items()), key=lambda x: -(x[1] + x[2]))
    return len(rows), win, loss, refund, top


async def build(s, cfg: Settings, now: datetime | None = None) -> tuple[str, bytes | None]:
    since, until, day = day_bounds(cfg, now)
    by_tag = await funnel(s, since, until)
    total: dict[str, int] = {}
    for c in by_tag.values():
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    lines = [f"📊 <b>Отчёт Trend Radar за {day}</b>", "",
             "👥 <b>Воронка</b> (" + " → ".join(n for _, n in STEPS) + ")", f"Всего: <b>{_chain(total)}</b>"]
    for tag, c in sorted(by_tag.items(), key=lambda x: -x[1].get("start", 0))[:12]:
        lines.append(f"• {html.escape(tag)}: {_chain(c)}")
    drops = []
    for (a, an), (b, bn) in zip(STEPS, STEPS[1:]):
        if total.get(a, 0) >= 5:
            drops.append((1 - total.get(b, 0) / total[a], f"{an} → {bn}"))
    if drops:
        worst = max(drops)
        lines += ["", f"⚠️ Больше всего теряем: <b>{worst[1]}</b> — отвал {round(worst[0] * 100)}%"]
    n_all, win, loss, refund, top = await signal_numbers(s, since, until)
    lines += ["", f"📈 <b>Сигналы</b>: выдано {n_all}, с итогом {win + loss}: в плюс {win}, в минус {loss}"
              + (f" ({round(win / (win + loss) * 100)}%)" if win + loss else "")]
    pic = None
    if win + loss:
        from .cardgen import day_picture
        pic = await asyncio.to_thread(day_picture, day=day, win=win, loss=loss, refund=refund, pairs=top)
        lines += ["", "🖼 Ниже — картинка итогов дня для канала."]
    return "\n".join(lines), pic


async def send_report(bot, now: datetime | None = None) -> int:
    cfg = get_settings()
    if not cfg.admin_ids:
        return 0
    async with db.session() as s:
        text, pic = await build(s, cfg, now)
    n = 0
    for admin in cfg.admin_ids:
        try:
            await bot.send_message(admin, text, disable_web_page_preview=True)
            if pic:
                await bot.send_photo(admin, BufferedInputFile(pic, filename="day.jpg"),
                                     caption="Итоги дня Trend Radar — можно публиковать в канале.")
            n += 1
        except Exception as exc:                 # the owner has not started the bot yet, etc.
            log.warning("report to %s failed: %s", admin, exc)
    return n


async def reports_forever(bot, interval: float = 60.0) -> None:
    """Once a day at REPORT_TIME (local), remembered in the database so a restart does not resend it."""
    await asyncio.sleep(30)
    while True:
        try:
            cfg = get_settings()
            local = datetime.now(ZoneInfo(cfg.tz))
            today = local.strftime("%Y-%m-%d")
            if cfg.admin_ids and local.strftime("%H:%M") >= cfg.report_time:
                async with db.session() as s:
                    row = await s.get(KV, "report_day")
                    if row is None or row.value != today:
                        await send_report(bot)
                        if row is None:
                            s.add(KV(key="report_day", value=today))
                        else:
                            row.value = today
                        await s.commit()
        except Exception:
            log.exception("report failed")
        await asyncio.sleep(interval)
