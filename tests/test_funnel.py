import re
import time

import pytest
from pathlib import Path
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from app import bot as botmod
from app import db
from app.models import Signal, User
from app.web import create_app
from tests.conftest import FakePartner


def msg(uid, text):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid, username="u"), text=text)


def cb(uid, data):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append(text)
    return SimpleNamespace(from_user=SimpleNamespace(id=uid, username="u"), data=data, answer=answer, answers=answers)


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def walk_to_manual(bot, uid, tag=None):
    await botmod.on_start(msg(uid, "/start"), SimpleNamespace(args=tag), bot)
    await botmod.on_go(cb(uid, "go:value"), bot)
    await botmod.on_go(cb(uid, "go:register"), bot)
    await botmod.on_go(cb(uid, "go:manual"), bot)


async def test_walkthrough_link_and_old_messages_deleted(cfg, database, bot):
    await botmod.on_start(msg(5, "/start sig_tg1"), SimpleNamespace(args="sig_tg1"), bot)
    assert "BinoDex" in bot.sent[-1][1]
    await botmod.on_go(cb(5, "go:value"), bot)                    # no channel configured: no gate
    assert "Бот открыт" in bot.sent[-1][1]
    await botmod.on_go(cb(5, "go:register"), bot)
    urls = [b.url for b in buttons(bot.sent[-1][2]) if b.url]
    assert urls == ["https://bd.example/smart/X?click_id=5&sub_id1=sig_tg1"]
    assert len(bot.deleted) == 2


async def test_partner_api_claim_paths(cfg, database, bot, monkeypatch):
    p = FakePartner({501: {"uid": 501, "depositsCount": 0}, 777: {"uid": 777, "depositsCount": 2}})
    monkeypatch.setattr(botmod, "partner", p)
    await walk_to_manual(bot, 7)
    await botmod.on_text(msg(7, "12345"), bot)
    assert "не по нашей ссылке" in bot.sent[-1][1]
    await botmod.on_text(msg(7, "501"), bot)
    assert "подтверждён" in bot.sent[-1][1]
    async with db.session() as s:
        u = await s.get(User, 7)
        assert (u.step, u.acc_id) == ("deposit", "501") and u.linked_at is not None

    await walk_to_manual(bot, 8)
    await botmod.on_text(msg(8, "501"), bot)
    assert "уже привязан" in bot.sent[-1][1]
    await botmod.on_text(msg(8, "777"), bot)       # deposited before linking: access at once
    assert "Доступ открыт" in bot.sent[-1][1]


async def test_partner_down_is_reported_not_rejected(cfg, database, bot, monkeypatch):
    monkeypatch.setattr(botmod, "partner", FakePartner(fail=True))
    await walk_to_manual(bot, 9)
    await botmod.on_text(msg(9, "501"), bot)
    assert "не ответил" in bot.sent[-1][1]
    async with db.session() as s:
        assert (await s.get(User, 9)).acc_id == ""


async def test_deposit_check_button_and_background_recheck(cfg, database, bot, monkeypatch):
    p = FakePartner({501: {"uid": 501, "depositsCount": 0}, 502: {"uid": 502, "depositsCount": 0}})
    monkeypatch.setattr(botmod, "partner", p)
    for uid, acc in ((10, "501"), (11, "502")):
        await walk_to_manual(bot, uid)
        await botmod.on_text(msg(uid, acc), bot)
    c = cb(10, "check:dep")
    await botmod.on_check_dep(c, bot)
    assert "не видно" in c.answers[0]
    p.traders[501]["firstDeposit"] = {"at": "2026-11-22 12:00"}
    await botmod.on_check_dep(cb(10, "check:dep"), bot)
    assert "Доступ открыт" in bot.sent[-1][1]

    p.traders[502]["depositsCount"] = 1
    from app.services import due_rechecks, recheck_deposit
    async with db.session() as s:
        due = await due_rechecks(s, cfg)
        assert due == []                              # checked a moment ago: not due yet
        u = await s.get(User, 11)
        u.checked_at = u.checked_at.replace(year=2000)
        await s.commit()
    async with db.session() as s:
        due = await due_rechecks(s, cfg)
        assert [u.id for u in due] == [11]
        out = await recheck_deposit(s, due[0], p)
        await s.commit()
    assert out.action == "access"
    await botmod.deliver(bot, out)
    assert bot.sent[-1][0] == 11 and "Доступ открыт" in bot.sent[-1][1]


