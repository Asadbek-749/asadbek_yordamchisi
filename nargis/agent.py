"""Nargis AI Agent — central orchestrator for incoming business messages."""
from __future__ import annotations

import logging
from typing import Any, Optional

from nargis.classifier import IntentClassifier, MessageClassification, UrgencyLevel
from nargis.validator import ResponseValidator

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Escalation notification template
# ─────────────────────────────────────────────
_ESCALATION_TEMPLATE = """\
🚨 <b>NARGIS — MUHIM XABAR</b>

👤 <b>Kim:</b> {sender_name}
📱 <b>Telegram:</b> {username}
📌 <b>Sabab:</b> {reason}
🔥 <b>Muhimlik:</b> {urgency}
{deadline_line}
💬 <b>Xabar:</b>
<i>{message_text}</i>

⚡ <i>Nargis darhol e'tibor berishingizni tavsiya qiladi.</i>\
"""

_HIGH_TEMPLATE = """\
⚠️ <b>NARGIS — Muhim xabar</b>

👤 {sender_name} (@{username_raw})
📌 {reason}
🔥 Muhimlik: {urgency}

💬 <i>{message_text}</i>\
"""


class NargisAgent:
    """
    Orchestrates the full Nargis AI pipeline for incoming business messages:
    classify → generate reply → validate → notify owner if needed → store contact.
    """

    def __init__(
        self,
        ai_generator: Any,
        owner_id: int,
        classifier: Optional[IntentClassifier] = None,
        validator: Optional[ResponseValidator] = None,
        contact_service: Any = None,   # NargisContactService (optional, injected later)
    ) -> None:
        self.ai = ai_generator
        self.owner_id = owner_id
        self.classifier = classifier or IntentClassifier(ai_generator)
        self.validator = validator or ResponseValidator()
        self.contact_service = contact_service

    async def process_and_classify(
        self,
        message_text: str,
        sender_name: str,
        sender_username: Optional[str],
        sender_telegram_id: int,
        connection_id: str,
        chat_id: int,
    ) -> MessageClassification:
        """Classify message and store contact info if contact_service is available."""
        classification = await self.classifier.classify(message_text)

        if self.contact_service is not None:
            try:
                await self.contact_service.upsert_contact(
                    telegram_id=sender_telegram_id,
                    first_name=sender_name.split()[0] if sender_name else None,
                    username=sender_username,
                    message_text=message_text,
                    classification=classification,
                )
            except Exception as e:
                logger.warning("Failed to upsert contact: %s", e)

        return classification

    def build_owner_notification(
        self,
        classification: MessageClassification,
        sender_name: str,
        sender_username: Optional[str],
        message_text: str,
    ) -> Optional[str]:
        """
        Build notification text to send to the owner.
        Returns None if no notification is needed.
        """
        if not classification.is_high_or_critical:
            return None

        username_display = f"@{sender_username}" if sender_username else "username yo'q"
        username_raw = sender_username or "noma'lum"
        reason = classification.reason_summary or classification.primary_intent.value
        urgency = classification.urgency.value
        deadline_line = (
            f"⏰ <b>Deadline:</b> {classification.deadline_hint}\n"
            if classification.deadline_hint
            else ""
        )
        # Truncate message to avoid huge notifications
        preview = message_text[:300] + ("..." if len(message_text) > 300 else "")

        if classification.is_critical:
            return _ESCALATION_TEMPLATE.format(
                sender_name=sender_name,
                username=username_display,
                reason=reason,
                urgency=urgency,
                deadline_line=deadline_line,
                message_text=preview,
            )
        else:
            return _HIGH_TEMPLATE.format(
                sender_name=sender_name,
                username_raw=username_raw,
                reason=reason,
                urgency=urgency,
                message_text=preview,
            )

    async def send_owner_notification(
        self,
        bot: Any,
        classification: MessageClassification,
        sender_name: str,
        sender_username: Optional[str],
        message_text: str,
    ) -> bool:
        """Send notification to owner if message is important. Returns True if sent."""
        notification = self.build_owner_notification(
            classification, sender_name, sender_username, message_text
        )
        if notification is None:
            return False

        try:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            buttons = [
                [
                    InlineKeyboardButton(text="👁 Ko'rildi", callback_data="nargis:ack"),
                    InlineKeyboardButton(text="🔥 Muhim", callback_data="nargis:mark_important"),
                ],
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            await bot.send_message(
                self.owner_id,
                notification,
                parse_mode="HTML",
                reply_markup=markup,
            )
            logger.info(
                "Owner notified: urgency=%s, intent=%s",
                classification.urgency,
                classification.primary_intent,
            )
            return True
        except Exception as e:
            logger.error("Failed to send owner notification: %s", e)
            return False

    def validate_reply(self, reply_text: str) -> str:
        """Validate and sanitize AI reply before sending."""
        return self.validator.get_safe_response(reply_text)
