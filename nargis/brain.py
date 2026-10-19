"""Nargis Central AI Brain — master cognitive pipeline for incoming interactions.

Pipeline:
Telegram Update -> Authentication -> User Identification -> Message Type Detection
-> Voice/Text Processing -> Intent Detection -> Context Retrieval -> Memory Retrieval
-> Confidence Analysis -> Decision Engine -> Action Engine -> Database -> Response Generation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrainIntent(str, Enum):
    # Finance
    FINANCE_ADD_EXPENSE = "FINANCE_ADD_EXPENSE"
    FINANCE_ADD_INCOME = "FINANCE_ADD_INCOME"
    FINANCE_QUERY = "FINANCE_QUERY"
    FINANCE_CONFIRM = "FINANCE_CONFIRM"

    # Tasks & Accountability
    TASK_CREATE = "TASK_CREATE"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_POSTPONE = "TASK_POSTPONE"
    TASK_LIST = "TASK_LIST"

    # Daily Planning & Boss Control
    PLAN_CREATE = "PLAN_CREATE"
    PLAN_STATUS = "PLAN_STATUS"
    PLAN_REASON = "PLAN_REASON"
    EVENING_REVIEW = "EVENING_REVIEW"

    # Discipline & Performance
    DISCIPLINE_STATUS = "DISCIPLINE_STATUS"
    DISCIPLINE_SCORE = "DISCIPLINE_SCORE"
    HABIT_CHECK = "HABIT_CHECK"

    # Goals
    GOAL_CREATE = "GOAL_CREATE"
    GOAL_STATUS = "GOAL_STATUS"

    # Dispatcher
    DISPATCH_MESSAGE = "DISPATCH_MESSAGE"

    # Communication (External users)
    COMMUNICATION_LEAD = "COMMUNICATION_LEAD"
    COMMUNICATION_GENERAL = "COMMUNICATION_GENERAL"

    # Copilot (General owner chat)
    COPILOT_CHAT = "COPILOT_CHAT"

    # Unknown / Ambiguous
    AMBIGUOUS = "AMBIGUOUS"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class BrainActionRequest:
    intent: BrainIntent
    confidence: ConfidenceLevel
    module: str
    entities: Dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    requires_owner: bool = True
    action_type: str = "EXECUTE"
    clarification_question: Optional[str] = None
    reply_text: Optional[str] = None
    inline_keyboard: Optional[Any] = None


class NargisBrain:
    """Master AI Brain orchestrator implementing the full cognitive pipeline."""

    def __init__(
        self,
        owner_id: int,
        ai_generator: Optional[Any] = None,
    ) -> None:
        self.owner_id = owner_id
        self.ai = ai_generator

    def is_owner(self, user_id: int) -> bool:
        """Check if user is the authorized owner."""
        return int(user_id) == int(self.owner_id)

    def analyze_intent(self, text: str, is_owner: bool) -> BrainActionRequest:
        """Analyze intent and extract entities with confidence scoring."""
        cleaned = text.strip()
        lower = cleaned.lower()

        # Non-owner messages are strictly routed to Communication Manager
        if not is_owner:
            return BrainActionRequest(
                intent=BrainIntent.COMMUNICATION_LEAD,
                confidence=ConfidenceLevel.HIGH,
                module="communication",
                entities={"raw_text": cleaned},
                requires_owner=False,
            )

        import re

        # 1. Finance Query checks (e.g. "bu hafta qancha pul sarfladim?", "qancha xarajat qildim?")
        if "qancha" in lower and any(kw in lower for kw in ("sarfladim", "xarajat", "pul", "ketdi", "ishlatdim", "bo'ldi")):
            period = "week" if "hafta" in lower else ("month" if "oy" in lower else "today")
            return BrainActionRequest(
                intent=BrainIntent.FINANCE_QUERY,
                confidence=ConfidenceLevel.HIGH,
                module="finance",
                entities={"period": period, "raw_text": cleaned},
                requires_confirmation=False,
                requires_owner=True,
            )

        # 2. Income statements
        if any(kw in lower for kw in ("daromad oldim", "maosh oldim", "pul tushdi", "daromad:", "oylik tushdi", "oylik oldim", "tushum bo'ldi", "daromad")):
            return BrainActionRequest(
                intent=BrainIntent.FINANCE_ADD_INCOME,
                confidence=ConfidenceLevel.HIGH,
                module="finance",
                entities={"raw_text": cleaned, "type": "INCOME"},
                requires_confirmation=True,
                requires_owner=True,
            )

        # 3. Expense statements (Rule based detection)
        if any(kw in lower for kw in ("sarfladim", "ishlatdim", "to'ladim", "berdim", "sotib oldim", "ketdi")):
            # Check if amount is specified via numbers, currency names, or '50k' pattern
            has_digits = bool(re.search(r"\d+", lower))
            has_currency_words = any(w in lower for w in ("ming", "million", "mln", "so'm", "som"))
            has_k_amount = bool(re.search(r"\b\d+\s*k\b", lower))

            if not (has_digits or has_currency_words or has_k_amount) or "ancha" in lower or "kop" in lower or "ko'p" in lower:
                return BrainActionRequest(
                    intent=BrainIntent.AMBIGUOUS,
                    confidence=ConfidenceLevel.LOW,
                    module="finance",
                    entities={"raw_text": cleaned},
                    clarification_question="Qancha summa sarflaganingizni aniq yozing yoki ayting (masalan: 45 ming so'm).",
                )
            return BrainActionRequest(
                intent=BrainIntent.FINANCE_ADD_EXPENSE,
                confidence=ConfidenceLevel.HIGH,
                module="finance",
                entities={"raw_text": cleaned, "type": "EXPENSE"},
                requires_confirmation=True,
                requires_owner=True,
            )

        # 4. Discipline status inquiry (check before plan creation to capture queries like "rejalarim bo'yicha qanday ketyapman?")
        if any(kw in lower for kw in ("qanday ketyapman", "reja qanday", "holat qanday", "natijalarim", "vazifalar qanday", "qanday boryapti")):
            return BrainActionRequest(
                intent=BrainIntent.DISCIPLINE_STATUS,
                confidence=ConfidenceLevel.HIGH,
                module="boss",
                entities={"raw_text": cleaned},
                requires_confirmation=False,
                requires_owner=True,
            )

        # 5. Daily Plan setup
        if "reja:" in lower or "rejalarim:" in lower or "rejalarim" in lower or "bugungi reja" in lower or "asosiy vazifa" in lower:
            return BrainActionRequest(
                intent=BrainIntent.PLAN_CREATE,
                confidence=ConfidenceLevel.HIGH,
                module="boss",
                entities={"raw_text": cleaned},
                requires_confirmation=False,
                requires_owner=True,
            )

        # 6. Task creation with time or deadline (e.g. "Ertaga soat 10 da prezentatsiyani tugatishim kerak")
        if any(kw in lower for kw in ("kerak", "qilishim shart", "vazifa:", "tugatishim", "bajaraman")):
            if any(time_kw in lower for time_kw in ("ertaga", "bugun", "soat", "gacha", "da", "dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba")):
                return BrainActionRequest(
                    intent=BrainIntent.TASK_CREATE,
                    confidence=ConfidenceLevel.HIGH,
                    module="boss",
                    entities={"raw_text": cleaned},
                    requires_confirmation=True,
                    requires_owner=True,
                )

        # 7. Goals creation / inquiry
        if "maqsad:" in lower or "yangi maqsad" in lower:
            return BrainActionRequest(
                intent=BrainIntent.GOAL_CREATE,
                confidence=ConfidenceLevel.HIGH,
                module="goals",
                entities={"raw_text": cleaned},
                requires_confirmation=True,
                requires_owner=True,
            )

        # 8. Dispatch command (send message to someone)
        if any(lower.startswith(prefix) for prefix in ("aliga", "valiga", "xabar yubor", "ayting", "yozing")):
            return BrainActionRequest(
                intent=BrainIntent.DISPATCH_MESSAGE,
                confidence=ConfidenceLevel.HIGH,
                module="dispatcher",
                entities={"raw_text": cleaned},
                requires_confirmation=True,
                requires_owner=True,
            )

        # Fallback to general copilot interaction
        return BrainActionRequest(
            intent=BrainIntent.COPILOT_CHAT,
            confidence=ConfidenceLevel.MEDIUM,
            module="copilot",
            entities={"raw_text": cleaned},
            requires_confirmation=False,
            requires_owner=True,
        )
