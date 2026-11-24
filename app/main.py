"""Entry point: bot (long polling), signal engine, deposit re-checks and the HTTP server in one process."""
from __future__ import annotations

import asyncio
import logging

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import bot as botmod
from . import db
from .binodex.market import MarketClient
from .binodex.partner_api import PartnerApi
from .config import get_settings
from .signal_engine import SignalEngine
from .web import create_app


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("main")
    cfg = get_settings()
    if not cfg.bot_token:
        raise SystemExit("BOT_TOKEN is not set")
    await db.init_db(cfg.database_url)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    partner = PartnerApi(cfg.partner_api_key, cfg.partner_api_url) if cfg.partner_api_key else None
    botmod.partner = partner
    if partner is None:
        log.warning("PARTNER_API_KEY is empty: accounts are confirmed only by postbacks")
    market = MarketClient(cfg.binodex_api_url)
    engine = SignalEngine(market, cfg)
    botmod.engine = engine
    engine.on_signal = lambda sig: botmod.push_signal(bot, sig)
    engine.on_settled = lambda rows: botmod.push_results(bot, rows)
    app = create_app(deliver=lambda out: botmod.deliver(bot, out))
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.http_host, port=cfg.http_port, log_level="info"))
    dp = botmod.build_dispatcher()
    tasks = [server.serve(), dp.start_polling(bot), engine.run_forever()]
    if partner is not None:
        tasks.append(botmod.recheck_forever(bot))
    try:
        await asyncio.gather(*tasks)
    finally:
        await bot.session.close()
        await market.aclose()
        if partner is not None:
            await partner.aclose()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
