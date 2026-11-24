"""Telegram handlers, signal pushes and result replies.

In the funnel only the current step stays in the chat (previous bot messages are deleted). Signal pushes and
their results are separate messages that stay, so the user keeps the history of what was sent."""
from __future__ import annotations

import asyncio
import html
import re
import logging
import time
from datetime import timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, ChatMemberUpdated, FSInputFile, InlineKeyboardButton as B, InlineKeyboardMarkup as KB, Message
from sqlalchemy import func, select

from . import db
from .config import Settings, get_settings
from .funnel import expiry_picker, pair_picker, render, result_kb, result_text, signal_kb, signal_text, trial_kb
from .models import KV, Delivery, Signal, User, utcnow
from .services import Outcome, claim_account, due_rechecks, get_or_create_user, log_event, recheck_deposit
from .signal_engine import quiet_now
from .texts import get_text

log = logging.getLogger(__name__)
router = Router()

ALLOWED = {"welcome": ("value",), "value": ("register",), "register": ("manual",), "manual": ("register",)}
RANK = {"welcome": 0, "value": 1, "register": 2, "manual": 2, "deposit": 3, "access": 4}
CLAIM_KEYS = {"bad": "manual_bad", "taken": "manual_taken", "not_found": "manual_not_found",
              "not_attributed": "manual_not_attributed", "unavailable": "manual_unavailable"}

MEDIA = Path(__file__).resolve().parents[1] / "media"
STEP_IMAGE = {"welcome": "welcome", "value": "value", "register": "register", "deposit": "deposit", "access": "access"}
_file_ids: dict[str, str] = {}          # Telegram file_id per picture: each JPG is uploaded once

partner = None          # PartnerApi, set by main (None = checks by postbacks only)
engine = None           # SignalEngine, set by main


async def send(bot: Bot, chat_id: int, text: str, kb=None, image: str | None = None,
               photo: bytes | None = None, **kw):
    """A picture with the text as its caption, else text (caption limit 1024).
    `photo` = a picture drawn for this message (cached by `image` as key); else media/<image>.jpg."""
    path = MEDIA / f"{image}.jpg" if image else None
    if (photo is not None or (path is not None and path.is_file())) and len(text) <= 1024:
        if image in _file_ids:
            src = _file_ids[image]
        elif photo is not None:
            src = BufferedInputFile(photo, filename=f"{image or 'card'}.jpg")
        else:
            src = FSInputFile(path)
        msg = await bot.send_photo(chat_id, src, caption=text, reply_markup=kb, **kw)
        sent = getattr(msg, "photo", None)
        if sent and image:
            _file_ids[image] = sent[-1].file_id
            _pics.pop(image, None)
        return msg
    return await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True, **kw)


async def show(bot: Bot, s, cfg: Settings, u: User, step: str | None = None, note: str = "") -> None:
    step = step or u.step
    text, kb = await render(s, cfg, u, step, note)
    old = list(u.msg_ids or [])
    try:
        msg = await send(bot, u.id, text, kb, STEP_IMAGE.get(step))
    except TelegramForbiddenError:
        u.blocked = True
        return
    u.msg_ids = [msg.message_id]
    for mid in old:
        try:
            await bot.delete_message(u.id, mid)
        except TelegramBadRequest:
            pass


@router.message(Command("reset"))
async def on_reset(m: Message, bot: Bot) -> None:
    """Test mode (TEST_ACCOUNTS set) or an admin: walk the funnel again from the start."""
    cfg = get_settings()
    if not cfg.test_map and m.from_user.id not in cfg.admin_ids:
        return
    async with db.session() as s:
        u, _ = await get_or_create_user(s, m.from_user.id, m.from_user.username or "")
        u.step, u.acc_id = "welcome", ""
        u.registered_at = u.ftd_at = u.subscribed_at = u.linked_at = u.checked_at = u.dep_try_at = None
        u.ftd_amount = u.deposits_total = u.commission_usd = 0.0
        u.deposits_count, u.free_signal_at = 0, 0.0
        await show(bot, s, cfg, u, "welcome", note=await get_text(s, u.lang, "reset_done", u.ab))
        await s.commit()


@router.message(CommandStart())
async def on_start(m: Message, command: CommandObject, bot: Bot) -> None:
    cfg = get_settings()
    async with db.session() as s:
        u, _new = await get_or_create_user(s, m.from_user.id, m.from_user.username or "", command.args)
        step = "menu" if u.step == "access" else u.step
        if step == "manual":
            step = u.step = "register"
        await show(bot, s, cfg, u, step)
        await s.commit()


