from sqlalchemy import func, select

from app import db
from app.models import Event, Postback, User
from app.services import claim_account, get_or_create_user, handle_postback, ref_link


async def new_user(uid=111, tag="tg_ads1"):
    async with db.session() as s:
        u, _ = await get_or_create_user(s, uid, "x", tag)
        await s.commit()
        return u


async def pb(cfg, **params):
    async with db.session() as s:
        out = await handle_postback(s, cfg, params)
        if out.action != "duplicate":
            await s.commit()
        return out


async def test_ref_link_substitutes_id_and_tag(cfg, database):
    u = await new_user(42, "yt_video-3")
    assert ref_link(cfg, u) == "https://bd.example/smart/X?click_id=42&sub_id1=yt_video-3"


async def test_tag_is_sanitised_and_fixed(cfg, database):
    await new_user(1, "ads<script>")
    async with db.session() as s:
        u, new = await get_or_create_user(s, 1, "x", "other")
        assert not new and u.tag == "adsscript"


async def test_reg_then_ftd_by_click_id(cfg, database):
    await new_user()
    out = await pb(cfg, event="reg", click_id="111", trader_id="5550001")
    assert out.action == "registered" and out.user_id == 111
    out = await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="25.5")
    assert out.action == "access"
    async with db.session() as s:
        u = await s.get(User, 111)
        assert (u.step, u.acc_id, u.ftd_amount, u.deposits_count) == ("access", "5550001", 25.5, 1)
        names = sorted(e.name for e in (await s.execute(select(Event))).scalars())
        assert names == ["ftd", "registered", "start"]


async def test_repeated_postbacks_are_ignored(cfg, database):
    await new_user()
    await pb(cfg, event="reg", click_id="111", trader_id="5550001")
    assert (await pb(cfg, event="reg", click_id="111", trader_id="5550001")).action == "duplicate"
    await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="20")
    assert (await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="20")).action == "duplicate"
    async with db.session() as s:
        assert (await s.get(User, 111)).deposits_total == 20


async def test_repeat_deposits_count_once_per_tx(cfg, database):
    await new_user()
    await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="20")
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", tx_id="t1")).action == "deposit"
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", tx_id="t1")).action == "duplicate"
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", tx_id="t2")).action == "deposit"
    async with db.session() as s:
        u = await s.get(User, 111)
        assert (u.deposits_total, u.deposits_count) == (120, 3)


async def test_ftd_without_reg_opens_access(cfg, database):
    await new_user()
    assert (await pb(cfg, event="ftd", click_id="111", trader_id="777777", amount="10")).action == "access"


async def test_unknown_user_is_stored_then_claimed_manually(cfg, database):
    await pb(cfg, event="reg", trader_id="9990001")
    await pb(cfg, event="ftd", trader_id="9990001", amount="15")
    u = await new_user(222)
    async with db.session() as s:
        u = await s.get(User, 222)
        status, outs = await claim_account(s, u, " 9990001 ")
        await s.commit()
    assert status == "linked" and [o.action for o in outs] == ["registered", "access"]
    async with db.session() as s:
        u = await s.get(User, 222)
        assert u.step == "access" and u.ftd_amount == 15
        assert (await s.execute(select(func.count()).where(Postback.applied.is_(False)))).scalar() == 0


async def test_claim_rules(cfg, database):
    await new_user(1)
    await new_user(2)
    await pb(cfg, event="reg", click_id="1", trader_id="1234567")
    async with db.session() as s:
        u2 = await s.get(User, 2)
        assert (await claim_account(s, u2, "abc"))[0] == "bad"
        assert (await claim_account(s, u2, "12"))[0] == "bad"
        assert (await claim_account(s, u2, "1234567"))[0] == "taken"
        assert (await claim_account(s, u2, "7654321"))[0] == "not_found"


async def test_postback_cannot_steal_linked_account(cfg, database):
    await new_user(1)
    await new_user(2)
    await pb(cfg, event="reg", click_id="1", trader_id="1234567")
    out = await pb(cfg, event="ftd", click_id="2", trader_id="1234567", amount="10")
    assert out.action == "ignored"
    async with db.session() as s:
        assert (await s.get(User, 2)).step == "welcome"


async def test_custom_param_names(cfg, database):
    cfg.pb_event, cfg.pb_click_id, cfg.pb_trader_id = "status", "sub1", "uid"
    cfg.pb_event_reg = "registration"
    await new_user()
    out = await pb(cfg, status="Registration", sub1="111", uid="4440001")
    assert out.action == "registered"


async def test_deposit_try_and_commission(cfg, database):
    await new_user()
    await pb(cfg, event="reg", click_id="111", trader_id="5550001")
    out = await pb(cfg, event="try", click_id="111", trader_id="5550001", amount="50")
    assert out.action == "dep_try"
    async with db.session() as s:
        u = await s.get(User, 111)
        assert u.step == "deposit" and u.dep_try_at is not None     # intent does not open access
    await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="50", dt="2026-11-22 10:00")
    await pb(cfg, event="com", click_id="111", trader_id="5550001", amount="12.5", dt="2026-11-22")
    assert (await pb(cfg, event="com", click_id="111", trader_id="5550001", amount="12.5",
                     dt="2026-11-22")).action == "duplicate"
    await pb(cfg, event="com", click_id="111", trader_id="5550001", amount="3", dt="2026-11-23")
    async with db.session() as s:
        assert (await s.get(User, 111)).commission_usd == 15.5


async def test_same_amount_deposits_differ_by_date_time(cfg, database):
    await new_user()
    await pb(cfg, event="ftd", click_id="111", trader_id="5550001", amount="20", dt="1")
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", dt="2026-11-22 11:00")).action == "deposit"
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", dt="2026-11-22 11:00")).action == "duplicate"
    assert (await pb(cfg, event="dep", trader_id="5550001", amount="50", dt="2026-11-22 12:00")).action == "deposit"
