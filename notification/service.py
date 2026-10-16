"""Notification service architecture supporting Telegram, Email, SMS, and VoiceCall."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BaseNotifier(ABC):
    """Abstract base class for all notification providers."""

    @abstractmethod
    async def send(self, recipient: str, title: str, message: str, **kwargs: Any) -> bool:
        """Send notification. Returns True if successfully sent."""
        pass


class TelegramNotifier(BaseNotifier):
    """Telegram bot notifier for instant owner alerts."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send(self, recipient: str, title: str, message: str, **kwargs: Any) -> bool:
        try:
            chat_id = int(recipient)
            full_text = f"<b>{title}</b>\n\n{message}" if title else message
            parse_mode = kwargs.get("parse_mode", "HTML")
            reply_markup = kwargs.get("reply_markup", None)
            await self.bot.send_message(
                chat_id=chat_id,
                text=full_text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            logger.error("TelegramNotifier error for %s: %s", recipient, e)
            return False


class EmailNotifier(BaseNotifier):
    """Email notifier stub (ready for SMTP / SendGrid configuration)."""

    def __init__(self, smtp_host: Optional[str] = None) -> None:
        self.smtp_host = smtp_host

    async def send(self, recipient: str, title: str, message: str, **kwargs: Any) -> bool:
        if not self.smtp_host:
            logger.info("EMAIL_UNAVAILABLE: No SMTP host configured for recipient %s", recipient)
            return False
        logger.info("Sending email to %s: %s", recipient, title)
        return True


class SMSNotifier(BaseNotifier):
    """SMS notifier stub (ready for Twilio / Eskiz SMS API)."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key

    async def send(self, recipient: str, title: str, message: str, **kwargs: Any) -> bool:
        if not self.api_key:
            logger.info("SMS_UNAVAILABLE: No SMS API key configured for recipient %s", recipient)
            return False
        logger.info("Sending SMS to %s: %s", recipient, message[:50])
        return True


class VoiceCallNotifier(BaseNotifier):
    """
    Voice call notifier.
    Per specification: Standard Telegram Bot API cannot initiate arbitrary voice calls.
    If no external telephony provider (e.g. Twilio Voice) is configured,
    it records VOICE_CALL_UNAVAILABLE gracefully without breaking the system.
    """

    def __init__(self, telephony_provider: Optional[Any] = None) -> None:
        self.telephony_provider = telephony_provider

    async def send(self, recipient: str, title: str, message: str, **kwargs: Any) -> bool:
        if not self.telephony_provider:
            logger.warning("VOICE_CALL_UNAVAILABLE: Arbitrary voice calls cannot be initiated without external telephony provider.")
            return False
        try:
            return await self.telephony_provider.call(recipient, message)
        except Exception as e:
            logger.error("VoiceCallNotifier error: %s", e)
            return False


class NotificationService:
    """Central orchestrator for all notification channels."""

    def __init__(
        self,
        telegram_notifier: Optional[TelegramNotifier] = None,
        email_notifier: Optional[EmailNotifier] = None,
        sms_notifier: Optional[SMSNotifier] = None,
        voice_notifier: Optional[VoiceCallNotifier] = None,
    ) -> None:
        self.telegram = telegram_notifier
        self.email = email_notifier or EmailNotifier()
        self.sms = sms_notifier or SMSNotifier()
        self.voice = voice_notifier or VoiceCallNotifier()

    async def notify_owner(
        self,
        owner_id: int,
        title: str,
        message: str,
        channel: str = "telegram",
        **kwargs: Any,
    ) -> bool:
        """Send notification through specified channel (defaults to telegram)."""
        if channel == "telegram" and self.telegram:
            return await self.telegram.send(str(owner_id), title, message, **kwargs)
        elif channel == "email":
            return await self.email.send(str(owner_id), title, message, **kwargs)
        elif channel == "sms":
            return await self.sms.send(str(owner_id), title, message, **kwargs)
        elif channel == "voice":
            return await self.voice.send(str(owner_id), title, message, **kwargs)
        return False
