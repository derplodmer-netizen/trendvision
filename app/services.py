"""Users, funnel events, partner link and postback processing. No Telegram calls here:
functions return what happened and the caller (bot / API) delivers messages."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .models import Event, Postback, User, utcnow

log = logging.getLogger(__name__)

TAG_RE = re.compile(r"[^A-Za-z0-9_\-]")
ACC_ID_RE = re.compile(r"^\d{3,12}$")

STEPS = ("welcome", "value", "register", "manual", "deposit", "access")


def clean_tag(payload: str | None) -> str:
    return TAG_RE.sub("", (payload or ""))[:64]


def ab_group(user_id: int) -> str:
    return "ab"[int(hashlib.sha256(str(user_id).encode()).hexdigest(), 16) % 2]


async def log_event(s, user: User, name: str) -> None:
    s.add(Event(user_id=user.id, name=name, tag=user.tag, ab=user.ab))


async def get_or_create_user(s, user_id: int, username: str = "", payload: str | None = None,
                             lang: str = "ru") -> tuple[User, bool]:
    user = await s.get(User, user_id)
    if user:
        user.last_seen = utcnow()
        user.blocked = False
        return user, False
    user = User(id=user_id, username=(username or "")[:64], tag=clean_tag(payload), ab=ab_group(user_id),
                lang=lang if lang in ("ru",) else "ru", msg_ids=[])
    s.add(user)
    await s.flush()
    await log_event(s, user, "start")
    return user, True


def ref_link(cfg: Settings, user: User) -> str:
    """Partner link with the user's Telegram id and tag substituted. Empty when not configured."""
    if not cfg.ref_url:
        return ""
    return cfg.ref_url.replace("{tg_id}", str(user.id)).replace("{tag}", quote(user.tag or "organic"))


# ------------------------------------------------------------------ postbacks
@dataclass
class Outcome:
    user_id: int | None
    action: str  # registered | access | deposit | dep_try | commission | stored | duplicate | ignored
    acc_id: str = ""
    amount: float = 0.0


def _num(v: str | None) -> float:
    try:
        return round(float((v or "0").replace(",", ".")), 2)
    except ValueError:
        return 0.0


def _event_kind(cfg: Settings, raw_event: str) -> str:
    e = (raw_event or "").strip().lower()
    return {cfg.pb_event_reg.lower(): "reg", cfg.pb_event_ftd.lower(): "ftd",
            cfg.pb_event_dep.lower(): "dep", cfg.pb_event_try.lower(): "try",
            cfg.pb_event_com.lower(): "com"}.get(e, "unknown")


async def handle_postback(s, cfg: Settings, params: dict[str, str]) -> Outcome:
    kind = _event_kind(cfg, params.get(cfg.pb_event, ""))
    acc_id = re.sub(r"\D", "", params.get(cfg.pb_trader_id, ""))[:32]
    click = params.get(cfg.pb_click_id, "")
    amount = _num(params.get(cfg.pb_amount))
    tx = params.get(cfg.pb_tx_id, "")
    tag = clean_tag(params.get(cfg.pb_tag))

    if kind == "reg":
        dedup = f"reg:{acc_id}"
    elif kind == "ftd":
        dedup = f"ftd:{acc_id}"
    else:       # deposits / commission repeat legitimately: dedup on tx id, else on the exact request
        # (the exact request includes {date_time}, so a resent postback is a duplicate, a new deposit is not)
        body = tx or "&".join(f"{k}={params[k]}" for k in sorted(params))
        dedup = f"{kind}:{acc_id}:" + hashlib.sha256(body.encode()).hexdigest()[:32]

    pb = Postback(dedup=dedup, event=kind, acc_id=acc_id, amount=amount, tag=tag, raw=dict(params))
    s.add(pb)
    try:
        await s.flush()
    except IntegrityError:
        await s.rollback()
        return Outcome(None, "duplicate", acc_id, amount)
    if kind == "unknown" or not acc_id:
        return Outcome(None, "ignored", acc_id, amount)

    user = None
    if click.isdigit():
        user = await s.get(User, int(click))
    if user is None:
        user = (await s.execute(select(User).where(User.acc_id == acc_id))).scalar_one_or_none()
    if user is None:
        return Outcome(None, "stored", acc_id, amount)      # claimed later by manual ID entry

    if user.acc_id and user.acc_id != acc_id:
        log.warning("postback %s for user %s with other acc_id %s", acc_id, user.id, user.acc_id)
        return Outcome(user.id, "ignored", acc_id, amount)
    owner = (await s.execute(select(User).where(User.acc_id == acc_id, User.id != user.id))).scalar_one_or_none()
    if owner is not None:
        return Outcome(user.id, "ignored", acc_id, amount)

    pb.user_id = user.id
    pb.applied = True
    return await _apply(s, user, kind, acc_id, amount)


