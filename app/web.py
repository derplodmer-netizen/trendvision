"""HTTP side: postback receiver and the admin API (texts, funnel by tag, ad spend)."""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import db
from .config import get_settings
from .models import Event, Postback, Signal, Spend, TextOverride, User, utcnow
from .services import Outcome, clean_tag, handle_postback
from .texts import default, text_keys

log = logging.getLogger(__name__)

Deliver = Callable[[Outcome], Awaitable[None]]

FUNNEL = ["start", "subscribed", "value", "join_from_broadcast", "register", "registered", "dep_try", "ftd"]


class TextIn(BaseModel):
    body: str = Field(max_length=4000)


class SpendIn(BaseModel):
    tag: str
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    usd: float = Field(ge=0)


def create_app(deliver: Deliver | None = None) -> FastAPI:
    app = FastAPI(title="BinoDex signals bot", docs_url=None, redoc_url=None, openapi_url=None)

    # ------------------------------------------------------------ postbacks
    @app.api_route("/pb/{secret}", methods=["GET", "POST"])
    async def postback(secret: str, request: Request):
        cfg = get_settings()
        if not cfg.postback_secret or not hmac.compare_digest(secret, cfg.postback_secret):
            raise HTTPException(404)
        params = {k: str(v)[:256] for k, v in request.query_params.items()}
        if request.method == "POST":
            try:
                form = await request.form()
                params.update({k: str(v)[:256] for k, v in form.items()})
            except Exception:        # not a form body: query params are enough
                pass
        async with db.session() as s:
            out = await handle_postback(s, cfg, params)
            if out.action != "duplicate":
                await s.commit()
        log.info("postback %s -> %s user=%s", params.get(cfg.pb_event), out.action, out.user_id)
        if deliver is not None and out.action in ("registered", "access"):
            try:
                await deliver(out)
            except Exception:
                log.exception("deliver failed for %s", out)
        return {"ok": True, "result": out.action}

    # ------------------------------------------------------------ admin
    def admin(authorization: str = Header("")) -> None:
        token = get_settings().admin_token
        if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401)


    @app.get("/admin/texts", dependencies=[Depends(admin)])
    async def list_texts(lang: str = "ru"):
        async with db.session() as s:
            rows = {r.key: r.body for r in (await s.execute(select(TextOverride))).scalars()}
        return [{"key": k, "default": default(lang, k), "a": rows.get(f"{lang}:{k}"), "b": rows.get(f"{lang}:{k}:b")}
                for k in text_keys()]

    @app.put("/admin/texts/{key}", dependencies=[Depends(admin)])
    async def put_text(key: str, body: TextIn, lang: str = "ru", variant: str = ""):
        if key not in text_keys() or variant not in ("", "b"):
            raise HTTPException(404)
        full = f"{lang}:{key}" + (f":{variant}" if variant else "")
        async with db.session() as s:
            row = await s.get(TextOverride, full)
            if not body.body.strip():
                if row:
                    await s.delete(row)          # empty = back to default
            elif row:
                row.body, row.updated_at = body.body, utcnow()
            else:
                s.add(TextOverride(key=full, body=body.body))
            await s.commit()
        return {"ok": True}


    @app.put("/admin/spend", dependencies=[Depends(admin)])
    async def put_spend(body: SpendIn):
        tag = clean_tag(body.tag)
        async with db.session() as s:
            row = (await s.execute(select(Spend).where(Spend.tag == tag, Spend.day == body.day))).scalar_one_or_none()
            if row:
                row.usd = body.usd
            else:
                s.add(Spend(tag=tag, day=body.day, usd=body.usd))
            await s.commit()
        return {"ok": True}

    @app.get("/admin/funnel", dependencies=[Depends(admin)])
    async def funnel(days: int = 7, by: str = "tag"):
        cfg = get_settings()
        since = utcnow() - timedelta(days=max(1, min(days, 365)))
        col = Event.ab if by == "ab" else Event.tag
        async with db.session() as s:
            rows = (await s.execute(
                select(col, Event.name, func.count(func.distinct(Event.user_id)))
                .where(Event.created_at >= since).group_by(col, Event.name))).all()
            ucol = User.ab if by == "ab" else User.tag
            dep = (await s.execute(
                select(ucol, func.sum(User.deposits_total), func.sum(User.commission_usd))
                .where(User.ftd_at >= since).group_by(ucol))).all()
            spend = {} if by == "ab" else dict((await s.execute(
                select(Spend.tag, func.sum(Spend.usd)).where(Spend.day >= since.strftime("%Y-%m-%d"))
                .group_by(Spend.tag))).all())
        table: dict[str, dict] = {}
        for key, name, n in rows:
            table.setdefault(key or "organic", {})[name] = n
        deposits = {(k or "organic"): float(v or 0) for k, v, _c in dep}
        commission = {(k or "organic"): float(c or 0) for k, _v, c in dep}
        out = []
        for key in sorted(set(table) | set(spend)):
            c = table.get(key, {})
            steps = {name: c.get(name, 0) for name in FUNNEL}
            ftd = steps["ftd"]
            cost = float(spend.get(key, 0))
            # CPA: payout per FTD. RevShare: commission postbacks of this cohort (FTD in the period).
            revenue = round(ftd * cfg.cpa_usd if cfg.cpa_usd else commission.get(key, 0.0), 2)
            out.append({
                by: key, **steps,
                "cr_start_reg": _pct(steps["registered"], steps["start"]),
                "cr_reg_ftd": _pct(ftd, steps["registered"]),
                "spend_usd": cost,
                "cost_per_ftd": round(cost / ftd, 2) if ftd and cost else None,
                "deposits_usd": round(deposits.get(key, 0.0), 2),
                "revenue_usd": revenue,
                "roi_pct": round((revenue - cost) / cost * 100, 1) if cost else None,
            })
        return {"since": since.isoformat(timespec="minutes"), "rows": out}

    @app.get("/admin/postbacks", dependencies=[Depends(admin)])
    async def postbacks(limit: int = 50):
        async with db.session() as s:
            rows = (await s.execute(select(Postback).order_by(Postback.id.desc()).limit(min(limit, 500)))).scalars()
            return [{"id": r.id, "event": r.event, "acc_id": r.acc_id, "user_id": r.user_id, "amount": r.amount,
                     "applied": r.applied, "at": r.created_at.isoformat(timespec="seconds")} for r in rows]

    @app.get("/admin/signals", dependencies=[Depends(admin)])
    async def signals(limit: int = 50):
        from .signal_engine import stats
        async with db.session() as s:
            rows = (await s.execute(select(Signal).order_by(Signal.id.desc()).limit(min(limit, 500)))).scalars().all()
            return {"d1": await stats(s, 1), "d7": await stats(s, 7), "d30": await stats(s, 30),
                    "items": [{"id": r.id, "symbol": r.symbol, "otc": r.is_otc, "direction": r.direction,
                               "expiry_sec": r.expiry_sec, "payout": r.payout, "strength": r.strength,
                               "entry": r.entry_price, "close": r.close_price, "result": r.result,
                               "at": r.created_at.isoformat(timespec="seconds")} for r in rows]}

    @app.get("/health")
    async def health():
        return {"ok": True, "time": datetime.utcnow().isoformat(timespec="seconds")}

    return app


def _pct(a: int, b: int) -> float | None:
    return round(a / b * 100, 1) if b else None