async def add_signal(**kw):
    now = time.time()
    row = dict(asset_id=1, symbol="EUR/USD", is_otc=True, direction="up", expiry_sec=60, payout=88, strength=0.7,
               entry_price=1.1, entry_ts=now, expires_ts=now + 60)
    row.update(kw)
    async with db.session() as s:
        sig = Signal(**row)
        s.add(sig)
        await s.commit()
        return sig


async def give_access(uid):
    async with db.session() as s:
        u = await s.get(User, uid)
        u.step = "access"
        await s.commit()


@pytest.fixture
def market(cfg, monkeypatch):
    from tests.test_engine import Clock, FakeMarket, flat, pair
    from app.signal_engine import SignalEngine
    clock = Clock()
    m = FakeMarket(clock, [pair(1, payout=90), pair(2)])
    e = SignalEngine(m, cfg, wall=clock, mono=clock)
    monkeypatch.setattr(botmod, "engine", e)
    return SimpleNamespace(engine=e, market=m, clock=clock, flat=flat)


async def test_subscription_gate(cfg, database, bot):
    cfg.channel = "@trendradar_test"
    await botmod.on_start(msg(20, "/start"), SimpleNamespace(args="yt_v1"), bot)
    text, kb = bot.sent[-1][1], bot.sent[-1][2]
    assert "подпишись на канал" in text.lower() and "из нашего ролика" in text      # yt_ traffic greeting
    assert [b.url for b in buttons(kb) if b.url] == ["https://t.me/trendradar_test"]
    assert [b.callback_data for b in buttons(kb) if b.callback_data] == ["sub:check"]
    c = cb(20, "sub:check")
    await botmod.on_sub_check(c, bot)                             # not a member yet
    assert "не вижу подписки" in c.answers[0]
    c = cb(20, "go:value")
    await botmod.on_go(c, bot)                                    # an old button cannot skip the gate
    assert "не вижу подписки" in c.answers[0]
    bot.members[20] = "member"
    await botmod.on_sub_check(cb(20, "sub:check"), bot)
    assert "Бот открыт" in bot.sent[-1][1]
    async with db.session() as s:
        u = await s.get(User, 20)
        assert u.step == "value" and u.subscribed_at is not None
    await botmod.on_go(cb(20, "go:register"), bot)
    assert "Шаг 1 из 2" in bot.sent[-1][1]


async def test_private_channel_learns_its_id_from_a_post(cfg, database, bot):
    cfg.channel, cfg.channel_url = "", "https://t.me/+tnGGRbInviteLink"
    await botmod.on_start(msg(24, "/start"), SimpleNamespace(args=None), bot)
    assert [b.url for b in buttons(bot.sent[-1][2]) if b.url] == ["https://t.me/+tnGGRbInviteLink"]
    await botmod.on_sub_check(cb(24, "sub:check"), bot)          # id not known yet: let in, log an error
    assert "Бот открыт" in bot.sent[-1][1]
    await botmod.on_channel_post(SimpleNamespace(chat=SimpleNamespace(id=-1001234567890)))
    await botmod.on_channel_post(SimpleNamespace(chat=SimpleNamespace(id=-1009999)))   # the first one stays
    await botmod.on_start(msg(25, "/start"), SimpleNamespace(args=None), bot)
    c = cb(25, "sub:check")
    await botmod.on_sub_check(c, bot)
    assert bot.checked_in == -1001234567890 and "не вижу подписки" in c.answers[0]
    bot.members[25] = "member"
    await botmod.on_sub_check(cb(25, "sub:check"), bot)
    assert "Бот открыт" in bot.sent[-1][1]


async def test_broken_check_does_not_lock_people_out(cfg, database, bot):
    cfg.channel = "@wrong_channel"
    bot.members_error = True
    await botmod.on_start(msg(23, "/start"), SimpleNamespace(args=None), bot)
    await botmod.on_sub_check(cb(23, "sub:check"), bot)
    assert "Бот открыт" in bot.sent[-1][1]


async def test_signals_need_access(cfg, database, bot, market):
    await botmod.on_start(msg(21, "/start"), SimpleNamespace(args=None), bot)
    await botmod.on_signal_btn(cb(21, "sig:new"), bot)          # no test signal any more: registration step
    assert "открываются после двух шагов" in bot.sent[-1][1] and "Шаг 1 из 2" in bot.sent[-1][1]
    await botmod.on_pick_expiry(cb(21, "rq:e:1:60"), bot)        # old picker buttons lead there too
    assert "Шаг 1 из 2" in bot.sent[-1][1]


