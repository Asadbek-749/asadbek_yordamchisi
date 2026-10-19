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
    def _format_sender(msg: NargisMessage) -> str:
        contact = getattr(msg, "contact", None)
        if contact:
            parts = [contact.first_name or "", contact.last_name or ""]
            full = " ".join(p for p in parts if p).strip()
            if full and contact.username:
                return f"{full} (@{contact.username})"
            if contact.username:
                return f"@{contact.username}"
            if full:
                return full
            if contact.telegram_id:
                return f"Foydalanuvchi (ID: {contact.telegram_id})"
        if msg.sender_name or msg.sender_username:
            if msg.sender_name and msg.sender_username:
                return f"{msg.sender_name} (@{msg.sender_username})"
            if msg.sender_username:
                return f"@{msg.sender_username}"
            return msg.sender_name or "Noma'lum"
        if msg.chat_id:
            return f"Chat ID: {msg.chat_id}"
        return "Noma'lum foydalanuvchi"

    # ─────────────────────────────────────────────
    # /messages — so'nggi kiruvchi xabarlar
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("messages"))
    @router.message(is_owner, F.text == "📥 Xabarlar")
    @router.message(is_owner, F.text == "📥 Kiruvchi xabarlar")
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
            is_del = getattr(msg, "is_deleted", False) or msg.status == "DELETED"
            status_icon = "🗑️" if is_del else ("🔴" if msg.status == "NEW" else "✅")
            deleted_text = " <i>[O'chirilgan]</i>" if is_del else ""
            sender = _format_sender(msg)
            preview = msg.content[:60].replace("\n", " ")
            if len(msg.content) > 60:
                preview += "..."
            lines.append(
                f"{i}. {urgency_icon} {status_icon} <b>#{msg.id}</b>{deleted_text}\n"
                f"   👤 <b>Kimdan:</b> {sender}\n"
                f"   🏷 {msg.intent or '?'} | {msg.urgency}\n"
                f"   💬 <i>«{preview}»</i>\n"
            )

        await message.answer("\n".join(lines), parse_mode="HTML")

    # ─────────────────────────────────────────────
    # /important — muhim / critical xabarlar
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("important"))
    @router.message(is_owner, F.text == "🔥 Muhim xabarlar")
    async def cmd_important(message: Message) -> None:
        messages = await contact_svc.list_important_messages(limit=10)
        if not messages:
            await message.answer("✅ Hozirda muhim yoki kritik xabarlar yo'q.")
            return

        lines = ["🔥 <b>Muhim va kritik xabarlar:</b>\n"]
        for msg in messages:
            urgency_icon = "🚨" if msg.urgency == "CRITICAL" else "🔥"
            sender = _format_sender(msg)
            preview = msg.content[:120].replace("\n", " ")
            if len(msg.content) > 120:
                preview += "..."
            deadline = f"\n   ⏰ Deadline: {msg.deadline_hint}" if msg.deadline_hint else ""
            reason = f"\n   📌 Sabab: {msg.reason_summary}" if msg.reason_summary else ""
            lines.append(
                f"{urgency_icon} <b>#{msg.id}</b> | {msg.urgency} | {msg.status}\n"
                f"   👤 <b>Kimdan:</b> {sender}\n"
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
    @router.message(is_owner, F.text == "🏆 Nargis ball")
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

    # ─────────────────────────────────────────────
    # Dispatch callbacks: [🚀 Ha, yuborilsin] / [❌ Bekor qilish]
    # ─────────────────────────────────────────────
    @router.callback_query(is_owner, F.data.startswith("dispatch:send:"))
    async def cb_dispatch_send(callback: CallbackQuery) -> None:
        draft_id = callback.data.split(":")[-1]
        from nargis.dispatcher import get_message_dispatcher
        dispatcher = get_message_dispatcher()
        success, report = await dispatcher.execute_send(draft_id, callback.bot)
        await callback.answer("✅ Yuborildi!" if success else "⚠️ Xatolik")
        try:
            await callback.message.edit_text(report, parse_mode="HTML")
        except Exception:
            await callback.message.answer(report, parse_mode="HTML")

    @router.callback_query(is_owner, F.data.startswith("dispatch:cancel:"))
    async def cb_dispatch_cancel(callback: CallbackQuery) -> None:
        draft_id = callback.data.split(":")[-1]
        from nargis.dispatcher import get_message_dispatcher
        dispatcher = get_message_dispatcher()
        draft = dispatcher._drafts.get(draft_id)
        if draft:
            draft.status = "CANCELLED"
        await callback.answer("❌ Bekor qilindi.")
        try:
            await callback.message.edit_text("❌ <b>Xabar yuborish bekor qilindi.</b>", parse_mode="HTML")
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # /addcontact — Yangi kontakt qo'shish yoki yangilash
    # ─────────────────────────────────────────────
    @router.message(is_owner, Command("addcontact"))
    async def cmd_addcontact(message: Message) -> None:
        args = (message.text or "").split()[1:]
        if not args:
            await message.answer(
                "ℹ️ <b>Foydalanish:</b> <code>/addcontact &lt;Ism&gt; &lt;Telegram_ID yoki @username&gt;</code>\n\n"
                "<b>Misollar:</b>\n"
                "• <code>/addcontact Oyattullo 123456789</code>\n"
                "• <code>/addcontact Oyattullo @oyatullo_dev</code>\n"
                "• <code>/addcontact Oyattullo 123456789 @oyatullo_dev</code>",
                parse_mode="HTML",
            )
            return

        name = args[0]
        telegram_id = None
        username = None

        for a in args[1:]:
            if a.startswith("@"):
                username = a.lstrip("@")
            elif a.isdigit():
                telegram_id = int(a)
            elif not username:
                username = a

        contact = await contact_svc.add_or_update_contact(
            first_name=name,
            telegram_id=telegram_id,
            username=username,
        )

        user_disp = f"@{contact.username}" if contact.username else "—"
        id_disp = f"<code>{contact.telegram_id}</code>" if contact.telegram_id and contact.telegram_id > 0 else "kiritilmagan"

        await message.answer(
            f"✅ <b>Kontakt muvaffaqiyatli saqlandi!</b>\n\n"
            f"👤 <b>Ism:</b> {contact.first_name}\n"
            f"🔹 <b>Username:</b> {user_disp}\n"
            f"🆔 <b>Telegram ID:</b> {id_disp}\n\n"
            f"<i>Endi ushbu kontaktga ovozli yoki matnli xabar yuborishingiz mumkin.</i>",
            parse_mode="HTML",
        )

    # ─────────────────────────────────────────────
    # Forwarded message from owner: auto-register contact & link to pending draft!
    # ─────────────────────────────────────────────
    @router.message(is_owner, F.forward_origin | F.forward_from | F.forward_sender_name)
    async def handle_forwarded_contact(message: Message) -> None:
        """When owner forwards any message from a chat to Nargis, automatically register that contact."""
        from nargis.dispatcher import get_message_dispatcher
        dispatcher = get_message_dispatcher()

        target_id: Optional[int] = None
        target_name: Optional[str] = None
        target_username: Optional[str] = None

        origin = getattr(message, "forward_origin", None)
        if origin:
            sender_user = getattr(origin, "sender_user", None)
            if sender_user:
                target_id = getattr(sender_user, "id", None)
                target_name = getattr(sender_user, "full_name", None) or getattr(sender_user, "first_name", None)
                target_username = getattr(sender_user, "username", None)
            elif getattr(origin, "sender_user_name", None):
                target_name = origin.sender_user_name
        elif getattr(message, "forward_from", None):
            ff = message.forward_from
            target_id = ff.id
            target_name = ff.full_name or ff.first_name
            target_username = ff.username
        elif getattr(message, "forward_sender_name", None):
            target_name = message.forward_sender_name

        if not target_name and not target_id:
            await message.answer("⚠️ Uzatilgan (forward qilingan) xabardan kontakt ma'lumotlarini ajratib bo'lmadi.")
            return

        display_name = target_name or f"User_{target_id}"
        contact = await contact_svc.add_or_update_contact(
            first_name=display_name,
            telegram_id=target_id,
            username=target_username,
        )

        user_disp = f"@{contact.username}" if contact.username else "—"
        id_disp = f"<code>{contact.telegram_id}</code>" if (contact.telegram_id and contact.telegram_id > 0) else "Aniqlanmagan"

        # Check if there is a pending message draft waiting for contact info
        draft = dispatcher.get_pending_draft(owner_id)
        if draft and draft.status == "WAITING_INFO":
            draft.contact_id = contact.id
            draft.recipient_telegram_id = contact.telegram_id
            draft.recipient_name = contact.first_name
            draft.recipient_username = contact.username
            draft.status = "PENDING_CONFIRMATION"

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🚀 Ha, yuborilsin", callback_data=f"dispatch:send:{draft.draft_id}"),
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"dispatch:cancel:{draft.draft_id}"),
                    ]
                ]
            )

            card = (
                f"✅ <b>Kontakt chatdan topildi va bog'landi!</b>\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {contact.first_name} ({user_disp})\n"
                f"🆔 <b>Telegram ID:</b> {id_disp}\n\n"
                f"💬 <b>Yetkaziladigan xabar:</b>\n"
                f"<i>«{draft.message_text}»</i>\n\n"
                f"Ushbu xabarni <b>{contact.first_name}</b>ga yuboraymi?\n"
                f"<i>(«Jo'nat» deb yozing yoki tugmani bosing)</i>"
            )
            await message.answer(card, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(
                f"✅ <b>Yangi kontakt chatdan muvaffaqiyatli saqlandi!</b>\n\n"
                f"👤 <b>Ism:</b> {contact.first_name}\n"
                f"🔹 <b>Username:</b> {user_disp}\n"
                f"🆔 <b>Telegram ID:</b> {id_disp}\n\n"
                f"<i>Endi Nargisga: «{contact.first_name}ga falon xabarni yubor» deb buyruq berishingiz mumkin!</i>",
                parse_mode="HTML",
            )

    return router

