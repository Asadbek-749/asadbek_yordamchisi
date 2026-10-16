"""Nargis Contact & Message repository — DB operations."""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from discipline.models import (
    DisciplineScore,
    DisciplineScoreEvent,
    NargisContact,
    NargisEscalation,
    NargisMessage,
    utcnow,
)

logger = logging.getLogger(__name__)


class NargisRepository:
    """Database operations for contacts, messages, escalations, and discipline score."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ─────────────────────────────────────────────
    # CONTACTS
    # ─────────────────────────────────────────────

    async def get_or_create_contact(
        self,
        telegram_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> NargisContact:
        result = await self.session.execute(
            select(NargisContact).where(NargisContact.telegram_id == telegram_id)
        )
        contact = result.scalar_one_or_none()
        if contact is None:
            contact = NargisContact(
                telegram_id=telegram_id,
                first_name=first_name,
                username=username,
            )
            self.session.add(contact)
            await self.session.flush()
        else:
            # Update last-seen fields
            if first_name:
                contact.first_name = first_name
            if username:
                contact.username = username
            contact.last_seen = utcnow()
        return contact

    async def update_contact_after_message(
        self,
        contact: NargisContact,
        intent: Optional[str],
        importance_score: int,
    ) -> None:
        contact.message_count = (contact.message_count or 0) + 1
        if intent:
            contact.last_intent = intent
        # Update importance to highest seen
        if importance_score > (contact.importance_score or 0):
            contact.importance_score = importance_score

    async def list_contacts(
        self,
        limit: int = 50,
        lead_status: Optional[str] = None,
    ) -> Sequence[NargisContact]:
        stmt = select(NargisContact).order_by(desc(NargisContact.last_seen))
        if lead_status:
            stmt = stmt.where(NargisContact.lead_status == lead_status)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_contact_by_id(self, contact_id: int) -> Optional[NargisContact]:
        result = await self.session.execute(
            select(NargisContact).where(NargisContact.id == contact_id)
        )
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────────
    # MESSAGES
    # ─────────────────────────────────────────────

    async def log_message(
        self,
        contact: NargisContact,
        content: str,
        direction: str = "IN",
        intent: Optional[str] = None,
        urgency: str = "NORMAL",
        importance_score: int = 50,
        requires_owner: bool = False,
        deadline_hint: Optional[str] = None,
        reason_summary: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
        ai_generated_reply: Optional[str] = None,
        owner_notified: bool = False,
    ) -> NargisMessage:
        msg = NargisMessage(
            contact_id=contact.id,
            content=content,
            direction=direction,
            intent=intent,
            urgency=urgency,
            importance_score=importance_score,
            requires_owner=requires_owner,
            deadline_hint=deadline_hint,
            reason_summary=reason_summary,
            telegram_message_id=telegram_message_id,
            business_connection_id=business_connection_id,
            ai_generated_reply=ai_generated_reply,
            owner_notified=owner_notified,
            status="NEW",
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def list_incoming_messages(
        self,
        limit: int = 30,
        status: Optional[str] = None,
        requires_owner: Optional[bool] = None,
        urgency: Optional[str] = None,
    ) -> Sequence[NargisMessage]:
        stmt = (
            select(NargisMessage)
            .where(NargisMessage.direction == "IN")
            .order_by(desc(NargisMessage.created_at))
        )
        if status:
            stmt = stmt.where(NargisMessage.status == status)
        if requires_owner is not None:
            stmt = stmt.where(NargisMessage.requires_owner == requires_owner)
        if urgency:
            stmt = stmt.where(NargisMessage.urgency == urgency)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_important_messages(self, limit: int = 20) -> Sequence[NargisMessage]:
        """Return HIGH and CRITICAL messages not yet resolved."""
        stmt = (
            select(NargisMessage)
            .where(
                NargisMessage.direction == "IN",
                NargisMessage.urgency.in_(["HIGH", "CRITICAL"]),
                NargisMessage.status.notin_(["RESOLVED", "ARCHIVED"]),
            )
            .order_by(desc(NargisMessage.created_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_message_status(self, message_id: int, status: str) -> None:
        await self.session.execute(
            update(NargisMessage)
            .where(NargisMessage.id == message_id)
            .values(status=status, updated_at=utcnow())
        )

    # ─────────────────────────────────────────────
    # ESCALATIONS
    # ─────────────────────────────────────────────

    async def log_escalation(
        self,
        urgency_level: str,
        notification_text: str,
        message_id: Optional[int] = None,
        contact_id: Optional[int] = None,
        delivered: bool = False,
    ) -> NargisEscalation:
        esc = NargisEscalation(
            message_id=message_id,
            contact_id=contact_id,
            urgency_level=urgency_level,
            notification_text=notification_text,
            delivered=delivered,
        )
        self.session.add(esc)
        await self.session.flush()
        return esc

    # ─────────────────────────────────────────────
    # DISCIPLINE SCORE
    # ─────────────────────────────────────────────

    async def get_or_create_score(self, user_id: int) -> DisciplineScore:
        result = await self.session.execute(
            select(DisciplineScore).where(DisciplineScore.user_id == user_id)
        )
        score = result.scalar_one_or_none()
        if score is None:
            score = DisciplineScore(user_id=user_id, score=100, level="STRONG")
            self.session.add(score)
            await self.session.flush()
        return score

    async def apply_score_event(
        self,
        user_id: int,
        event_type: str,
        delta: int,
        reason: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> tuple[DisciplineScore, DisciplineScoreEvent]:
        score = await self.get_or_create_score(user_id)
        new_score = max(0, score.score + delta)
        score.score = new_score
        score.level = _score_to_level(new_score)
        score.updated_at = utcnow()

        event = DisciplineScoreEvent(
            user_id=user_id,
            event_type=event_type,
            score_delta=delta,
            score_after=new_score,
            reason=reason,
            task_id=task_id,
        )
        self.session.add(event)
        await self.session.flush()
        return score, event

    async def get_recent_score_events(
        self, user_id: int, limit: int = 10
    ) -> Sequence[DisciplineScoreEvent]:
        result = await self.session.execute(
            select(DisciplineScoreEvent)
            .where(DisciplineScoreEvent.user_id == user_id)
            .order_by(desc(DisciplineScoreEvent.created_at))
            .limit(limit)
        )
        return result.scalars().all()


# ─────────────────────────────────────────────
# Score helpers
# ─────────────────────────────────────────────

def _score_to_level(score: int) -> str:
    if score >= 90:
        return "ELITE"
    if score >= 80:
        return "STRONG"
    if score >= 70:
        return "NORMAL"
    if score >= 50:
        return "WEAK"
    if score >= 30:
        return "CRITICAL"
    return "DISCIPLINE_FAILURE"


# Score deltas — spec §27
SCORE_DELTAS: dict[str, int] = {
    "TASK_COMPLETED_IMPORTANT": +10,
    "TASK_COMPLETED_CRITICAL": +20,
    "FOCUS_COMPLETED": +10,
    "HABIT_COMPLETED": +5,
    "TASK_POSTPONED": -10,
    "TASK_MISSED": -20,
    "TASK_MISSED_CRITICAL": -30,
    "PLAN_IGNORED": -10,
    "REPEATED_PROCRASTINATION": -15,
}
