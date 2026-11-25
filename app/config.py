"""Settings from environment (.env). Secrets live only here, never in code or texts."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = ""
    reserve_bot_username: str = ""          # backup bot, announced if the main one is banned
    database_url: str = "sqlite+aiosqlite:///data/bdxsignal.db"

    # BinoDex partner smart link. {tg_id} and {tag} are substituted if you put them into the link
    # (only where the cabinet supports sub-parameters; without them the link is used as is).
    ref_url: str = "https://bdclick.app/smart/ZYwT6O"
    trade_url: str = ""                     # where "Open BinoDex" leads after registration; empty = ref_url
    min_deposit_usd: float = 10.0

    # Partner API (read-only): is a BinoDex account attributed to us, has it deposited.
    partner_api_key: str = ""
    partner_api_url: str = "https://api.binopartner.com/v1"
    recheck_recent_min: int = 10            # deposit re-check for accounts linked within 14 days
    recheck_all_hours: int = 3

    # Public market data (no token needed)
    binodex_api_url: str = "https://api.binodex.app/v1"

    # Signals on request: the user picks a pair (or auto) and an expiry, the bot analyses BinoDex quotes now
    signal_expiries: str = "30,60,120,300"  # seconds offered to the user (BinoDex minimum is 15)
    signal_valid_sec: int = 30              # how long after issuing the entry is still "on time"
    signal_min_strength: float = 0.35       # indicator agreement 0..1 needed to give a direction at all
    signal_min_payout: int = 80
    signal_otc: str = "all"                 # all | otc | market
    signal_pairs_shown: int = 8             # pairs offered in the picker (best payout first)
    signal_watchlist: int = 6               # pairs kept live in the background (auto pick, broadcast)
    signal_poll_sec: float = 5.0

    # Broadcast of strong entries to users who keep notifications on
    broadcast_enabled: bool = True
    broadcast_min_strength: float = 0.6
    broadcast_expiry_sec: int = 60
    broadcast_per_day: int = 4              # strong signals sent per day (all users get the same ones)
    broadcast_gap_min: int = 90             # minimum time between two broadcasts
    push_per_day: int = 5                   # per user, safety cap
    quiet_from: str = "23:00"
    quiet_to: str = "09:00"
    tz: str = "Europe/Kyiv"

    # Optional postbacks (if the BinoDex cabinet offers them): https://<host>/pb/<POSTBACK_SECRET>?...
    postback_secret: str = ""
    pb_event: str = "event"
    pb_click_id: str = "click_id"           # carries {tg_id} back
    pb_trader_id: str = "trader_id"         # BinoDex account ID
    pb_amount: str = "amount"
    pb_tag: str = "sub_id1"
    pb_tx_id: str = "tx_id"
    pb_event_reg: str = "reg"
    pb_event_ftd: str = "ftd"
    pb_event_dep: str = "dep"
    pb_event_try: str = "try"
    pb_event_com: str = "com"

    cpa_usd: float = 0.0                    # payout per FTD; 0 = RevShare (revenue from commission postbacks)

    # Subscription gate: the bot opens only after joining the channel. The bot must be an admin of the channel.
    # CHANNEL_URL = the link for the button (a private +invite link works if joining needs no approval).
    # CHANNEL = @username or the numeric id (-100...); empty for a private channel: the bot learns the id itself
    # from the first post it sees there. Both empty = no gate.
    channel: str = ""
    channel_url: str = ""

    @property
    def channel_link(self) -> str:
        if self.channel_url:
            return self.channel_url
        return f"https://t.me/{self.channel.lstrip('@')}" if self.channel.startswith("@") else ""

    @property
    def gate(self) -> bool:
        return bool(self.channel or self.channel_url)

    # Evening report to ADMIN_IDS (funnel by source, signal results, a picture for the channel)
    report_time: str = "21:00"

    # Retention for users with access: inactive 3 / 7 / 14 days, low BinoDex balance (Partner API "balance")
    low_balance_usd: float = 10.0
    low_balance_every_days: int = 5

    # Follow-up reminders for people who stopped on a funnel step (see app/reminders.py)
    reminders_enabled: bool = True
    reminders_per_day: int = 2

    # Test accounts for walking the funnel without a real BinoDex account: "ID:reg,ID:dep"
    # (reg = registered without a deposit, dep = with a deposit). Also enables /reset for everyone.
    # Leave EMPTY in production.
    test_accounts: str = ""

    @property
    def test_map(self) -> dict[str, str]:
        out = {}
        for part in self.test_accounts.split(","):
            acc, _, kind = part.strip().partition(":")
            if acc.isdigit() and kind in ("reg", "dep"):
                out[acc] = kind
        return out

    admin_token: str = ""                   # bearer token for /admin API
    admin_ids: list[int] = Field(default_factory=list)
    support_url: str = ""
    video_howto_url: str = ""
    video_new_account_url: str = ""

    http_host: str = "0.0.0.0"
    http_port: int = 8080

    @property
    def expiries(self) -> list[int]:
        out = sorted({int(x) for x in self.signal_expiries.split(",") if x.strip().isdigit() and int(x) >= 15})
        return out or [60]

    @property
    def open_url(self) -> str:
        return self.trade_url or self.ref_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
