"""Nargis contact, message, and score service layer."""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from discipline.db import get_session
from discipline.models import NargisContact, NargisMessage
from nargis.classifier import MessageClassification
from nargis.repository import NargisRepository, SCORE_DELTAS

logger = logging.getLogger(__name__)


class NargisContactService:
    """High-level service for contact management, message logging, and escalations."""

    async def upsert_contact(
        self,
        telegram_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
        message_text: Optional[str] = None,
        classification: Optional[MessageClassification] = None,
    ) -> NargisContact:
        """Create or update contact, optionally log a message."""
        async with get_session() as session:
            repo = NargisRepository(session)
            contact = await repo.get_or_create_contact(
                telegram_id=telegram_id,
                first_name=first_name,
                username=username,
            )
            intent_str = classification.primary_intent.value if classification else None
            importance = classification.importance_score if classification else 50
            await repo.update_contact_after_message(contact, intent_str, importance)
        return contact

    async def log_incoming_message(
        self,
        telegram_id: int,
        sender_name: Optional[str],
        username: Optional[str],
        content: str,
        classification: MessageClassification,
        telegram_message_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
        ai_reply: Optional[str] = None,
        owner_notified: bool = False,
    ) -> NargisMessage:
        """Log an incoming user message with classification data."""
        async with get_session() as session:
            repo = NargisRepository(session)
            contact = await repo.get_or_create_contact(
                telegram_id=telegram_id,
                first_name=sender_name,
                username=username,
            )
            await repo.update_contact_after_message(
                contact,
                intent=classification.primary_intent.value,
                importance_score=classification.importance_score,
            )
            msg = await repo.log_message(
                contact=contact,
                content=content,
                direction="IN",
                intent=classification.primary_intent.value,
                urgency=classification.urgency.value,
                importance_score=classification.importance_score,
                requires_owner=classification.requires_owner,
                deadline_hint=classification.deadline_hint,
                reason_summary=classification.reason_summary,
                telegram_message_id=telegram_message_id,
                business_connection_id=business_connection_id,
                ai_generated_reply=ai_reply,
                owner_notified=owner_notified,
            )

            # Log escalation record if high/critical
            if classification.is_high_or_critical:
                await repo.log_escalation(
                    urgency_level=classification.urgency.value,
                    notification_text=f"[{classification.urgency.value}] {content[:200]}",
                    message_id=msg.id,
                    contact_id=contact.id,
                    delivered=owner_notified,
                )
        return msg

    async def list_important_messages(self, limit: int = 20) -> Sequence[NargisMessage]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.list_important_messages(limit=limit)

    async def list_all_messages(
        self,
        limit: int = 30,
        status: Optional[str] = None,
    ) -> Sequence[NargisMessage]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.list_incoming_messages(limit=limit, status=status)

    async def list_contacts(self, limit: int = 50) -> Sequence[NargisContact]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.list_contacts(limit=limit)

    async def mark_message_viewed(self, message_id: int) -> None:
        async with get_session() as session:
            repo = NargisRepository(session)
            await repo.update_message_status(message_id, "VIEWED")

    async def search_contacts(self, query: str, limit: int = 10) -> Sequence[NargisContact]:
        """Search contacts by username, full name, phone, or telegram_id."""
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.search_contacts(query=query, limit=limit)

    async def get_contact_by_id(self, contact_id: int) -> Optional[NargisContact]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.get_contact_by_id(contact_id)

    async def get_contact_by_telegram_id(self, telegram_id: int) -> Optional[NargisContact]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.get_contact_by_telegram_id(telegram_id)

    async def get_contact_by_username(self, username: str) -> Optional[NargisContact]:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.get_contact_by_username(username)

    async def add_or_update_contact(
        self,
        first_name: str,
        telegram_id: Optional[int] = None,
        username: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> NargisContact:
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.create_or_update_contact(
                first_name=first_name,
                telegram_id=telegram_id,
                username=username,
                phone=phone,
            )

    async def log_outgoing_message(
        self,
        contact: NargisContact,
        content: str,
        telegram_message_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> NargisMessage:
        """Log outgoing message sent from owner / Nargis to a contact."""
        async with get_session() as session:
            repo = NargisRepository(session)
            return await repo.log_message(
                contact=contact,
                content=content,
                direction="OUT",
                intent="DISPATCH",
                urgency="NORMAL",
                importance_score=50,
                telegram_message_id=telegram_message_id,
                business_connection_id=business_connection_id,
                owner_notified=True,
            )


class NargisDisciplineScoreService:
    """Manages discipline score — awards, penalties, and level tracking."""

    async def get_score(self, user_id: int) -> tuple[int, str]:
        """Return (score, level)."""
        async with get_session() as session:
            repo = NargisRepository(session)
            score_rec = await repo.get_or_create_score(user_id)
            return score_rec.score, score_rec.level

    async def award(
        self,
        user_id: int,
        event_type: str,
        reason: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> tuple[int, str, int]:
        """Apply a positive score event. Returns (new_score, level, delta)."""
        delta = SCORE_DELTAS.get(event_type, 0)
        if delta == 0:
            logger.warning("Unknown score event type: %s", event_type)
            return await self._get_current(user_id)

        async with get_session() as session:
            repo = NargisRepository(session)
            score_rec, _ = await repo.apply_score_event(
                user_id=user_id,
                event_type=event_type,
                delta=delta,
                reason=reason,
                task_id=task_id,
            )
            return score_rec.score, score_rec.level, delta

    async def penalize(
        self,
        user_id: int,
        event_type: str,
        reason: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> tuple[int, str, int]:
        """Apply a negative score event. Returns (new_score, level, delta)."""
        delta = SCORE_DELTAS.get(event_type, 0)
        async with get_session() as session:
            repo = NargisRepository(session)
            score_rec, _ = await repo.apply_score_event(
                user_id=user_id,
                event_type=event_type,
                delta=delta,  # already negative
                reason=reason,
                task_id=task_id,
            )
            return score_rec.score, score_rec.level, delta

    async def _get_current(self, user_id: int) -> tuple[int, str, int]:
        score, level = await self.get_score(user_id)
        return score, level, 0

    def level_emoji(self, level: str) -> str:
        return {
            "ELITE": "🏆",
            "STRONG": "💪",
            "NORMAL": "📊",
            "WEAK": "⚠️",
            "CRITICAL": "🔴",
            "DISCIPLINE_FAILURE": "🚨",
        }.get(level, "📊")

    def score_summary(self, score: int, level: str) -> str:
        emoji = self.level_emoji(level)
        return f"{emoji} Intizom bali: <b>{score}/100</b> — <b>{level}</b>"