@router.callback_query(F.data.startswith("go:"))
async def on_go(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    target = c.data.split(":", 1)[1]
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        if target == "back":                 # from the pair / expiry pickers
            await show(bot, s, cfg, u, "menu" if u.step == "access" else u.step)
        elif target.startswith("join"):      # from a broadcast or the free signal: straight to registration
            await log_event(s, u, "join_from_free" if target.endswith("free") else "join_from_broadcast")
            if RANK.get(u.step, 0) < RANK["register"]:
                await log_event(s, u, "register")
                u.step = "register"
            await show(bot, s, cfg, u, "menu" if u.step == "access" else u.step)
        elif target == "value" and u.step == "welcome" and cfg.gate and not u.subscribed_at:
            await check_subscription(c, bot, s, cfg, u)       # the old "how it works" button: gate first
            await s.commit()
            return
        elif target in ("menu", "stats"):
            if u.step != "access":
                await c.answer(await get_text(s, u.lang, "no_access", u.ab), show_alert=True)
                await s.commit()
                return
            await show(bot, s, cfg, u, target)
        else:
            if target in ALLOWED.get(u.step, ()):
                if RANK[target] > RANK[u.step]:
                    await log_event(s, u, target)
                u.step = target
            await show(bot, s, cfg, u, "menu" if u.step == "access" else u.step)
        await s.commit()
    await c.answer()


async def channel_ref(s, cfg: Settings) -> int | str | None:
    """CHANNEL from settings, else the id the bot learned from the channel itself (private channels)."""
    if cfg.channel:
        c = cfg.channel.strip()
        return int(c) if c.lstrip("-").isdigit() else c
    row = await s.get(KV, "channel_id")
    return int(row.value) if row and row.value.lstrip("-").isdigit() else None


async def is_subscribed(bot: Bot, s, cfg: Settings, user_id: int) -> bool | None:
    """True / False, or None when the check is impossible (channel id not known yet, bot not an admin)."""
    ref = await channel_ref(s, cfg)
    if ref is None:
        log.error("subscription gate: channel id unknown — publish any post in the channel so the bot sees it")
        return None
    try:
        m = await bot.get_chat_member(ref, user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        log.error("subscription check in %s failed: %s (is the bot an admin of the channel?)", ref, exc)
        return None
    status = getattr(m.status, "value", m.status)
    return status in ("member", "administrator", "creator") or (status == "restricted" and getattr(m, "is_member", False))


async def remember_channel(chat_id: int) -> None:
    """The first channel the bot sees as an admin becomes the gate channel (unless CHANNEL is set)."""
    if get_settings().channel:
        return
    async with db.session() as s:
        if await s.get(KV, "channel_id") is None:
            s.add(KV(key="channel_id", value=str(chat_id)))
            await s.commit()
            log.info("subscription gate channel learned: %s", chat_id)


@router.my_chat_member(F.chat.type == "channel")
async def on_bot_in_channel(event: ChatMemberUpdated) -> None:
    if getattr(event.new_chat_member.status, "value", event.new_chat_member.status) == "administrator":
        await remember_channel(event.chat.id)


@router.channel_post()
async def on_channel_post(m: Message) -> None:
    await remember_channel(m.chat.id)


async def check_subscription(c: CallbackQuery, bot: Bot, s, cfg: Settings, u: User) -> None:
    ok = await is_subscribed(bot, s, cfg, u.id) if cfg.gate else True
    if ok is False:
        await c.answer(await get_text(s, u.lang, "not_subscribed", u.ab), show_alert=True)
        return
    if ok is None:          # a broken check must not lock people out: let them in, the log shows the error
        log.warning("letting %s through without a subscription check", u.id)
    if u.subscribed_at is None:
        u.subscribed_at = utcnow()
        await log_event(s, u, "subscribed")
    if u.step == "welcome":
        await log_event(s, u, "value")
        u.step = "value"
    await show(bot, s, cfg, u, "menu" if u.step == "access" else u.step)
    await c.answer()


@router.callback_query(F.data == "sub:check")
async def on_sub_check(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        await check_subscription(c, bot, s, cfg, u)
        await s.commit()


@router.callback_query(F.data == "check:dep")
async def on_check_dep(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        if u.step != "access":
            await recheck_deposit(s, u, partner)
        if u.step == "access":
            await show(bot, s, cfg, u, "access")
            await s.commit()
            await c.answer()
            return
        await s.commit()
        await c.answer(await get_text(s, u.lang, "deposit_wait", u.ab), show_alert=True)


async def show_raw(bot: Bot, u: User, text: str, kb) -> None:
    """A transient screen (pickers): replaces the previous funnel/picker message."""
    old = list(u.msg_ids or [])
    try:
        msg = await bot.send_message(u.id, text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramForbiddenError:
        u.blocked = True
        return
    u.msg_ids = [msg.message_id]
    for mid in old:
        try:
            await bot.delete_message(u.id, mid)
        except TelegramBadRequest:
            pass


async def clear_screens(bot: Bot, u: User) -> None:
    for mid in list(u.msg_ids or []):
        try:
            await bot.delete_message(u.id, mid)
        except TelegramBadRequest:
            pass
    u.msg_ids = []


async def _gate(c: CallbackQuery, bot: Bot, s, cfg: Settings, u: User) -> bool:
    """Signals on request are for users with access; everyone else goes to the registration step."""
    if u.step == "access":
        return True
    if RANK.get(u.step, 0) < RANK["register"]:
        await log_event(s, u, "register")
        u.step = "register"
    await show(bot, s, cfg, u, u.step, note=await get_text(s, u.lang, "free_used", u.ab))
    await c.answer()
    return False


def _label(p) -> str:
    return p.symbol + (" OTC" if p.is_otc and "OTC" not in p.symbol.upper() else "")


@router.callback_query(F.data.startswith("sig:"))
async def on_signal_btn(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    what = c.data.split(":", 1)[1]
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        if what == "mute":
            u.notify = False
            await c.answer(await get_text(s, u.lang, "muted", u.ab), show_alert=True)
            await s.commit()
            return
        if what == "notify":
            if u.step != "access":
                await c.answer(await get_text(s, u.lang, "no_access", u.ab), show_alert=True)
            else:
                u.notify = not u.notify
                await show(bot, s, cfg, u, "menu")
                await c.answer()
            await s.commit()
            return
        if not await _gate(c, bot, s, cfg, u):
            await s.commit()
            return
        try:
            await engine.refresh_pairs()
        except Exception as exc:          # BinoDex down: the picker says there are no pairs
            log.warning("pairs: %s", exc)
        pairs = engine.eligible(min(cfg.expiries))
        if not pairs:
            await c.answer(await get_text(s, u.lang, "no_pairs", u.ab, payout=cfg.signal_min_payout), show_alert=True)
            await s.commit()
            return
        text, kb = await pair_picker(s, cfg, u, pairs)
        await show_raw(bot, u, text, kb)
        await s.commit()
    await c.answer()


@router.callback_query(F.data.startswith("rq:p:"))
async def on_pick_pair(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    key = c.data.split(":", 2)[2]
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        if key == "auto":
            label = await get_text(s, u.lang, "btn_auto", u.ab)
        else:
            p = engine.pairs.get(int(key)) if key.isdigit() else None
            if p is None:
                await c.answer(await get_text(s, u.lang, "pair_unavailable", u.ab), show_alert=True)
                await s.commit()
                return
            label = _label(p)
        text, kb = await expiry_picker(s, cfg, u, key, label)
        await show_raw(bot, u, text, kb)
        await s.commit()
    await c.answer()


@router.callback_query(F.data.startswith("rq:e:"))
async def on_pick_expiry(c: CallbackQuery, bot: Bot) -> None:
    cfg = get_settings()
    _, _, key, exp = c.data.split(":", 3)
    async with db.session() as s:
        u, _ = await get_or_create_user(s, c.from_user.id, c.from_user.username or "")
        if not await _gate(c, bot, s, cfg, u):
            await s.commit()
            return
        asset = None if key == "auto" else int(key) if key.isdigit() else -1
        r = await engine.request(u.id, asset, int(exp) if exp.isdigit() else 0)
        if r.status == "ok":
            if u.step != "access":
                u.free_signal_at = time.time()
                await log_event(s, u, "free_signal")
            await log_event(s, u, "signal")
            await clear_screens(bot, u)
            await send_signal(bot, s, cfg, u, r.signal)
            await s.commit()
            await c.answer()
            return
        pair = engine.pairs.get(asset) if asset else None
        pname = html.escape(_label(pair)) if pair else ""
        if r.status == "no_entry" and asset is None:
            await c.answer(await get_text(s, u.lang, "no_entry_auto", u.ab), show_alert=True)
        elif r.status == "no_entry":
            note = await get_text(s, u.lang, "no_entry", u.ab, pair=pname)
            rows = [[]]
            if r.alternative is not None:
                alt = _label(r.alternative)
                note += "\n\n" + await get_text(s, u.lang, "no_entry_alt", u.ab, alt=html.escape(alt))
                rows = [[B(text=await get_text(s, u.lang, "btn_alt", u.ab, alt=alt),
                           callback_data=f"rq:e:{r.alternative.id}:{exp}")]]
            rows.append([B(text=await get_text(s, u.lang, "btn_other_pair", u.ab), callback_data="sig:new")])
            await show_raw(bot, u, note, KB(inline_keyboard=[x for x in rows if x]))
            await c.answer()
        elif r.status == "pending":
            from .funnel import local_time
            text = await get_text(s, u.lang, "pending", u.ab, pair=_label(pair) if pair else "",
                                  until=local_time(r.signal.expires_ts, cfg.tz))
            await c.answer(re.sub(r"<[^>]+>", "", text), show_alert=True)       # alerts show plain text
        else:
            key2 = r.status if r.status in ("no_quotes", "pair_unavailable", "no_pairs") else "pair_unavailable"
            await c.answer((await get_text(s, u.lang, key2, u.ab, payout=cfg.signal_min_payout)), show_alert=True)
        await s.commit()


@router.message(F.text)
async def on_text(m: Message, bot: Bot) -> None:
    cfg = get_settings()
    async with db.session() as s:
        u, _ = await get_or_create_user(s, m.from_user.id, m.from_user.username or "")
        if u.step not in ("register", "manual"):
            await show(bot, s, cfg, u, "menu" if u.step == "access" else u.step)
            await s.commit()
            return
        status, _outcomes = await claim_account(s, u, m.text or "", partner, cfg.test_map)
        acc = "".join(ch for ch in (m.text or "") if ch.isdigit())[:12]
        if status == "linked":
            kind = cfg.test_map.get(acc)
            note = ""
            if kind:
                note = await get_text(s, u.lang, "test_mode", u.ab, acc_id=acc,
                                      what="есть депозит" if kind == "dep" else "регистрация без депозита")
            await show(bot, s, cfg, u, u.step, note=note)   # deposit screen, or "access" when already deposited
        else:
            u.step = "manual"
            await show(bot, s, cfg, u, "manual", note=await get_text(s, u.lang, CLAIM_KEYS[status], u.ab, acc_id=acc))
        await s.commit()


# ---------------------------------------------------------------------- pushes
async def send_signal(bot: Bot, s, cfg: Settings, u: User, sig: Signal) -> bool:
    kb = await signal_kb(s, cfg, u)
    text = await signal_text(s, cfg, u, sig)
    if sig.source == "broadcast":
        text = await get_text(s, u.lang, "broadcast_head", u.ab) + "\n" + text
        if u.step != "access":              # a free test for newcomers: watch the result, then join
            text += "\n\n" + await get_text(s, u.lang, "broadcast_trial", u.ab)
            kb = await trial_kb(s, cfg, u)
    if sig.source != "broadcast" and u.step != "access":
        text += "\n\n" + await get_text(s, u.lang, "free_note", u.ab)
    key, pic = await signal_photo(sig)
    for _ in range(2):
        try:
            msg = await send(bot, u.id, text, kb, key, photo=pic)
            break
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except TelegramForbiddenError:
            u.blocked = True
            return False
        except TelegramBadRequest as exc:
            log.warning("signal to %s: %s", u.id, exc)
            return False
    else:
        return False
    if (await s.execute(select(Delivery.id).where(Delivery.signal_id == sig.id, Delivery.user_id == u.id))).scalar() is None:
        s.add(Delivery(signal_id=sig.id, user_id=u.id, message_id=msg.message_id))
    return True


_pics: dict[str, bytes] = {}            # drawn pictures by key, until Telegram gives us a file_id


async def signal_photo(sig: Signal) -> tuple[str, bytes | None]:
    """A live card for this signal: real pair, payout, direction, expiry, entry-until and recent BinoDex prices.
    Falls back to the static picture if drawing fails."""
    key = f"sig{sig.id}"
    if key in _file_ids or key in _pics:
        return key, _pics.get(key)
    try:
        from .cardgen import signal_picture
        from .funnel import strength_word
        cfg = get_settings()
        q = engine.quotes.get(sig.asset_id) if engine is not None else None
        prices = [p for t, p in q.closes if t <= sig.entry_ts] if q else []
        pic = await asyncio.to_thread(
            signal_picture, symbol=sig.symbol, is_otc=sig.is_otc, direction=sig.direction, payout=sig.payout,
            expiry_sec=sig.expiry_sec, until_ts=sig.entry_ts + cfg.signal_valid_sec, tz=cfg.tz,
            prices=(prices + [sig.entry_price])[-60:], strong=sig.source == "broadcast",
            strength_word=strength_word(sig.strength))
        _pics[key] = pic
        return key, pic
    except Exception:
        log.exception("signal picture %s", sig.id)
        return ("strong" if sig.source == "broadcast" else f"signal_{sig.direction}"), None


async def result_photo(sig: Signal) -> tuple[str | None, bytes | None]:
    """A live result card: the price path from entry to expiry with the entry level (win / loss only)."""
    if sig.result not in ("win", "loss") or sig.close_price is None:
        return None, None
    key = f"res{sig.id}"
    if key in _file_ids or key in _pics:
        return key, _pics.get(key)
    try:
        from .cardgen import result_picture
        q = engine.quotes.get(sig.asset_id) if engine is not None else None
        path = [p for t, p in q.closes if sig.entry_ts <= t <= sig.expires_ts + 5] if q else []
        pair = engine.pairs.get(sig.asset_id) if engine is not None else None
        pic = await asyncio.to_thread(
            result_picture, symbol=sig.symbol, is_otc=sig.is_otc, direction=sig.direction, result=sig.result,
            entry=sig.entry_price, close=sig.close_price, prices=[sig.entry_price] + path,
            digits=pair.digits if pair else None)
        _pics[key] = pic
        return key, pic
    except Exception:
        log.exception("result picture %s", sig.id)
        return sig.result, None


async def push_signal(bot: Bot, sig: Signal) -> int:
    """Strong signal -> every user who pressed /start and did not mute them, within the daily cap,
    outside quiet hours. Newcomers get it as a test with a "Get access" button."""
    cfg = get_settings()
    if quiet_now(cfg):
        return 0
    sent = 0
    day_ago = utcnow() - timedelta(hours=24)
    async with db.session() as s:
        got = dict((await s.execute(select(Delivery.user_id, func.count())
                                    .join(Signal, Signal.id == Delivery.signal_id)
                                    .where(Delivery.created_at >= day_ago, Signal.source == "broadcast")
                                    .group_by(Delivery.user_id))).all())
        # everyone who pressed /start: for newcomers a strong signal + its result is the best demo of the bot
        users = (await s.execute(select(User).where(User.notify.is_(True), User.blocked.is_(False)))).scalars().all()
        for u in users:
            if got.get(u.id, 0) >= cfg.push_per_day:
                continue
            if await send_signal(bot, s, cfg, u, sig):
                sent += 1
            await s.commit()
            await asyncio.sleep(0.05)        # well under Telegram's 30 messages per second
    log.info("signal %s pushed to %d users", sig.id, sent)
    return sent


async def push_results(bot: Bot, settled: list[Signal]) -> int:
    """Result as a reply under each delivered signal message."""
    n = 0
    async with db.session() as s:
        for sig in settled:
            rows = (await s.execute(select(Delivery).where(Delivery.signal_id == sig.id))).scalars().all()
            for d in rows:
                u = await s.get(User, d.user_id)
                if u is None or u.blocked:
                    continue
                try:
                    key, pic = await result_photo(sig)
                    await send(bot, u.id, await result_text(s, u, sig), await result_kb(s, get_settings(), u), key,
                               photo=pic,
                               reply_to_message_id=d.message_id, allow_sending_without_reply=True)
                    n += 1
                except TelegramForbiddenError:
                    u.blocked = True
                except TelegramBadRequest as exc:
                    log.info("result to %s: %s", u.id, exc)
                await asyncio.sleep(0.05)
        await s.commit()
    return n


async def deliver(bot: Bot, outcome: Outcome) -> None:
    """After a postback or a Partner API check moved the user: show the next screen right away."""
    if outcome.user_id is None or outcome.action not in ("registered", "access"):
        return
    cfg = get_settings()
    async with db.session() as s:
        u = await s.get(User, outcome.user_id)
        if u is None or u.blocked:
            return
        await show(bot, s, cfg, u, u.step)
        await s.commit()


async def recheck_forever(bot: Bot, interval: float = 60.0) -> None:
    """Partner API deposit checks for linked users without a deposit (see services.due_rechecks)."""
    cfg = get_settings()
    await asyncio.sleep(30)
    while True:
        try:
            async with db.session() as s:
                users = await due_rechecks(s, cfg)
                opened = []
                for u in users:
                    out = await recheck_deposit(s, u, partner)
                    if out is not None and out.action == "access":
                        opened.append(out)
                    await asyncio.sleep(0.3)
                await s.commit()
            for out in opened:
                await deliver(bot, out)
        except Exception:
            log.exception("recheck failed")
        await asyncio.sleep(interval)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp
