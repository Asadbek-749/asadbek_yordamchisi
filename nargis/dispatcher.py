"""Nargis message dispatch system — sending real messages to contacts upon owner's voice or text commands."""
from __future__ import annotations

import datetime
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from discipline.models import NargisContact
from nargis.service import NargisContactService

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Recipient name cleaning & Uzbek suffix stemming
# ─────────────────────────────────────────────────────────────────────────────

def clean_uzbek_recipient(name: str) -> str:
    """
    Clean Uzbek noun cases/suffixes from contact names.
    Examples:
        'Oyattulaga' -> 'Oyattula'
        'Oyattulloga' -> 'Oyattullo'
        'Aliga' -> 'Ali'
        'Azizbekka' -> 'Azizbek'
        'Ulug'bekka' -> 'Ulug'bek'
        'Boburga' -> 'Bobur'
        'Oyattullo akaga' -> 'Oyattullo'
    """
    name = name.strip().strip(",.:;!?\"'«»")
    if not name:
        return ""
    if name.startswith("@"):
        return name

    # Remove titles/honorifics
    honorifics = [
        " akaga", " ukaga", " opaga", " singilga", " ustozga",
        " aka", " uka", " opa", " singil", " ustoz", " o'rtoq", " do'stim"
    ]
    lower = name.lower()
    for h in honorifics:
        if lower.endswith(h):
            name = name[:-len(h)].strip()
            lower = name.lower()
            break

    # Strip dative, accusative, genitive, locative, ablative suffixes
    suffixes = ["ning", "dan", "dagi", "ga", "ka", "qa", "ge", "da", "ni"]
    for s in suffixes:
        if lower.endswith(s) and len(lower) > len(s) + 2:
            name = name[:-len(s)].strip()
            break

    return name.strip()


@dataclass
class MessageDispatchCommand:
    action: str  # "SEND", "CONFIRM", "CANCEL", "STATUS", "CORRECT", "PROVIDE_RECIPIENT", "NONE"
    recipient: Optional[str] = None
    message: Optional[str] = None
    raw_text: str = ""


@dataclass
class MessageDispatchDraft:
    draft_id: str
    owner_id: int
    recipient_query: str
    message_text: str
    status: str  # "PENDING_CONFIRMATION", "WAITING_INFO", "SENT", "CANCELLED"
    created_at: float
    contact_id: Optional[int] = None
    recipient_telegram_id: Optional[int] = None
    recipient_name: Optional[str] = None
    recipient_username: Optional[str] = None
    business_connection_id: Optional[str] = None
    sent_at: Optional[float] = None
    error_message: Optional[str] = None


