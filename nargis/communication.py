"""Communication Manager & Deleted Message Monitor for Nargis 2.0.

Features:
- Concise, bounded external user interactions (First message vs Repeated message protocol)
- Contact & Lead Extraction (WHO, WHY, WHAT, DEADLINE, PRIORITY)
- Deleted Message Monitoring via Telegram API event interception
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from discipline.db import get_session
from discipline.models import NargisContact, NargisMessage, utcnow

logger = logging.getLogger(__name__)


class CommunicationManager:
    """Manages external interlocutors, lead triage, and message preservation."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def handle_external_message(
        self,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        message_text: str,
        chat_id: int,
        telegram_message_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle incoming message from non-owner:
        - Check if contact exists and count previous messages
        - Store immutable message record
        - Return strictly bounded response (first message vs repeated message)
        """
        clean_text = message_text.strip()
        lower = clean_text.lower()

        async with get_session() as session:
            # 1. Upsert contact
            stmt = select(NargisContact).where(NargisContact.telegram_id == sender_id)
            res = await session.execute(stmt)
            contact = res.scalar_one_or_none()

            is_first_time = False
            if not contact:
                is_first_time = True
                contact = NargisContact(
                    telegram_id=sender_id,
                    username=sender_username,
                    first_name=sender_name.split()[0] if sender_name else "User",
                    last_name=" ".join(sender_name.split()[1:]) if len(sender_name.split()) > 1 else None,
                    message_count=1,
                    first_seen=utcnow(),
                    last_seen=utcnow(),
                )
                session.add(contact)
                await session.flush()
            else:
                contact.message_count = (contact.message_count or 0) + 1
                contact.last_seen = utcnow()
                if sender_username:
                    contact.username = sender_username

            # 2. Extract intent & urgency
            intent = "GENERAL"
            urgency = "NORMAL"
            importance = 50

            if any(w in lower for w in ("kurs", "dars", "o'qish", "aipply", "narx", "to'lov")):
                intent = "COURSE"
                contact.lead_status = "QUALIFIED"
                importance = 70
            elif any(w in lower for w in ("loyiha", "hamkorlik", "startap", "sayt", "bot yozish", "buyurtma")):
                intent = "PROJECT"
                contact.lead_status = "QUALIFIED"
                importance = 80
            elif any(w in lower for w in ("shoshilinch", "darhol", "tez", "muhim", "urgent")):
                intent = "URGENT"
                urgency = "HIGH"
                importance = 90

            contact.last_intent = intent
            contact.importance_score = importance

            # 3. Store immutable message event
            msg_record = NargisMessage(
                contact_id=contact.id,
                telegram_message_id=telegram_message_id,
                chat_id=chat_id,
                sender_name=sender_name,
                sender_username=sender_username,
                business_connection_id=business_connection_id,
                content=clean_text,
                direction="IN",
                intent=intent,
                urgency=urgency,
                importance_score=importance,
                status="NEW",
            )
            session.add(msg_record)
            await session.commit()

        # 4. Generate bounded response
        if is_first_time or contact.message_count <= 1:
            reply = (
                "Va alaykum assalom. Men Asadbek ustozning AI agentiman — Nargis. "
                "Hozir Asadbek ustoz ish bilan bandlar. Xabaringizni yetkazaman, "
                "imkon bo‘lishi bilan o‘zlari sizga yozadilar."
            )
        else:
            # If lead provided specifics (e.g. course or project)
            if intent in ("COURSE", "PROJECT", "URGENT"):
                reply = "Ma'lumotni Asadbek ustozga yetkazdim. O'zlari sizga aloqaga chiqadilar."
            else:
                reply = (
                    "Asadbek ustozga yetkazishim uchun qisqacha nima masalada "
                    "bog‘lanayotganingizni yozib qoldiring."
                )

        return {
            "reply_text": reply,
            "intent": intent,
            "urgency": urgency,
            "importance": importance,
            "notify_owner": (importance >= 70),
        }

    async def record_deleted_message(
        self,
        chat_id: int,
        message_ids: List[int],
        business_connection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Record deletion event when Telegram notifies deleted_business_messages.
        Returns list of deleted message details to notify the owner.
        """
        deleted_records = []
        now_dt = utcnow()

        async with get_session() as session:
            for msg_id in message_ids:
                stmt = select(NargisMessage).where(
                    NargisMessage.telegram_message_id == msg_id,
                    NargisMessage.is_deleted == False,
                )
                if business_connection_id:
                    stmt = stmt.where(NargisMessage.business_connection_id == business_connection_id)

                res = await session.execute(stmt)
                record = res.scalar_one_or_none()

                if record:
                    record.is_deleted = True
                    record.deleted_at = now_dt
                    record.status = "DELETED"

                    time_str = record.created_at.astimezone(ZoneInfo("Asia/Tashkent")).strftime("%H:%M, %d-%m-%Y")
                    user_tag = f"@{record.sender_username}" if record.sender_username else (record.sender_name or "Noma'lum")

                    deleted_records.append({
                        "message_id": record.telegram_message_id,
                        "sender": user_tag,
                        "time": time_str,
                        "content": record.content,
                        "preview_alert": (
                            f"🚨 <b>O'CHIRILGAN XABAR ANIQLANDI!</b>\n\n"
                            f"👤 <b>Kimdan:</b> {user_tag}\n"
                            f"🕐 <b>Yuborilgan vaqti:</b> {time_str}\n\n"
                            f"📩 <b>O'chirilgan xabar:</b>\n"
                            f"<i>«{record.content}»</i>\n\n"
                            f"⚡ <i>Xabar foydalanuvchi tomonidan o'chirildi.</i>"
                        ),
                    })

            await session.commit()

        return deleted_records
