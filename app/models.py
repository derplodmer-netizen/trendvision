from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)          # Telegram user id
    username: Mapped[str] = mapped_column(String(64), default="")
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    tag: Mapped[str] = mapped_column(String(64), default="", index=True)   # first ad tag, never changed
    ab: Mapped[str] = mapped_column(String(1), default="a")                # A/B group, fixed at /start
    step: Mapped[str] = mapped_column(String(16), default="welcome")
    acc_id: Mapped[str] = mapped_column(String(32), default="", index=True)    # BinoDex account ID
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ftd_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ftd_amount: Mapped[float] = mapped_column(Float, default=0.0)
    deposits_total: Mapped[float] = mapped_column(Float, default=0.0)
    deposits_count: Mapped[int] = mapped_column(Integer, default=0)
    commission_usd: Mapped[float] = mapped_column(Float, default=0.0)   # from "Комиссия" postbacks
    dep_try_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)      # account ID confirmed
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)     # last Partner API check
    notify: Mapped[bool] = mapped_column(default=True)                               # signal pushes on/off
    free_signal_at: Mapped[float] = mapped_column(Float, default=0.0)                # last free signal (unix)
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # channel subscription seen
    msg_ids: Mapped[list] = mapped_column(JSON, default=list)              # bot messages to delete on next step
    blocked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Postback(Base):
    """Every incoming postback, as received. `dedup` makes repeats harmless."""
    __tablename__ = "postbacks"
    __table_args__ = (UniqueConstraint("dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup: Mapped[str] = mapped_column(String(160))
    event: Mapped[str] = mapped_column(String(16))          # reg / ftd / dep / unknown
    acc_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    tag: Mapped[str] = mapped_column(String(64), default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    applied: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    """Funnel events for the dashboard: start, value, reg_click, registered, ftd, ..."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    tag: Mapped[str] = mapped_column(String(64), default="", index=True)
    ab: Mapped[str] = mapped_column(String(1), default="a")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TextOverride(Base):
    """Admin-edited step texts. Missing key = default from texts.py."""
    __tablename__ = "texts"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)        # e.g. "ru:welcome" or "ru:welcome:b"
    body: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Spend(Base):
    """Ad spend per tag per day, entered in admin (CSV import later)."""
    __tablename__ = "spend"
    __table_args__ = (UniqueConstraint("tag", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(64))
    day: Mapped[str] = mapped_column(String(10))            # YYYY-MM-DD
    usd: Mapped[float] = mapped_column(Float, default=0.0)


class Signal(Base):
    """One published signal and its result, computed from BinoDex quotes. Rows are never edited by hand."""
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(10), default="request", index=True)   # request | broadcast
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # who asked (request)
    asset_id: Mapped[int] = mapped_column(BigInteger, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    is_otc: Mapped[bool] = mapped_column(default=False)
    direction: Mapped[str] = mapped_column(String(4))          # up | down
    expiry_sec: Mapped[int] = mapped_column(Integer)
    payout: Mapped[int] = mapped_column(Integer)
    strength: Mapped[float] = mapped_column(Float)              # indicator agreement 0..1
    rsi: Mapped[float] = mapped_column(Float, default=50.0)
    ema_fast: Mapped[float] = mapped_column(Float, default=0.0)     # EMA 8 of 5-second closes at entry
    ema_slow: Mapped[float] = mapped_column(Float, default=0.0)     # EMA 21
    momentum: Mapped[float] = mapped_column(Float, default=0.0)     # relative change over the last 20 closes
    best_move: Mapped[float | None] = mapped_column(Float, nullable=True)    # max move in our direction, relative
    worst_move: Mapped[float | None] = mapped_column(Float, nullable=True)   # max move against us, relative
    entry_price: Mapped[float] = mapped_column(Float)
    entry_ts: Mapped[float] = mapped_column(Float)
    expires_ts: Mapped[float] = mapped_column(Float, index=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(8), default="pending", index=True)   # pending|win|loss|refund|void
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Delivery(Base):
    """Which user got which signal push (to reply with the result under it)."""
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("signal_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)



class KV(Base):
    """Small values the bot learns at runtime (e.g. the gate channel id)."""
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