class NargisMessageDispatcher:
    """Manages parsing, drafting, contact resolution, and physical dispatch of Telegram messages."""

    def __init__(self, contact_service: Optional[NargisContactService] = None) -> None:
        self.contact_service = contact_service or NargisContactService()
        self._drafts: dict[str, MessageDispatchDraft] = {}
        self._last_draft_by_owner: dict[int, str] = {}

    def get_pending_draft(self, owner_id: int) -> Optional[MessageDispatchDraft]:
        draft_id = self._last_draft_by_owner.get(owner_id)
        if not draft_id:
            return None
        draft = self._drafts.get(draft_id)
        if draft and draft.status in ("PENDING_CONFIRMATION", "WAITING_INFO"):
            return draft
        return None

    def get_last_draft(self, owner_id: int) -> Optional[MessageDispatchDraft]:
        draft_id = self._last_draft_by_owner.get(owner_id)
        if not draft_id:
            return None
        return self._drafts.get(draft_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Parsing
    # ─────────────────────────────────────────────────────────────────────────

    async def parse_command(
        self,
        text: str,
        owner_id: int,
        ai_generator: Optional[Any] = None,
    ) -> MessageDispatchCommand:
        """Parse text/transcription to detect dispatch action, recipient, and message."""
        cleaned = text.strip()
        lower = cleaned.lower()

        # 1. Status check: "Aloo jo'natdingmi ?", "Jo'natdingmi?", "Yubordingmi?"
        status_regex = r"^(?:(?:aloo|alo|ey|hey),?\s*)?(?:jo'natdingmi|yubordingmi|yetkazdingmi|xabar\s*ketdimi|ketdimi|holat\s*nima\s*bo'ldi|nima\s*bo'ldi)\s*\??$"
        if re.search(status_regex, lower, re.IGNORECASE):
            return MessageDispatchCommand(action="STATUS", raw_text=cleaned)

        # 2. Confirmation: "Jo'nat", "Yubor", "Ha jo'nat", "Ha"
        confirm_regex = r"^(?:ha,?\s*)?(?:jo'nat|yubor|jo'natgin|yuborgin|yuborilsin|jo'natilsin|albatta|ha|ok)\.?$"
        if re.match(confirm_regex, lower):
            pending = self.get_pending_draft(owner_id)
            if pending:
                return MessageDispatchCommand(action="CONFIRM", raw_text=cleaned)

        # 3. Cancellation: "Bekor qil", "Yuborma", "Kerakmas"
        cancel_regex = r"^(?:bekor\s*qil|bekor|yuborma|kerakmas|kerak\s*emas|yo'q\s*bekor|to'xtat|otmena)\.?$"
        if re.match(cancel_regex, lower):
            return MessageDispatchCommand(action="CANCEL", raw_text=cleaned)

        # 4. Message correction: "Yo'q unga shu xabarimni yetkaz: ...", "Yo'q unga bunday de: ..."
        correction_regex = r"^(?:yo'q,?\s*)?(?:unga\s*)?(?:shu\s*xabarimni\s*yetkaz|bunday\s*de|shunday\s*yoz|xabarni\s*o'zgartir),?:?\s*(?P<text>.+)$"
        m_corr = re.match(correction_regex, cleaned, re.IGNORECASE)
        if m_corr:
            return MessageDispatchCommand(action="CORRECT", message=m_corr.group("text").strip(), raw_text=cleaned)

        # 5. Providing recipient info (username / ID) when waiting
        pending = self.get_pending_draft(owner_id)
        if pending and pending.status == "WAITING_INFO":
            # Check if this text is a username or ID or short name
            if cleaned.startswith("@") or cleaned.isdigit() or (len(cleaned.split()) <= 2 and not any(kw in lower for kw in ["qil", "ber", "ayt", "so'm"])):
                return MessageDispatchCommand(action="PROVIDE_RECIPIENT", recipient=cleaned, raw_text=cleaned)

        # 6. SEND Commands (Regex Patterns)
        # Pattern A: "Oyattulaga ayting, kechki soat 6 dagi gruppamga menga dars o'tib bersin."
        # Pattern A2: "Oyattulaga ayt, ..." / "Aliga yozing: ..." / "Valiga xabar yubor ..."
        send_pat_1 = (
            r"^(?:iltimos,?\s*)?"
            r"(?P<rec_raw>@?[a-zA-Z0-9_'\u0400-\u04FF]+(?:ga|ka|qa|ge|ning|ni)?)"
            r"(?:\s+(?:ayting|ayt|yozing|yoz|xabar\s+yubor(?:ing)?|yuboring|yubor|jo'nating|jo'nat|yetkazing|yetkaz)),?:?\s+"
            r"(?P<msg>.+)$"
        )
        m = re.match(send_pat_1, cleaned, re.IGNORECASE)
        if m:
            rec_clean = clean_uzbek_recipient(m.group("rec_raw"))
            return MessageDispatchCommand(action="SEND", recipient=rec_clean, message=m.group("msg").strip(), raw_text=cleaned)

        # Pattern B: "Xabar yubor Oyattuloga: ..." / "Yoz Aliga: ..."
        send_pat_2 = (
            r"^(?:iltimos,?\s*)?"
            r"(?:xabar\s+yubor(?:ing)?|yuboring|yubor|jo'nating|jo'nat|yozing|yoz|ayting|ayt)\s+"
            r"(?P<rec_raw>@?[a-zA-Z0-9_'\u0400-\u04FF]+(?:ga|ka|qa|ge|ning|ni)?),?:?\s+"
            r"(?P<msg>.+)$"
        )
        m = re.match(send_pat_2, cleaned, re.IGNORECASE)
        if m:
            rec_clean = clean_uzbek_recipient(m.group("rec_raw"))
            return MessageDispatchCommand(action="SEND", recipient=rec_clean, message=m.group("msg").strip(), raw_text=cleaned)

        # Pattern C: "Oyattulaga dars soat 6 da bo'ladi deb ayting"
        send_pat_3 = (
            r"^(?:iltimos,?\s*)?"
            r"(?P<rec_raw>@?[a-zA-Z0-9_'\u0400-\u04FF]+(?:ga|ka|qa|ge)?)\s+"
            r"(?P<msg>.+)\s+deb\s+"
            r"(?:ayting|ayt|yozing|yoz|yuboring|yubor|jo'nating|jo'nat|yetkazing|yetkaz)\.?$"
        )
        m = re.match(send_pat_3, cleaned, re.IGNORECASE)
        if m:
            rec_clean = clean_uzbek_recipient(m.group("rec_raw"))
            return MessageDispatchCommand(action="SEND", recipient=rec_clean, message=m.group("msg").strip(), raw_text=cleaned)

        # Pattern D: /msg <recipient> <message>
        send_pat_4 = r"^/(?:msg|send)\s+(?P<rec_raw>@?[a-zA-Z0-9_'\u0400-\u04FF]+)\s+(?P<msg>.+)$"
        m = re.match(send_pat_4, cleaned, re.IGNORECASE)
        if m:
            rec_clean = clean_uzbek_recipient(m.group("rec_raw"))
            return MessageDispatchCommand(action="SEND", recipient=rec_clean, message=m.group("msg").strip(), raw_text=cleaned)

        # 7. LLM Intent Fallback (if words indicate dispatch instruction)
        dispatch_keywords = ["ayting", "ayt", "yozing", "yoz", "yubor", "jo'nat", "yetkaz", "xabar"]
        has_kw = any(kw in lower for kw in dispatch_keywords)
        if has_kw and ai_generator is not None and hasattr(ai_generator, "generate"):
            try:
                llm_cmd = await self._llm_extract_command(cleaned, ai_generator)
                if llm_cmd and llm_cmd.action != "NONE":
                    return llm_cmd
            except Exception as e:
                logger.warning("LLM dispatch extraction fallback failed: %s", e)

        return MessageDispatchCommand(action="NONE", raw_text=cleaned)

    async def _llm_extract_command(self, text: str, ai_generator: Any) -> Optional[MessageDispatchCommand]:
        """Extract dispatch intent using Gemini/LLM."""
        sys_prompt = (
            "Siz Asadbek ustozning xabarlarini tahlil qiluvchi AI tizimisiz.\n"
            "Foydalanuvchi biror shaxsga (tanishi, shogirdi, hamkasbi) Telegram orqali xabar yuborishni buyurdimi yoki buyurmadimi aniqlang.\n"
            "Faqat quyidagi JSON formatida javob bering, boshqa hech qanday so'z qo'shmang:\n"
            "{\n"
            '  "is_dispatch": true | false,\n'
            '  "action": "SEND" | "CONFIRM" | "CANCEL" | "STATUS" | "NONE",\n'
            '  "recipient": "Ism yoki @username",\n'
            '  "message": "Yetkazilishi kerak bo\'lgan aniq xabar matni"\n'
            "}"
        )
        resp = await ai_generator.generate(sys_prompt, text)
        if not resp:
            return None

        # Clean JSON from markdown code blocks
        raw_json = resp.strip()
        if raw_json.startswith("```"):
            raw_json = re.sub(r"^```(?:json)?\n?", "", raw_json)
            raw_json = re.sub(r"\n?```$", "", raw_json)
        raw_json = raw_json.strip()

        data = json.loads(raw_json)
        if data.get("is_dispatch") or data.get("action") in ("SEND", "CONFIRM", "CANCEL", "STATUS"):
            act = data.get("action", "SEND")
            rec = clean_uzbek_recipient(data.get("recipient", "") or "")
            msg = data.get("message", "") or ""
            return MessageDispatchCommand(action=act, recipient=rec, message=msg, raw_text=text)

        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Dispatch & Handling
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_owner_message(
        self,
        message: Message,
        text: str,
        owner_id: int,
        bot: Any,
        ai_generator: Optional[Any] = None,
        business_connection_id: Optional[str] = None,
    ) -> bool:
        """
        Main entry point. Handles owner's voice transcription or text command.
        Returns True if handled, False otherwise.
        """
        cmd = await self.parse_command(text, owner_id=owner_id, ai_generator=ai_generator)
        if cmd.action == "NONE":
            return False

        logger.info("Dispatcher matched action=%s recipient=%s msg_len=%s", cmd.action, cmd.recipient, len(cmd.message or ""))

        if cmd.action == "STATUS":
            await self._handle_status(message, owner_id)
            return True

        if cmd.action == "CONFIRM":
            await self._handle_confirm(message, owner_id, bot)
            return True

        if cmd.action == "CANCEL":
            await self._handle_cancel(message, owner_id)
            return True

        if cmd.action == "CORRECT":
            await self._handle_correct(message, owner_id, cmd.message or "")
            return True

        if cmd.action == "PROVIDE_RECIPIENT":
            await self._handle_provide_recipient(message, owner_id, cmd.recipient or "", bot)
            return True

        if cmd.action == "SEND":
            await self._handle_send(
                message=message,
                owner_id=owner_id,
                recipient_query=cmd.recipient or "",
                message_text=cmd.message or "",
                bot=bot,
                business_connection_id=business_connection_id,
            )
            return True

        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Sub-handlers
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_send(
        self,
        message: Message,
        owner_id: int,
        recipient_query: str,
        message_text: str,
        bot: Any,
        business_connection_id: Optional[str] = None,
    ) -> None:
        """Handle new message dispatch command."""
        cleaned_rec = clean_uzbek_recipient(recipient_query)
        if not cleaned_rec:
            await message.answer("⚠️ Qabul qiluvchi shaxs aniqlanmadi. Iltimos, kimga yozish kerakligini ko'rsating.")
            return

        # Search for contact in database
        contacts = await self.contact_service.search_contacts(cleaned_rec, limit=5)
        draft_id = uuid.uuid4().hex[:8]

        if contacts:
            best_contact = contacts[0]
            display_name = best_contact.first_name or cleaned_rec
            username_display = f"@{best_contact.username}" if best_contact.username else "yo'q"

            draft = MessageDispatchDraft(
                draft_id=draft_id,
                owner_id=owner_id,
                recipient_query=cleaned_rec,
                message_text=message_text,
                status="PENDING_CONFIRMATION",
                created_at=datetime.datetime.now(datetime.timezone.utc).timestamp(),
                contact_id=best_contact.id,
                recipient_telegram_id=best_contact.telegram_id,
                recipient_name=display_name,
                recipient_username=best_contact.username,
                business_connection_id=business_connection_id,
            )
            self._drafts[draft_id] = draft
            self._last_draft_by_owner[owner_id] = draft_id

            formatted_out = self.format_outgoing_text(message_text)

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🚀 Ha, yuborilsin", callback_data=f"dispatch:send:{draft_id}"),
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"dispatch:cancel:{draft_id}"),
                    ]
                ]
            )

            card = (
                f"📤 <b>XABAR YUBORISHNI TASDIQLASH</b>\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {display_name} ({username_display})\n"
                f"🆔 <b>Telegram ID:</b> <code>{best_contact.telegram_id}</code>\n"
                f"💬 <b>Yetkaziladigan xabar:</b>\n"
                f"<i>«{message_text}»</i>\n\n"
                f"Ushbu xabarni <b>{display_name}</b>ga yuboraymi?\n"
                f"<i>(Quyidagi tugmani bosing yoki «Jo'nat» deb yozing)</i>"
            )
            await message.answer(card, parse_mode="HTML", reply_markup=kb)
        else:
            # Contact not found in database!
            draft = MessageDispatchDraft(
                draft_id=draft_id,
                owner_id=owner_id,
                recipient_query=cleaned_rec,
                message_text=message_text,
                status="WAITING_INFO",
                created_at=datetime.datetime.now(datetime.timezone.utc).timestamp(),
                business_connection_id=business_connection_id,
            )
            self._drafts[draft_id] = draft
            self._last_draft_by_owner[owner_id] = draft_id

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"dispatch:cancel:{draft_id}"),
                    ]
                ]
            )

            card = (
                f"🔍 <b>Kontakt qidiruvi:</b> «{cleaned_rec}»\n\n"
                f"⚠️ Bazangizda <b>«{cleaned_rec}»</b> ismli kontakt topilmadi.\n\n"
                f"Ushbu xabarni yetkazishim uchun uning Telegram <b>@username</b>ini (masalan: <code>@{cleaned_rec.lower()}</code>) "
                f"yoki <b>Telegram ID</b>sini yuboring.\n"
                f"<i>(Yoki u avval botimizga kirib /start bosgan bo'lishi kerak)</i>\n\n"
                f"📝 <b>Saqlangan qoralama:</b>\n"
                f"<i>«{message_text}»</i>"
            )
            await message.answer(card, parse_mode="HTML", reply_markup=kb)

    async def _handle_confirm(self, message: Message, owner_id: int, bot: Any) -> None:
        """Handle 'Jo'nat' / 'Yubor' confirmation."""
        draft = self.get_pending_draft(owner_id)
        if not draft:
            await message.answer("ℹ️ Hozirda yuborilishi kutilayotgan xabar mavjud emas.")
            return

        if draft.status == "WAITING_INFO":
            await message.answer(
                f"⚠️ Xabarni yuborish uchun <b>«{draft.recipient_query}»</b>ning Telegram @username yoki Telegram ID sini kiritishingiz kerak.",
                parse_mode="HTML",
            )
            return

        success, report = await self.execute_send(draft.draft_id, bot)
        await message.answer(report, parse_mode="HTML")

    async def _handle_cancel(self, message: Message, owner_id: int) -> None:
        """Handle 'Bekor qil'."""
        draft = self.get_pending_draft(owner_id)
        if draft:
            draft.status = "CANCELLED"
            await message.answer("❌ <b>Xabar yuborish bekor qilindi.</b>", parse_mode="HTML")
        else:
            await message.answer("ℹ️ Bekor qilinadigan faol xabar yo'q.")

    async def _handle_status(self, message: Message, owner_id: int) -> None:
        """Handle 'Aloo jo'natdingmi ?' status inquiry."""
        draft = self.get_last_draft(owner_id)
        if not draft:
            await message.answer("Asadbek ustoz, yaqin orada hech qanday xabar yuborish topshirig'i berilmagan.")
            return

        if draft.status == "SENT":
            sent_time_str = (
                datetime.datetime.fromtimestamp(draft.sent_at, tz=datetime.timezone.utc).strftime("%H:%M")
                if draft.sent_at else "hozirgina"
            )
            await message.answer(
                f"✅ <b>Ha, Asadbek ustoz!</b>\n\n"
                f"Xabar <b>{draft.recipient_name or draft.recipient_query}</b>ga muvaffaqiyatli yetkazilgan (Vaqt: {sent_time_str}).\n\n"
                f"💬 <i>«{draft.message_text}»</i>",
                parse_mode="HTML",
            )
        elif draft.status == "PENDING_CONFIRMATION":
            await message.answer(
                f"⏳ Xabar hali yuborilmadi, <b>tasdiqlashingiz kutilmoqda</b>.\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {draft.recipient_name or draft.recipient_query}\n"
                f"💬 <i>«{draft.message_text}»</i>\n\n"
                f"Yuborish uchun <b>«Jo'nat»</b> deb yozing yoki tasdiqlash tugmasini bosing.",
                parse_mode="HTML",
            )
        elif draft.status == "WAITING_INFO":
            await message.answer(
                f"⚠️ Xabar hali yuborilmadi, chunki <b>{draft.recipient_query}</b>ning kontakt ma'lumoti (username yoki Telegram ID) kutilmoqda.\n\n"
                f"Iltimos, uning @username yoki ID sini yozib yuboring.",
                parse_mode="HTML",
            )
        elif draft.status == "CANCELLED":
            await message.answer("❌ Ushbu xabar avvalroq bekor qilingan edi.")

    async def _handle_correct(self, message: Message, owner_id: int, new_text: str) -> None:
        """Handle 'Yo'q unga shu xabarimni yetkaz: ...'."""
        draft = self.get_pending_draft(owner_id) or self.get_last_draft(owner_id)
        if not draft:
            await message.answer("⚠️ Yangilash uchun faol xabar topilmadi. Qaytadan aytib yuboring.")
            return

        draft.message_text = new_text
        if draft.status == "WAITING_INFO":
            await message.answer(
                f"📝 <b>Xabar matni yangilandi:</b>\n<i>«{new_text}»</i>\n\n"
                f"Endi <b>{draft.recipient_query}</b>ning Telegram @username (masalan: <code>@username</code>) yoki ID sini yozing.",
                parse_mode="HTML",
            )
        elif draft.status == "PENDING_CONFIRMATION":
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🚀 Ha, yuborilsin", callback_data=f"dispatch:send:{draft.draft_id}"),
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"dispatch:cancel:{draft.draft_id}"),
                    ]
                ]
            )
            await message.answer(
                f"📝 <b>Xabar matni yangilandi:</b>\n<i>«{new_text}»</i>\n\n"
                f"<b>{draft.recipient_name}</b>ga yuboraymi? («Jo'nat» deb yozing yoki tugmani bosing)",
                parse_mode="HTML",
                reply_markup=kb,
            )

    async def _handle_provide_recipient(self, message: Message, owner_id: int, recipient_input: str, bot: Any) -> None:
        """Handle owner providing @username or ID when waiting for contact info."""
        draft = self.get_pending_draft(owner_id)
        if not draft:
            return

        clean_input = recipient_input.strip()
        username: Optional[str] = None
        telegram_id: Optional[int] = None

        if clean_input.startswith("@"):
            username = clean_input.lstrip("@")
        elif clean_input.isdigit():
            telegram_id = int(clean_input)
        else:
            username = clean_input

        # Search or register contact
        contact: Optional[NargisContact] = None
        if telegram_id:
            contact = await self.contact_service.get_contact_by_telegram_id(telegram_id)
        elif username:
            contact = await self.contact_service.get_contact_by_username(username)

        if not contact:
            # Create contact with known info
            contact = await self.contact_service.add_or_update_contact(
                first_name=draft.recipient_query,
                telegram_id=telegram_id,
                username=username,
            )

        # Update draft
        draft.contact_id = contact.id
        draft.recipient_telegram_id = contact.telegram_id
        draft.recipient_name = contact.first_name or draft.recipient_query
        draft.recipient_username = contact.username
        draft.status = "PENDING_CONFIRMATION"

        username_display = f"@{contact.username}" if contact.username else "—"
        id_display = f"<code>{contact.telegram_id}</code>" if (contact.telegram_id and contact.telegram_id > 0) else "Aniqlanmagan"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🚀 Ha, yuborilsin", callback_data=f"dispatch:send:{draft.draft_id}"),
                    InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"dispatch:cancel:{draft.draft_id}"),
                ]
            ]
        )

        card = (
            f"✅ <b>Kontakt bog'landi:</b> {draft.recipient_name} ({username_display})\n"
            f"🆔 <b>Telegram ID:</b> {id_display}\n\n"
            f"💬 <b>Yetkaziladigan xabar:</b>\n"
            f"<i>«{draft.message_text}»</i>\n\n"
            f"Ushbu xabar <b>{draft.recipient_name}</b>ga yuborilsinmi?\n"
            f"<i>(Quyidagi tugmani bosing yoki «Jo'nat» deb yozing)</i>"
        )
        await message.answer(card, parse_mode="HTML", reply_markup=kb)

    # ─────────────────────────────────────────────────────────────────────────
    # Physical Execution
    # ─────────────────────────────────────────────────────────────────────────

    def format_outgoing_text(self, message_text: str) -> str:
        """Format polite, clear message for recipient."""
        return (
            f"📩 <b>Assalomu alaykum!</b>\n\n"
            f"<b>Asadbek Ustozdan xabar:</b>\n"
            f"«{message_text}»\n\n"
            f"<i>(Ushbu xabar Asadbek ustozning Nargis AI assistenti orqali yuborildi)</i>"
        )

    async def execute_send(self, draft_id: str, bot: Any) -> tuple[bool, str]:
        """Physically dispatch the message to Telegram."""
        draft = self._drafts.get(draft_id)
        if not draft:
            return False, "❌ Qoralama xabar topilmadi."

        tid = draft.recipient_telegram_id
        if not tid or tid <= 0:
            return False, (
                f"⚠️ <b>{draft.recipient_name or draft.recipient_query}</b>ning Telegram ID raqami ma'lum emas.\n\n"
                f"Telegram Bot API orqali xabar yuborish uchun uning raqamli ID si kerak (u kamida bir marta botga /start bosgan bo'lishi lozim)."
            )

        outgoing_text = self.format_outgoing_text(draft.message_text)

        try:
            # 1. Attempt delivery via Telegram Bot API
            sent_msg = None
            if draft.business_connection_id:
                try:
                    sent_msg = await bot.send_message(
                        business_connection_id=draft.business_connection_id,
                        chat_id=tid,
                        text=outgoing_text,
                        parse_mode="HTML",
                    )
                except Exception as b_err:
                    logger.warning("Business connection dispatch failed, trying direct bot: %s", b_err)

            if not sent_msg:
                sent_msg = await bot.send_message(
                    chat_id=tid,
                    text=outgoing_text,
                    parse_mode="HTML",
                )

            # 2. Update draft
            now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
            draft.status = "SENT"
            draft.sent_at = now_ts

            # 3. Log to database
            if draft.contact_id:
                contact = await self.contact_service.get_contact_by_id(draft.contact_id)
                if contact:
                    msg_id = getattr(sent_msg, "message_id", None)
                    await self.contact_service.log_outgoing_message(
                        contact=contact,
                        content=draft.message_text,
                        telegram_message_id=msg_id,
                        business_connection_id=draft.business_connection_id,
                    )

            time_str = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc).strftime("%H:%M")
            rec_display = f"{draft.recipient_name} (@{draft.recipient_username})" if draft.recipient_username else draft.recipient_name

            report = (
                f"✅ <b>XABAR MUVAFFAQIYATLI YUBORILDI!</b>\n\n"
                f"👤 <b>Qabul qiluvchi:</b> {rec_display}\n"
                f"🕒 <b>Yuborilgan vaqt:</b> {time_str}\n"
                f"💬 <b>Xabar matni:</b>\n"
                f"<i>«{draft.message_text}»</i>"
            )
            return True, report

        except Exception as e:
            logger.error("Failed to dispatch telegram message: %s", e)
            draft.error_message = str(e)
            rec_user = f"@{draft.recipient_username}" if draft.recipient_username else draft.recipient_name

            report = (
                f"⚠️ <b>Xabar yetkazilmadi (Telegram cheklovi):</b>\n\n"
                f"Foydalanuvchi ({rec_user}) hali botimizga <b>/start</b> bosmagan yoki botni bloklagan bo'lishi mumkin.\n"
                f"<i>Telegram xavfsizlik qoidalariga ko'ra bot foydalanuvchiga birinchi bo'lib yozolmaydi.</i>\n\n"
                f"Ushbu xabarni uning shaxsiy chatiga quyidagicha nusxalab yuborishingiz mumkin:\n"
                f"<code>{outgoing_text}</code>"
            )
            return False, report


# Global dispatcher instance
_global_dispatcher: Optional[NargisMessageDispatcher] = None


def get_message_dispatcher() -> NargisMessageDispatcher:
    global _global_dispatcher
    if _global_dispatcher is None:
        _global_dispatcher = NargisMessageDispatcher()
    return _global_dispatcher