async def test_no_entry_offers_alternative(cfg, database, bot, market):
    market.market.override[1] = market.flat
    await botmod.on_start(msg(24, "/start"), SimpleNamespace(args=None), bot)
    await give_access(24)
    await market.engine.refresh_pairs()
    await market.engine.poll(2)
    await botmod.on_signal_btn(cb(24, "sig:new"), bot)
    assert buttons(bot.sent[-1][2])[-1].text == "← Назад"
    await botmod.on_pick_expiry(cb(24, "rq:e:1:60"), bot)
    text, kb = bot.sent[-1][1], bot.sent[-1][2]
    assert "нет чёткого входа" in text and "P2 OTC" in text
    assert buttons(kb)[0].callback_data == "rq:e:2:60"
    await botmod.on_pick_expiry(cb(24, "rq:e:2:60"), bot)
    assert "P2 OTC" in bot.sent[-1][1] and "Экспирация" in bot.sent[-1][1]


async def test_access_user_unlimited_but_one_open_per_pair(cfg, database, bot, market):
    await botmod.on_start(msg(22, "/start"), SimpleNamespace(args=None), bot)
    await give_access(22)
    await botmod.on_pick_expiry(cb(22, "rq:e:1:60"), bot)
    c = cb(22, "rq:e:1:60")
    await botmod.on_pick_expiry(c, bot)
    assert "уже есть открытый сигнал" in c.answers[0]
    await botmod.on_pick_expiry(cb(22, "rq:e:2:30"), bot)
    assert "P2 OTC" in bot.sent[-1][1]


async def test_broadcast_only_to_users_with_access(cfg, database, bot):
    for uid in (30, 31, 32):
        await botmod.on_start(msg(uid, "/start"), SimpleNamespace(args="ads1"), bot)
    await give_access(30)
    await give_access(31)
    async with db.session() as s:
        (await s.get(User, 31)).notify = False
        await s.commit()
    sig = await add_signal(source="broadcast")
    assert await botmod.push_signal(bot, sig) == 1          # 30 only: 31 muted, 32 has no access
    by_user = {m[0]: m for m in bot.sent if "Сильный вход" in m[1]}
    assert set(by_user) == {30}
    async with db.session() as s:
        row = await s.get(Signal, sig.id)
        row.result, row.close_price = "win", 1.2
        await s.commit()
    assert await botmod.push_results(bot, [row]) == 1
    assert "Почему плюс" in bot.sent[-1][1] and bot.kw["reply_to_message_id"] == by_user[30][3]
    cfg.push_per_day = 1
    assert await botmod.push_signal(bot, await add_signal(asset_id=2, source="broadcast")) == 0


def test_quiet_hours(cfg):
    from datetime import datetime
    from app.signal_engine import quiet_now
    cfg.quiet_from, cfg.quiet_to = "23:00", "09:00"
    assert quiet_now(cfg, datetime(2026, 1, 1, 23, 30))
    assert quiet_now(cfg, datetime(2026, 1, 1, 8, 59))
    assert not quiet_now(cfg, datetime(2026, 1, 1, 12, 0))


async def test_menu_toggle_and_stats(cfg, database, bot):
    await botmod.on_start(msg(40, "/start"), SimpleNamespace(args=None), bot)
    await give_access(40)
    await add_signal(result="win", close_price=1.2)
    await add_signal(result="loss", close_price=1.0, asset_id=2, symbol="GBP/USD")
    await add_signal(result="void", asset_id=3, symbol="USD/JPY")
    await botmod.on_go(cb(40, "go:stats"), bot)
    text = bot.sent[-1][1]
    assert "50% в плюс (1 из 2)" in text                    # void is not counted
    await botmod.on_signal_btn(cb(40, "sig:notify"), bot)
    assert "Уведомления о сигналах: выкл" in bot.sent[-1][1]


async def test_admin_signals_and_auth(cfg, database):
    await add_signal(result="win", close_price=1.2)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as h:
        assert (await h.get("/admin/signals")).status_code == 401
        r = await h.get("/admin/signals", headers={"Authorization": f"Bearer {cfg.admin_token}"})
        assert r.json()["d7"]["winrate"] == 100
        assert (await h.get("/pb/wrong", params={"event": "reg"})).status_code == 404


def test_no_secrets_in_repo():
    root = Path(__file__).resolve().parents[1]
    pat = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}|binp_[0-9a-f]{24,}|" + "fd49" + "f7a9|" + "7c2a" + "abf4")
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in {".py", ".md", ".txt", ".example", ".yml", ""}:
            assert not pat.search(p.read_text(errors="ignore")), p


