"""Nargis owner inbox handlers — /messages, /contacts, /important, /score commands."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from nargis.service import NargisContactService, NargisDisciplineScoreService

logger = logging.getLogger(__name__)


def build_nargis_owner_router(owner_id: int) -> Router:
    """Build aiogram router for Nargis owner inbox and score commands."""
    router = Router(name="nargis_owner_router")
    is_owner = F.from_user.id == owner_id

    contact_svc = NargisContactService()
    score_svc = NargisDisciplineScoreService()

    # ─────────────────────────────────────────────
    # /messages — so'nggi kiruvchi xabarlar
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("messages"))
    async def cmd_messages(message: Message) -> None:
        messages = await contact_svc.list_all_messages(limit=15)
        if not messages:
            await message.answer("📥 Hozircha kiruvchi xabarlar yo'q.")
            return

        lines = ["📥 <b>So'nggi kiruvchi xabarlar:</b>\n"]
        for i, msg in enumerate(messages, 1):
            urgency_icon = {
                "CRITICAL": "🚨",
                "HIGH": "🔥",
                "NORMAL": "📩",
                "LOW": "💬",
            }.get(msg.urgency, "📩")
            status_icon = "🔴" if msg.status == "NEW" else "✅"
            preview = msg.content[:60].replace("\n", " ")
            if len(msg.content) > 60:
                preview += "..."
            lines.append(
                f"{i}. {urgency_icon} {status_icon} <b>#{msg.id}</b>\n"
                f"   {msg.intent or '?'} | {msg.urgency}\n"
                f"   <i>{preview}</i>\n"
            )

        await message.answer("\n".join(lines), parse_mode="HTML")

    # ─────────────────────────────────────────────
    # /important — muhim / critical xabarlar
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("important"))
    async def cmd_important(message: Message) -> None:
        messages = await contact_svc.list_important_messages(limit=10)
        if not messages:
            await message.answer("✅ Hozirda muhim yoki kritik xabarlar yo'q.")
            return

        lines = ["🔥 <b>Muhim va kritik xabarlar:</b>\n"]
        for msg in messages:
            urgency_icon = "🚨" if msg.urgency == "CRITICAL" else "🔥"
            preview = msg.content[:120].replace("\n", " ")
            if len(msg.content) > 120:
                preview += "..."
            deadline = f"\n   ⏰ Deadline: {msg.deadline_hint}" if msg.deadline_hint else ""
            reason = f"\n   📌 Sabab: {msg.reason_summary}" if msg.reason_summary else ""
            lines.append(
                f"{urgency_icon} <b>#{msg.id}</b> | {msg.urgency} | {msg.status}\n"
                f"   <i>{preview}</i>"
                f"{deadline}{reason}\n"
            )

        await message.answer("\n".join(lines), parse_mode="HTML")

    # ─────────────────────────────────────────────
    # /contacts — foydalanuvchilar ro'yxati
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("contacts"))
    async def cmd_contacts(message: Message) -> None:
        contacts = await contact_svc.list_contacts(limit=20)
        if not contacts:
            await message.answer("👥 Hozircha kontaktlar yo'q.")
            return

        lines = ["👥 <b>Kontaktlar:</b>\n"]
        for c in contacts:
            name = c.first_name or "Noma'lum"
            username = f"@{c.username}" if c.username else "—"
            status_icon = {
                "NEW": "🔵",
                "CONTACTED": "🟡",
                "QUALIFIED": "🟢",
                "WAITING": "⏳",
                "CONVERTED": "✅",
                "LOST": "❌",
                "ARCHIVED": "🗃",
            }.get(c.lead_status, "⚪")
            lines.append(
                f"{status_icon} <b>{name}</b> ({username})\n"
                f"   Status: {c.lead_status} | Xabarlar: {c.message_count}\n"
                f"   Intent: {c.last_intent or '?'} | Score: {c.importance_score}\n"
            )

        await message.answer("\n".join(lines), parse_mode="HTML")

    # ─────────────────────────────────────────────
    # /score — intizom bali
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("score"))
    @router.message(is_owner, F.text == "🏆 Intizom bali")
    async def cmd_score(message: Message) -> None:
        score, level = await score_svc.get_score(owner_id)
        summary = score_svc.score_summary(score, level)

        level_descriptions = {
            "ELITE": "🏆 Ajoyib! Davom eting.",
            "STRONG": "💪 Yaxshi. Barqarorlikni saqlang.",
            "NORMAL": "📊 O'rtacha. Rivojlanish uchun joy bor.",
            "WEAK": "⚠️ Zaif. E'tibor qarating.",
            "CRITICAL": "🔴 Kritik holat. Darhol chora ko'ring.",
            "DISCIPLINE_FAILURE": "🚨 Intizom buzildi. Jiddiy muammo.",
        }
        desc = level_descriptions.get(level, "")

        text = (
            f"🏆 <b>Nargis — Intizom Bali</b>\n\n"
            f"{summary}\n\n"
            f"{desc}\n\n"
            f"📊 Ballar tizimi:\n"
            f"• 90–100: ELITE\n"
            f"• 80–89: STRONG\n"
            f"• 70–79: NORMAL\n"
            f"• 50–69: WEAK\n"
            f"• 30–49: CRITICAL\n"
            f"• 0–29: DISCIPLINE FAILURE"
        )
        await message.answer(text, parse_mode="HTML")

    # ─────────────────────────────────────────────
    # Callback: "Ko'rildi" button on owner notifications
    # ─────────────────────────────────────────────
    @router.callback_query(is_owner, F.data == "nargis:ack")
    async def cb_nargis_ack(callback: CallbackQuery) -> None:
        await callback.answer("✅ Ko'rildi deb belgilandi.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @router.callback_query(is_owner, F.data == "nargis:mark_important")
    async def cb_nargis_important(callback: CallbackQuery) -> None:
        await callback.answer("🔥 Muhim deb belgilandi.")

    return router
