import itertools
from types import SimpleNamespace

import pytest

from app import db
from app.config import Settings, get_settings


@pytest.fixture
def cfg(monkeypatch):
    s = Settings(_env_file=None, bot_token="1:x", postback_secret="s" * 24, admin_token="adm" * 8,
                 ref_url="https://bd.example/smart/X?click_id={tg_id}&sub_id1={tag}", min_deposit_usd=10,
                 cpa_usd=100, quiet_from="00:00", quiet_to="00:00", tz="Europe/Kyiv")
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: s)
    for mod in ("app.bot", "app.web"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: s)
    return s


@pytest.fixture
async def database(tmp_path):
    await db.init_db(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    yield
    await db.close_db()


class FakeBot:
    """Records sends and deletes instead of calling Telegram."""
    def __init__(self):
        self.sent, self.deleted, self._ids = [], [], itertools.count(100)
        self.members = {}

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        mid = next(self._ids)
        self.sent.append((chat_id, text, reply_markup, mid))
        self.kw = kw

        class M:
            message_id = mid
        return M()

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        msg = await self.send_message(chat_id, caption, reply_markup, **kw)
        self.photos = getattr(self, "photos", []) + [(chat_id, str(getattr(photo, "path", None) or getattr(photo, "filename", None) or photo))]
        return msg

    members: dict = {}
    members_error = False

    async def get_chat_member(self, chat_id, user_id):
        from aiogram.exceptions import TelegramBadRequest
        self.checked_in = chat_id
        if self.members_error:
            raise TelegramBadRequest(method=None, message="chat not found")
        return SimpleNamespace(status=self.members.get(user_id, "left"))

    async def delete_message(self, chat_id, mid):
        self.deleted.append((chat_id, mid))


@pytest.fixture
def bot():
    return FakeBot()


class FakePartner:
    """Partner API stand-in: traders = {binodex_id: card}; missing id = not attributed (404 -> None)."""
    def __init__(self, traders=None, fail=False):
        self.traders, self.fail, self.calls = dict(traders or {}), fail, []

    async def trader(self, uid):
        from app.binodex.partner_api import PartnerApiError
        self.calls.append(uid)
        if self.fail:
            raise PartnerApiError("down", 503)
        return self.traders.get(uid)