def test_explanations_are_built_from_stored_data():
    from app.funnel import explain
    base = dict(symbol="EUR/USD", is_otc=True, expiry_sec=60, payout=88, strength=0.7, entry_ts=0, expires_ts=60,
                asset_id=1, source="broadcast")
    win = Signal(**base, direction="up", rsi=58, ema_fast=1.0002, ema_slow=1.0001, momentum=0.0003,
                 entry_price=1.1000, close_price=1.1003, result="win", best_move=0.0003, worst_move=0.0001)
    t = explain(win)
    assert "EMA 8) выше" in t and "RSI 58 — без перегрева" in t and "Почему плюс" in t
    assert "1.1 → 1.1003 (+0.027%)" in t and "откат против нас до 0.01%" in t

    loss = Signal(**base, direction="up", rsi=74, ema_fast=1.0002, ema_slow=1.0001, momentum=0.0003,
                  entry_price=1.1000, close_price=1.0996, result="loss", best_move=0.0002, worst_move=0.0004)
    t = explain(loss)
    assert "Почему минус" in t and "сначала цена пошла в нашу сторону" in t and "RSI 74" in t

    loss2 = Signal(**base, direction="down", rsi=25, ema_fast=1.0002, ema_slow=1.0001, momentum=0.0003,
                   entry_price=1.1000, close_price=1.10003, result="loss", best_move=0.0, worst_move=0.00003)
    t = explain(loss2)
    assert "почти сразу пошла против" in t and "против короткого тренда" in t and "доли пункта" in t

    void = Signal(**base, direction="up", rsi=50, ema_fast=1, ema_slow=1, momentum=0, entry_price=1.1,
                  result="void")
    assert "итог не считаем" in explain(void)


async def test_pictures_go_with_screens_signals_and_results(cfg, database, bot):
    await botmod.on_start(msg(50, "/start"), SimpleNamespace(args=None), bot)
    assert bot.photos[-1][1].endswith("welcome.jpg")
    await give_access(50)
    sig = await add_signal(source="broadcast")
    await botmod.push_signal(bot, sig)
    assert bot.photos[-1][1] == f"sig{sig.id}.jpg"          # a live card drawn for this signal
    async with db.session() as s:
        row = await s.get(Signal, sig.id)
        row.result, row.close_price = "loss", 1.0
        await s.commit()
    await botmod.push_results(bot, [row])
    assert bot.photos[-1][1] == f"res{sig.id}.jpg" and "Почему минус" in bot.sent[-1][1]


def test_live_cards_draw_real_data():
    import io
    import math
    import time as _t
    from PIL import Image
    from app.cardgen import result_picture, signal_picture
    prices = [1.0841 + 0.00004 * math.sin(i / 5) + 0.000006 * i for i in range(80)]
    sig = signal_picture(symbol="GBP/JPY", is_otc=True, direction="down", payout=85, expiry_sec=120,
                         until_ts=_t.time() + 30, tz="Europe/Kyiv", prices=prices, strong=True, strength_word="сильное")
    res = result_picture(symbol="EUR/USD", is_otc=False, direction="up", result="loss", entry=1.08412, close=1.0839,
                         prices=[1.08412, 1.0842, 1.084, 1.0839], digits=5)
    for data in (sig, res):
        im = Image.open(io.BytesIO(data))
        assert im.size == (1280, 720) and im.format == "JPEG"


async def test_test_accounts_and_reset(cfg, database, bot):
    cfg.test_accounts = "1000001:reg,1000002:dep"
    for uid, acc, expect in ((60, "1000001", "Шаг 2 из 2"), (61, "1000002", "Доступ открыт"),
                             (62, "1000002", "Доступ открыт")):       # several testers may share an ID
        await walk_to_manual(bot, uid)
        await botmod.on_text(msg(uid, acc), bot)
        assert expect in bot.sent[-1][1] and "Тестовый аккаунт" in bot.sent[-1][1]
    await botmod.on_reset(msg(61, "/reset"), bot)
    assert "Прогресс сброшен" in bot.sent[-1][1]
    async with db.session() as s:
        u = await s.get(User, 61)
        assert (u.step, u.acc_id, u.ftd_at) == ("welcome", "", None)
    cfg.test_accounts = ""
    n = len(bot.sent)
    await botmod.on_reset(msg(61, "/reset"), bot)                  # production: /reset does nothing
    assert len(bot.sent) == n


