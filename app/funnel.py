"""Screens: (text, inline keyboard) per step, and signal / result messages. Pure rendering, no sending."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as KB

from .config import Settings
from .models import Signal, User
from .services import ref_link
from .signal_engine import recent, stats, today_stats
from .texts import get_text

ARROW = {"up": "⬆️", "down": "⬇️"}
ICON = {"win": "✅", "loss": "❌", "refund": "↩️", "void": "⚪️"}
WORD = {"win": "в плюс", "loss": "в минус", "refund": "возврат (цена не изменилась)", "void": "нет котировок — не учитывается"}


def _kb(rows: list[list[B]]) -> KB:
    return KB(inline_keyboard=[r for r in rows if r])


async def _btn(s, u: User, key: str, data: str | None = None, url: str | None = None) -> B:
    label = await get_text(s, u.lang, key, u.ab)
    return B(text=label, url=url) if url else B(text=label, callback_data=data)


def expiry_word(sec: int) -> str:
    return f"{sec // 60} мин" if sec % 60 == 0 else f"{sec} с"


def strength_word(x: float) -> str:
    return "сильное" if x >= 0.6 else "умеренное"


def local_time(ts: float, tz: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz)).strftime("%H:%M:%S")


def fmt_rate(st: dict) -> str:
    n = st["win"] + st["loss"]
    if not n:
        return "пока нет сигналов с итогом"
    return f"{st['winrate']}% в плюс ({st['win']} из {n})"


def pair_name(sig: Signal) -> str:
    return html.escape(sig.symbol + (" OTC" if sig.is_otc and "OTC" not in sig.symbol.upper() else ""))


def result_line(sig: Signal) -> str:
    return f"{ICON.get(sig.result, '')} {pair_name(sig)} {ARROW[sig.direction]} {expiry_word(sig.expiry_sec)}"


async def signal_text(s, cfg: Settings, u: User, sig: Signal) -> str:
    return await get_text(s, u.lang, "signal", u.ab, arrow=ARROW[sig.direction], symbol=pair_name(sig),
                          dir_word="вверх" if sig.direction == "up" else "вниз",
                          expiry=expiry_word(sig.expiry_sec), payout=sig.payout,
                          until=local_time(sig.entry_ts + cfg.signal_valid_sec, cfg.tz),
                          strength=strength_word(sig.strength), risk_short="Риск потери средств, 18+.")


async def signal_kb(s, cfg: Settings, u: User) -> KB:
    if u.step != "access":           # the free signal: next step is registration, not another signal
        link = ref_link(cfg, u)
        return _kb([[await _btn(s, u, "btn_join", "go:join:free")],
                    [await _btn(s, u, "btn_register", url=link)] if link and not u.acc_id else []])
    rows = [[await _btn(s, u, "btn_open", url=cfg.open_url)] if cfg.open_url else [],
            [await _btn(s, u, "btn_more", "sig:new")]]
    return _kb(rows)


async def result_kb(s, cfg: Settings, u: User) -> KB | None:
    return None if u.step == "access" else _kb([[await _btn(s, u, "btn_join", "go:join:free")]])


async def trial_kb(s, cfg: Settings, u: User) -> KB:
    """Under a broadcast for someone without access: join the funnel, register, or stop these pushes."""
    link = ref_link(cfg, u)
    rows = [[await _btn(s, u, "btn_join", "go:join")],
            [await _btn(s, u, "btn_register", url=link)] if link and not u.acc_id else [],
            [await _btn(s, u, "btn_mute", "sig:mute")]]
    return _kb(rows)


async def pair_picker(s, cfg: Settings, u: User, pairs) -> tuple[str, KB]:
    rows = [[await _btn(s, u, "btn_auto", "rq:p:auto")]]
    line = []
    for p in pairs[: cfg.signal_pairs_shown]:
        label = p.symbol + (" OTC" if p.is_otc and "OTC" not in p.symbol.upper() else "") + f" · {p.payout}%"
        line.append(B(text=label, callback_data=f"rq:p:{p.id}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    rows.append(line)
    rows.append([await _btn(s, u, "btn_back", "go:back")])
    return await get_text(s, u.lang, "pick_pair", u.ab), _kb(rows)


async def expiry_picker(s, cfg: Settings, u: User, key: str, pair_label: str, note: str = "") -> tuple[str, KB]:
    row = [B(text=expiry_word(e), callback_data=f"rq:e:{key}:{e}") for e in cfg.expiries]
    text = await get_text(s, u.lang, "pick_expiry", u.ab, pair=html.escape(pair_label))
    return (note + "\n\n" + text if note else text), _kb([row, [await _btn(s, u, "btn_other_pair", "sig:new"),
                                                                   await _btn(s, u, "btn_back", "go:back")]])


def _px(x: float) -> str:
    return f"{x:.6g}" if x < 1000 else f"{x:.2f}"


def _pct(x: float) -> str:
    return f"{x * 100:.3f}".rstrip("0").rstrip(".") + "%"


def explain(sig: Signal) -> str:
    """Short, factual explanation built only from what was stored: indicators at entry and the price path.
    It says what happened, never claims to know the market's 'real' reason."""
    up = sig.direction == "up"
    trend_up = sig.ema_fast > sig.ema_slow
    with_trend = trend_up == up
    lines = []
    trend = ("быстрая средняя (EMA 8) выше медленной (EMA 21) — короткий тренд вверх" if trend_up
             else "быстрая средняя (EMA 8) ниже медленной (EMA 21) — короткий тренд вниз")
    if sig.rsi >= 70:
        rsi = f"RSI {sig.rsi:.0f} — зона перекупленности"
    elif sig.rsi <= 30:
        rsi = f"RSI {sig.rsi:.0f} — зона перепроданности"
    else:
        rsi = f"RSI {sig.rsi:.0f} — без перегрева"
    mom = "последние ~100 с цена росла" if sig.momentum > 0 else "последние ~100 с цена снижалась" \
        if sig.momentum < 0 else "последние ~100 с цена стояла"
    basis = f"{trend}; {rsi}; {mom}."
    if not with_trend and (sig.rsi >= 70 or sig.rsi <= 30):
        basis += " Вход против короткого тренда — ставка на откат от крайней зоны RSI."
    elif not with_trend:
        basis += " Вход против короткого тренда — по импульсу последних секунд."
    lines.append("🧩 <b>На чём построен сигнал:</b> " + basis)

    if sig.close_price is None or sig.result == "void":
        lines.append("⚪️ Котировок BinoDex на момент экспирации не было — итог не считаем.")
        return "\n".join(lines)
    change = (sig.close_price - sig.entry_price) / sig.entry_price if sig.entry_price else 0.0
    path = f"Цена: {_px(sig.entry_price)} → {_px(sig.close_price)} ({'+' if change >= 0 else '−'}{_pct(abs(change))})."
    best, worst = sig.best_move or 0.0, sig.worst_move or 0.0
    if sig.result == "win":
        lines.append(f"✅ <b>Почему плюс:</b> цена пошла в сторону сигнала. {path}")
        if worst > 0:
            lines.append(f"↩️ По пути был откат против нас до {_pct(worst)}, но к экспирации цена вернулась.")
    elif sig.result == "refund":
        lines.append(f"<b>Возврат:</b> цена на экспирации совпала с ценой входа. {path}")
    else:
        why = []
        if best > 0 and best >= worst * 0.3:
            why.append(f"сначала цена пошла в нашу сторону (до +{_pct(best)}), но к экспирации развернулась")
        else:
            why.append(f"цена почти сразу пошла против сигнала (до −{_pct(worst)})")
        if up and sig.rsi >= 70 or not up and sig.rsi <= 30:
            why.append(f"вход был при RSI {sig.rsi:.0f} — в зоне, где откаты случаются чаще")
        if not with_trend:
            why.append("вход был против короткого тренда, и тренд оказался сильнее")
        if abs(change) < 0.00005:
            why.append("движение было минимальным — исход решили доли пункта")
        lines.append(f"❌ <b>Почему минус:</b> {'; '.join(why)}. {path}")
        lines.append("💡 Сигнал не даёт гарантии — поэтому бот показывает все итоги, и плюсы, и минусы.")
    return "\n".join(lines)


async def result_text(s, u: User, sig: Signal) -> str:
    line = await get_text(s, u.lang, "result", u.ab, icon=ICON.get(sig.result, ""), symbol=pair_name(sig),
                          arrow=ARROW[sig.direction], word=WORD.get(sig.result, sig.result))
    if sig.result in ("win", "loss"):      # the explanation goes with every result, not only broadcasts
        line = "<b>" + line + "</b>\n\n" + explain(sig)
    return line


async def stats_text(s, cfg: Settings, u: User) -> str:
    from .models import Signal as S
    from sqlalchemy import select
    pairs = []
    ids = (await s.execute(select(S.asset_id, S.symbol, S.is_otc).distinct())).all()
    for aid, sym, otc in ids:
        st = await stats(s, 7, aid)
        if st["win"] + st["loss"]:
            label = html.escape(sym + (" OTC" if otc and "OTC" not in sym.upper() else ""))
            pairs.append((st["win"] + st["loss"], f"{label}: {fmt_rate(st)}"))
    pairs = [p for _, p in sorted(pairs, reverse=True)[:8]] or ["—"]
    return await get_text(s, u.lang, "stats", u.ab, today=fmt_rate(await today_stats(s, cfg.tz)),
                          d7=fmt_rate(await stats(s, 7)), d30=fmt_rate(await stats(s, 30)), pairs="\n".join(pairs))


def open_kb(label: str, url: str) -> KB | None:
    return _kb([[B(text=label, url=url)]]) if url else None


async def render(s, cfg: Settings, u: User, step: str | None = None, note: str = "") -> tuple[str, KB]:
    step = step or u.step
    t = lambda key, **kw: get_text(s, u.lang, key, u.ab, **kw)       # noqa: E731
    sup = [await _btn(s, u, "btn_support", url=cfg.support_url)] if cfg.support_url else []

    def with_note(text: str) -> str:
        return note + "\n\n" + text if note else text

    if step == "welcome":
        if cfg.gate:                        # subscription gate; YouTube / Facebook traffic gets its own greeting
            src = (u.tag or "").lower()
            key = "welcome_yt" if src.startswith("yt") else "welcome_fb" if src.startswith("fb") else "welcome"
            return with_note(await t(key)), _kb([[await _btn(s, u, "btn_subscribe", url=cfg.channel_link)],
                                                       [await _btn(s, u, "btn_subscribed", "sub:check")], sup])
        return with_note(await t("welcome_nogate")), _kb([[await _btn(s, u, "btn_how", "go:value")], sup])

    if step == "value":                     # "how it works", right after the subscription
        return with_note(await t("value")), _kb([[await _btn(s, u, "btn_access", "go:register")],
                                                 [await _btn(s, u, "btn_faq", "faq:open")], sup])

    if step in ("register", "manual"):
        link = ref_link(cfg, u)
        rows = [[await _btn(s, u, "btn_register", url=link)] if link else [],
                [await _btn(s, u, "btn_manual_id", "go:manual")] if step == "register" else [],
                [await _btn(s, u, "btn_find_id", "faq:id"), await _btn(s, u, "btn_faq", "faq:open")],
                [await _btn(s, u, "btn_why_new", url=cfg.video_new_account_url)] if cfg.video_new_account_url else [],
                sup]
        text = await t("manual_id") if step == "manual" else await t("register")
        return with_note(text), _kb(rows)

    if step == "deposit":
        rows = [[await _btn(s, u, "btn_deposit", url=cfg.open_url)] if cfg.open_url else [],
                [await _btn(s, u, "btn_check_dep", "check:dep")],
                [await _btn(s, u, "btn_faq", "faq:open")], sup]
        text = await t("deposit", acc_id=html.escape(u.acc_id), min_dep=f"{cfg.min_deposit_usd:g}")
        return (text + "\n\n" + note if note else text), _kb(rows)

    if step == "access":
        rows = [[await _btn(s, u, "btn_video", url=cfg.video_howto_url)] if cfg.video_howto_url else [],
                [await _btn(s, u, "btn_menu", "go:menu")]]
        return with_note(await t("access", per_day=cfg.broadcast_per_day)), _kb(rows)

    if step == "stats":
        return await stats_text(s, cfg, u), _kb([[await _btn(s, u, "btn_back", "go:menu")]])

    # menu
    rows = [[await _btn(s, u, "btn_signal", "sig:new")],
            [await _btn(s, u, "btn_stats", "go:stats"),
             await _btn(s, u, "btn_notify_off" if u.notify else "btn_notify_on", "sig:notify")],
            [await _btn(s, u, "btn_open", url=cfg.open_url)] if cfg.open_url else [],
            sup]
    text = await t("menu", today=fmt_rate(await today_stats(s, cfg.tz)), notify="вкл" if u.notify else "выкл")
    return (text + "\n\n" + note if note else text), _kb(rows)