async def _apply(s, user: User, kind: str, acc_id: str, amount: float) -> Outcome:
    if kind == "com":                # money for us, says nothing about the funnel step
        if not user.acc_id:
            user.acc_id = acc_id
        user.commission_usd = round(user.commission_usd + amount, 2)
        return Outcome(user.id, "commission", acc_id, amount)
    if kind == "try":                # intent only: the funnel step does not change
        if not user.acc_id:
            user.acc_id = acc_id
        if user.dep_try_at is None:
            user.dep_try_at = utcnow()
            await log_event(s, user, "dep_try")
        return Outcome(user.id, "dep_try", acc_id, amount)
    if not user.acc_id:
        user.acc_id = acc_id
    if user.registered_at is None:
        user.registered_at = utcnow()
        await log_event(s, user, "registered")
    if kind == "reg":
        if user.step not in ("access",):
            user.step = "deposit"
        return Outcome(user.id, "registered", acc_id, amount)
    if user.ftd_at is None:          # ftd, or the first deposit we ever see
        user.ftd_at = utcnow()
        user.ftd_amount = amount
        user.deposits_total += amount
        user.deposits_count += 1
        user.step = "access"
        await log_event(s, user, "ftd")
        return Outcome(user.id, "access", acc_id, amount)
    if kind == "dep":
        user.deposits_total += amount
        user.deposits_count += 1
        await log_event(s, user, "deposit")
        return Outcome(user.id, "deposit", acc_id, amount)
    return Outcome(user.id, "duplicate", acc_id, amount)


async def claim_account(s, user: User, acc_id: str, partner=None,
                        test_accounts: dict[str, str] | None = None) -> tuple[str, list[Outcome]]:
    """Manual BinoDex ID entry. Returns (status, outcomes):
    bad | taken | not_attributed (Partner API: not our referral) | unavailable (check failed) | not_found | linked."""
    acc_id = re.sub(r"\s", "", acc_id or "")
    if not ACC_ID_RE.match(acc_id):
        return "bad", []
    test = (test_accounts or {}).get(acc_id)
    if test is not None:            # test mode: no Partner API, no uniqueness (several testers share an ID)
        user.acc_id, user.linked_at, user.checked_at = acc_id, user.linked_at or utcnow(), utcnow()
        if user.registered_at is None:
            user.registered_at = utcnow()
            await log_event(s, user, "registered")
        if user.step != "access":
            user.step = "deposit"
        outs = [await _apply(s, user, "ftd", acc_id, 0.0)] if test == "dep" else []
        return "linked", outs
    other = (await s.execute(select(User).where(User.acc_id == acc_id, User.id != user.id))).scalar_one_or_none()
    if other is not None:
        return "taken", []
    rows = (await s.execute(select(Postback).where(Postback.acc_id == acc_id, Postback.applied.is_(False),
                                                   Postback.event.in_(("reg", "ftd", "dep", "try", "com")))
                            .order_by(Postback.id))).scalars().all()
    trader = None
    if not rows and user.acc_id != acc_id:
        if partner is None:
            return "not_found", []
        from .binodex.partner_api import PartnerApiError
        try:
            trader = await partner.trader(int(acc_id))
        except PartnerApiError as exc:
            log.warning("partner check %s failed: %s", acc_id, exc.message)
            return "unavailable", []
        if trader is None:
            return "not_attributed", []
    outcomes = []
    if user.acc_id != acc_id:
        user.acc_id = acc_id
    user.linked_at = user.linked_at or utcnow()
    for pb in rows:      # registration first, then deposits, in arrival order
        pb.user_id, pb.applied = user.id, True
        outcomes.append(await _apply(s, user, pb.event, acc_id, pb.amount))
    if user.registered_at is None:
        user.registered_at = utcnow()
        await log_event(s, user, "registered")
    if user.step not in ("access",):
        user.step = "deposit"
    if trader is not None:
        user.checked_at = utcnow()
        if has_deposit(trader):
            outcomes.append(await _apply(s, user, "ftd", acc_id, 0.0))
    return "linked", outcomes


def has_deposit(trader: dict) -> bool:
    """Partner API trader card: depositsCount (or a firstDeposit record) means at least one deposit."""
    try:
        n = int(trader.get("depositsCount") or 0)
    except (TypeError, ValueError):
        n = 0
    return n > 0 or bool(trader.get("firstDeposit"))


async def recheck_deposit(s, user: User, partner) -> Outcome | None:
    """Partner API check for a linked user without a deposit. Returns the outcome when access opened."""
    from .binodex.partner_api import PartnerApiError
    if not user.acc_id or user.ftd_at is not None or partner is None:
        return None
    try:
        trader = await partner.trader(int(user.acc_id))
    except PartnerApiError as exc:
        log.info("recheck %s: %s", user.id, exc.message)
        return None
    user.checked_at = utcnow()
    if trader is not None and has_deposit(trader):
        return await _apply(s, user, "ftd", user.acc_id, 0.0)
    return None


async def due_rechecks(s, cfg: Settings) -> list[User]:
    """Linked within 14 days: every RECHECK_RECENT_MIN; older: every RECHECK_ALL_HOURS."""
    from datetime import timedelta
    from sqlalchemy import and_, or_
    now = utcnow()
    recent = now - timedelta(days=14)
    q = select(User).where(User.acc_id != "", User.ftd_at.is_(None), User.blocked.is_(False), or_(
        User.checked_at.is_(None),
        and_(User.linked_at >= recent, User.checked_at <= now - timedelta(minutes=cfg.recheck_recent_min)),
        User.checked_at <= now - timedelta(hours=cfg.recheck_all_hours)))
    return list((await s.execute(q.limit(200))).scalars())
