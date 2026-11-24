"""Errors raised by the BinoDex Broker API client.

Error body format is taken from the official broker-web (src/lib/broker.ts, src/lib/types.ts):
    {"error": {"message": str, "details": any}}
"""
from __future__ import annotations

from typing import Any


class BinoDexError(Exception):
    """Base class for every BinoDex failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BinoDexNotConfigured(BinoDexError):
    """client_id / client_secret / token key are missing on the server."""


class BinoDexUnavailable(BinoDexError):
    """Network error, timeout or 5xx: the request may or may not have reached BinoDex."""


class BinoDexApiError(BinoDexError):
    """BinoDex answered with a non-2xx status and (usually) an error body."""

    def __init__(self, message: str, status: int, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details

    @property
    def is_auth(self) -> bool:
        # 401 = token missing/expired/revoked. 403 is NOT a token problem in the Broker API: a blocked
        # account (login) or the trading grant missing ({"field": "trading"}); refreshing does not help.
        return self.status == 401

    @property
    def field(self) -> str | None:
        return self.details.get("field") if isinstance(self.details, dict) else None

    @property
    def trading_not_enabled(self) -> bool:
        return self.status == 403 and self.field == "trading"

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429


class BinoDexRelinkRequired(BinoDexError):
    """The stored refresh token no longer works: the user must link the account again."""


class BinoDexBadResponse(BinoDexError):
    """2xx answer that does not match the format used by the official broker-web."""


class RealTradingBlocked(BinoDexError):
    """A request or a response would involve the REAL account. Never retried, never ignored."""