async def test_reminders_chains_limits_and_stop(cfg, database, bot):
    from datetime import timedelta
    from app import reminders
    from app.models import utcnow
    cfg.channel = "@trendradar_test"
    await botmod.on_start(msg(70, "/start"), SimpleNamespace(args=None), bot)      # stuck before subscribing
    t0 = utcnow()
    assert await reminders.run_once(bot, t0 + timedelta(minutes=5)) == 0
    assert await reminders.run_once(bot, t0 + timedelta(minutes=16)) == 1
    assert "Ты почти у цели" in bot.sent[-1][1] and bot.photos[-1][1].endswith("welcome.jpg")
    assert await reminders.run_once(bot, t0 + timedelta(minutes=30)) == 0        # each reminder goes once
    assert await reminders.run_once(bot, t0 + timedelta(hours=4)) == 1
    assert await reminders.run_once(bot, t0 + timedelta(hours=5)) == 0           # start chain is over

    await botmod.on_start(msg(71, "/start"), SimpleNamespace(args=None), bot)
    bot.members[71] = "member"
    await botmod.on_sub_check(cb(71, "sub:check"), bot)                          # now in the "reg" chain
    # away for 4 days: only the latest due reminder is sent, older ones are skipped
    assert await reminders.run_once(bot, t0 + timedelta(days=4)) == 1
    assert "Напоминаем" in bot.sent[-1][1]
    assert await reminders.run_once(bot, t0 + timedelta(days=4, hours=1)) == 0

    cfg.test_accounts = "1000001:reg"
    bot.members[72] = "member"
    await walk_to_manual(bot, 72)
    await botmod.on_text(msg(72, "1000001"), bot)                                 # account linked, no deposit
    n = await reminders.run_once(bot, t0 + timedelta(minutes=40))
    assert n >= 1 and "остался один шаг" in [m for m in bot.sent if m[0] == 72][-1][1]
    cfg.reminders_per_day = 1
    assert await reminders.run_once(bot, t0 + timedelta(hours=7)) == 0            # daily cap
    await give_access(72)
    cfg.reminders_per_day = 5
    assert await reminders.run_once(bot, t0 + timedelta(days=2)) == 0             # deposited: chain stops


async def test_faq_and_find_id_with_back(cfg, database, bot):
    await walk_to_manual(bot, 80)
    await botmod.on_faq(cb(80, "faq:open"), bot)
    assert "Частые вопросы" in bot.sent[-1][1] and "$10" in bot.sent[-1][1]
    await botmod.on_faq(cb(80, "faq:id"), bot)
    assert "Где найти ID" in bot.sent[-1][1]
    await botmod.on_faq(cb(80, "faq:back"), bot)
    assert "Шаг 1 из 2" in bot.sent[-1][1]


async def test_evening_report_with_channel_picture(cfg, database, bot):
    from app import reports
    cfg.admin_ids = [999]
    bot.members[90] = "member"
    await botmod.on_start(msg(90, "/start"), SimpleNamespace(args="yt_v1"), bot)
    await botmod.on_sub_check(cb(90, "sub:check"), bot)
    await add_signal(result="win", close_price=1.2)
    await add_signal(result="loss", close_price=1.0, asset_id=2, symbol="GBP/USD")
    assert await reports.send_report(bot) == 1
    texts = [m[1] for m in bot.sent if m[0] == 999]
    assert any("Отчёт Trend Radar" in t and "yt_v1: 1 → 1 → 0 → 0" in t and "в плюс 1, в минус 1" in t for t in texts)
    assert bot.photos[-1] == (999, "day.jpg")


async def test_idle_and_low_balance_for_users_with_access(cfg, database, bot, monkeypatch):
    from datetime import timedelta
    from app import reminders
    from app.models import utcnow
    p = FakePartner({501: {"uid": 501, "depositsCount": 1, "balance": "4.20"},
                     502: {"uid": 502, "depositsCount": 1, "balance": "250"}})
    monkeypatch.setattr(botmod, "partner", p)
    for uid, acc in ((91, "501"), (92, "502")):
        await botmod.on_start(msg(uid, "/start"), SimpleNamespace(args=None), bot)
        async with db.session() as s:
            u = await s.get(User, uid)
            u.step, u.acc_id = "access", acc
            await s.commit()
    now = utcnow()
    assert await reminders.run_once(bot, now) == 1                       # 91: balance below $10
    assert "меньше $10" in [m for m in bot.sent if m[0] == 91][-1][1]
    assert await reminders.run_once(bot, now + timedelta(hours=13)) == 0  # once per LOW_BALANCE_EVERY_DAYS
    assert await reminders.run_once(bot, now + timedelta(days=3, hours=1)) == 2   # both idle for 3 days
    assert "Давно не заходил" in [m for m in bot.sent if m[0] == 92][-1][1]
    assert await reminders.run_once(bot, now + timedelta(days=3, hours=2)) == 0
