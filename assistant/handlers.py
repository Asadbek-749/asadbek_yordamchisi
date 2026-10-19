"""Telegram event handlers and router configuration."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BusinessConnection, Message

from assistant.service import Assistant

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "business_connection", "business_message", "deleted_business_messages", "callback_query"]


def build_router(assistant: Assistant) -> Router:
    """Build and configure the main aiogram router."""
    router = Router(name="assistant_router")

    # 1. Business messages
    @router.business_message()
    async def handle_business_message(message: Message) -> None:
        await assistant.on_business_message(message.bot, message)

    # 1.5 Deleted business messages monitoring
    @router.deleted_business_messages()
    async def handle_deleted_business_messages(event: Any) -> None:
        if hasattr(assistant, "on_deleted_business_messages"):
            await assistant.on_deleted_business_messages(event.bot, event)

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
        from discipline.keyboards import main_reply_keyboard
        await message.answer(
            assistant.start_text(),
            parse_mode="HTML",
            reply_markup=main_reply_keyboard(),
        )

    # 4. Direct /start from non-owners (clients, students, guests)
    @router.message(
        F.chat.type == "private",
        Command("start"),
    )
    async def cmd_other_start(message: Message) -> None:
        reply_markup = None
        if assistant.settings.webapp_url:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚀 Qabul Mini App-ni ochish",
                            web_app=WebAppInfo(url=assistant.settings.webapp_url),
                        )
                    ]
                ]
            )

        await message.answer(
            "🌸 <b>Assalomu alaykum!</b>\n\n"
            "Men <b>Asadbek ustozning shaxsiy yordamchi boti — AI assistentiman</b>. Ismim <b>Nargis</b> 🤖\n\n"
            "Asadbek ustoz hozir darsda yoki ish bilan band bo'lishlari mumkin. "
            "Sizga qanday yordam bera olaman?\n\n"
            "💬 <i>(Kurslar, konsultatsiyalar, hamkorlik yoki boshqa savollaringiz bo'lsa, "
            "bemalol yozing. Men xabaringizni ustozga darhol yetkazaman!)</i>",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    # 5. Direct messages from users or owner (not caught by commands)
    @router.message(F.chat.type == "private")
    async def handle_direct_private(message: Message) -> None:
        await assistant.on_direct_private_message(message.bot, message)

    return router
