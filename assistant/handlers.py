"""Telegram event handlers and router configuration."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BusinessConnection, Message

from assistant.service import Assistant

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "business_connection", "business_message"]


def build_router(assistant: Assistant) -> Router:
    """Build and configure the main aiogram router."""
    router = Router(name="assistant_router")

    # 1. Business messages
    @router.business_message()
    async def handle_business_message(message: Message) -> None:
        await assistant.on_business_message(message.bot, message)

    # 2. Business connections
    @router.business_connection()
    async def handle_business_connection(connection: BusinessConnection) -> None:
        await assistant.on_business_connection(connection)

    # 3. Direct private commands from owner
    @router.message(
        F.chat.type == "private",
        F.from_user.id == assistant.settings.owner_id,
        Command("pause"),
    )
    async def cmd_pause(message: Message) -> None:
        assistant.pause()
        await message.answer("⏸ Bot faoliyati vaqtincha to'xtatildi (pauzaga qo'yildi).")

    @router.message(
        F.chat.type == "private",
        F.from_user.id == assistant.settings.owner_id,
        Command("resume"),
    )
    async def cmd_resume(message: Message) -> None:
        assistant.resume()
        await message.answer("▶️ Bot qayta ishga tushirildi (pauza, limit va taymerlar tozalandi).")

    @router.message(
        F.chat.type == "private",
        F.from_user.id == assistant.settings.owner_id,
        Command("status"),
    )
    async def cmd_status(message: Message) -> None:
        await message.answer(assistant.status_text())

    @router.message(
        F.chat.type == "private",
        F.from_user.id == assistant.settings.owner_id,
        Command("start"),
    )
    async def cmd_owner_start(message: Message) -> None:
        await message.answer(assistant.start_text())

    # 4. Direct /start from non-owners
    @router.message(
        F.chat.type == "private",
        Command("start"),
    )
    async def cmd_other_start(message: Message) -> None:
        await message.answer(
            "Assalomu alaykum! Bu shaxsiy yordamchi bot bo'lib, faqat o'z egasi uchun ishlaydi. "
            "Bu yerda to'g'ridan-to'g'ri suhbatlashib bo'lmaydi. Iltimos, egasiga shaxsiy akkaunti orqali yozing."
        )

    # 5. Direct messages from non-owners: silently ignored
    @router.message(F.chat.type == "private")
    async def handle_other_private(message: Message) -> None:
        # Silently ignore
        pass

    return router
