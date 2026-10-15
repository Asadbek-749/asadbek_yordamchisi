"""Test fixtures and mock harness for offline testing."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetBusinessConnection, GetMe, SendMessage
from aiogram.types import BusinessConnection, Message, Update, User

from assistant.config import Settings, load_settings
from assistant.handlers import build_router
from assistant.service import Assistant, Outcome, build_assistant


class Clock:
    """Mock clock for deterministic time in tests."""

    def __init__(self, start: float = 1700000000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


class FakeGenerator:
    """Mock generator that returns canned replies or raises exceptions."""

    def __init__(
        self,
        responses: Optional[List[Any]] = None,
        hook: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.responses = list(responses or ["Sample AI response"])
        self.hook = hook
        self.calls: List[Tuple[str, str]] = []

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.hook:
            res = self.hook()
            if asyncio.iscoroutine(res):
                await res
        if not self.responses:
            return "Default AI response"
        val = self.responses.pop(0)
        if isinstance(val, Exception):
            raise val
        return str(val)


class RecordingSession(BaseSession):
    """Aiogram session that records requests and executes fully offline."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: List[Any] = []
        self.connections: Dict[str, BusinessConnection] = {}
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def make_request(
        self, bot: Bot, method: Any, timeout: Optional[int] = None
    ) -> Any:
        self.requests.append(method)
        if isinstance(method, GetBusinessConnection):
            conn = self.connections.get(method.business_connection_id)
            if conn is None:
                raise TelegramBadRequest(
                    method=method, message="Business connection not found"
                )
            return conn
        elif isinstance(method, SendMessage):
            return Message.model_validate(
                {
                    "message_id": 9999 + len(self.requests),
                    "date": 1700000000,
                    "chat": {"id": method.chat_id, "type": "private"},
                    "text": method.text,
                }
            )
        elif isinstance(method, GetMe):
            return User.model_validate(
                {
                    "id": 123456,
                    "is_bot": True,
                    "first_name": "AssistantBot",
                    "username": "assistant_bot",
                }
            )
        return True

    async def stream_content(
        self, url: str, timeout: Optional[int] = None, chunk_size: int = 65536
    ) -> Any:
        raise AssertionError("Network streaming is disabled in tests")


class Harness:
    """End-to-end test harness wrapping Bot, Dispatcher, and Assistant."""

    def __init__(
        self,
        tmp_path: Path,
        clock: Optional[Clock] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.clock = clock or Clock()
        self.data_dir = tmp_path / "data"
        self.profile_path = tmp_path / "profile.md"
        self.profile_path.write_text(
            "# Profile\n- Ism: John\n- Kasb: Dasturchi", encoding="utf-8"
        )

        self._overrides = {
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "OWNER_ID": 123456,
            "DATA_DIR": str(self.data_dir),
            "PROFILE_PATH": str(self.profile_path),
            "AUTO_SEND": "false",
            "OWNER_TAKEOVER_MINUTES": 15,
            "LIMIT_COOLDOWN_MINUTES": 30,
        }
        if overrides:
            self._overrides.update(overrides)

        self.settings = load_settings(
            source=self._overrides,
            require_telegram=True,
        )
        self.generator = FakeGenerator()
        self.session = RecordingSession()
        self.bot = Bot(token="123456:ABC-DEF", session=self.session)
        self.assistant = build_assistant(
            self.settings,
            generator=self.generator,
            clock=self.clock,
        )
        self.dp = Dispatcher()
        self.dp.include_router(build_router(self.assistant))

    def set_connection(
        self,
        connection_id: str = "conn1",
        user_id: int = 123456,
        is_enabled: bool = True,
        can_reply: bool = True,
    ) -> BusinessConnection:
        payload = {
            "id": connection_id,
            "user": {"id": user_id, "is_bot": False, "first_name": "Owner"},
            "user_chat_id": user_id,
            "date": 1700000000,
            "can_reply": can_reply,
            "is_enabled": is_enabled,
            "rights": {"can_reply": can_reply},
        }
        bc = BusinessConnection.model_validate(payload)
        self.session.connections[connection_id] = bc
        return bc

    async def connect(
        self,
        connection_id: str = "conn1",
        user_id: int = 123456,
        is_enabled: bool = True,
        can_reply: bool = True,
    ) -> None:
        bc = self.set_connection(
            connection_id=connection_id,
            user_id=user_id,
            is_enabled=is_enabled,
            can_reply=can_reply,
        )
        upd = Update.model_validate(
            {"update_id": 1, "business_connection": bc.model_dump()},
            context={"bot": self.bot},
        )
        await self.dp.feed_update(self.bot, upd)

    async def business_message(
        self,
        text: Optional[str] = "salom",
        from_id: int = 999,
        from_name: str = "Interlocutor",
        chat_id: int = 1001,
        message_id: int = 1,
        connection_id: str = "conn1",
        is_bot: bool = False,
        via_business_bot: bool = False,
        caption: Optional[str] = None,
        sticker: Optional[Dict[str, Any]] = None,
        voice: Optional[Dict[str, Any]] = None,
        photo: Optional[List[Dict[str, Any]]] = None,
    ) -> Outcome:
        msg_payload: Dict[str, Any] = {
            "message_id": message_id,
            "date": int(self.clock()),
            "chat": {"id": chat_id, "type": "private", "first_name": from_name},
            "from": {
                "id": from_id,
                "is_bot": is_bot,
                "first_name": from_name,
            },
            "business_connection_id": connection_id,
        }
        if text is not None:
            msg_payload["text"] = text
        if caption is not None:
            msg_payload["caption"] = caption
        if sticker is not None:
            msg_payload["sticker"] = sticker
        if voice is not None:
            msg_payload["voice"] = voice
        if photo is not None:
            msg_payload["photo"] = photo
        if via_business_bot:
            msg_payload["sender_business_bot"] = {
                "id": 888,
                "is_bot": True,
                "first_name": "BusinessBot",
            }

        upd = Update.model_validate(
            {"update_id": 2, "business_message": msg_payload},
            context={"bot": self.bot},
        )
        await self.dp.feed_update(self.bot, upd)
        return self.assistant.outcomes[-1] if self.assistant.outcomes else Outcome.error

    async def direct_message(
        self,
        text: str,
        from_id: int = 123456,
        chat_id: Optional[int] = None,
    ) -> None:
        target_chat = chat_id if chat_id is not None else from_id
        entities = []
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            entities.append(
                {"type": "bot_command", "offset": 0, "length": len(parts[0])}
            )

        msg_payload = {
            "message_id": 50,
            "date": int(self.clock()),
            "chat": {"id": target_chat, "type": "private", "first_name": "User"},
            "from": {"id": from_id, "is_bot": False, "first_name": "User"},
            "text": text,
            "entities": entities,
        }
        upd = Update.model_validate(
            {"update_id": 3, "message": msg_payload},
            context={"bot": self.bot},
        )
        await self.dp.feed_update(self.bot, upd)

    def restart(self, overrides: Optional[Dict[str, Any]] = None) -> Harness:
        new_overrides = dict(self._overrides)
        if overrides:
            new_overrides.update(overrides)
        new_harness = Harness(
            self.tmp_path,
            clock=self.clock,
            overrides=new_overrides,
        )
        new_harness.generator = self.generator
        new_harness.session = self.session
        new_harness.bot = Bot(token="123456:ABC-DEF", session=self.session)
        new_harness.dp = Dispatcher()
        new_harness.dp.include_router(build_router(new_harness.assistant))
        return new_harness


@pytest.fixture
def make_harness(tmp_path: Path) -> Callable[..., Harness]:
    def _factory(**kwargs: Any) -> Harness:
        return Harness(tmp_path, **kwargs)

    return _factory
