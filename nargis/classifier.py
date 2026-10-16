"""Intent and importance classifier for incoming messages."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    GREETING = "GREETING"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    ABOUT_ASADBEK = "ABOUT_ASADBEK"
    COURSE = "COURSE"
    AI = "AI"
    IT = "IT"
    PROJECT = "PROJECT"
    BUSINESS = "BUSINESS"
    PRICE = "PRICE"
    MEETING = "MEETING"
    PERSONAL = "PERSONAL"
    FINANCIAL = "FINANCIAL"
    COMPLAINT = "COMPLAINT"
    URGENT = "URGENT"
    DOCUMENT = "DOCUMENT"
    COLLABORATION = "COLLABORATION"
    JOB = "JOB"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class UrgencyLevel(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class MessageClassification:
    intents: List[Intent] = field(default_factory=lambda: [Intent.UNKNOWN])
    urgency: UrgencyLevel = UrgencyLevel.NORMAL
    importance_score: int = 50   # 0–100
    requires_owner: bool = False
    deadline_hint: Optional[str] = None
    reason_summary: Optional[str] = None

    @property
    def primary_intent(self) -> Intent:
        return self.intents[0] if self.intents else Intent.UNKNOWN

    @property
    def is_critical(self) -> bool:
        return self.urgency == UrgencyLevel.CRITICAL

    @property
    def is_high_or_critical(self) -> bool:
        return self.urgency in (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL)


# ─────────────────────────────────────────────
# Rule-based fast pre-classifier
# ─────────────────────────────────────────────
_CRITICAL_KEYWORDS = [
    "shoshilinch", "urgent", "срочно", "darhol", "hoziroq", "emergency",
    "xavfli", "critical", "favqulodda", "avval", "zudlik",
]
_HIGH_KEYWORDS = [
    "muhim", "important", "biznes", "business", "taklif", "proposal",
    "loyiha", "project", "hamkorlik", "collaboration", "ish taklif",
    "deadline", "muddat", "moliya", "financial", "katta", "big deal",
    "investitsiya", "investment",
]
_MEETING_KEYWORDS = [
    "uchrashuv", "meeting", "uchrashaylik", "qachon bo'sh", "qachon",
    "ko'rishaylik", "zoom", "call", "video call",
]
_COURSE_KEYWORDS = [
    "kurs", "course", "o'qish", "o'rganish", "dars", "lesson",
    "ai pro", "vibe coding", "frontend", "aipply",
]
_PRICE_KEYWORDS = [
    "narx", "price", "qancha", "nechanchi", "to'lov", "payment",
    "arzon", "qimmat", "chegirma", "discount",
]
_GREETING_KEYWORDS = [
    "salom", "assalomu", "hello", "hi", "hey", "привет", "здравствуйте",
]


def _rule_classify(text: str) -> MessageClassification:
    """Fast rule-based classification as fallback / pre-filter."""
    lower = text.lower()
    intents: List[Intent] = []

    if any(k in lower for k in _GREETING_KEYWORDS) and len(text.split()) <= 3:
        intents.append(Intent.GREETING)
        return MessageClassification(
            intents=intents,
            urgency=UrgencyLevel.LOW,
            importance_score=20,
            requires_owner=False,
        )

    if any(k in lower for k in _CRITICAL_KEYWORDS):
        urgency = UrgencyLevel.CRITICAL
        importance_score = 90
        requires_owner = True
        intents.append(Intent.URGENT)
    elif any(k in lower for k in _HIGH_KEYWORDS):
        urgency = UrgencyLevel.HIGH
        importance_score = 70
        requires_owner = True
        intents = [Intent.BUSINESS]
    else:
        urgency = UrgencyLevel.NORMAL
        importance_score = 50
        requires_owner = False

    if any(k in lower for k in _MEETING_KEYWORDS):
        if Intent.MEETING not in intents:
            intents.append(Intent.MEETING)
        if urgency == UrgencyLevel.NORMAL:
            urgency = UrgencyLevel.HIGH
            importance_score = max(importance_score, 65)
            requires_owner = True

    if any(k in lower for k in _COURSE_KEYWORDS):
        if Intent.COURSE not in intents:
            intents.append(Intent.COURSE)

    if any(k in lower for k in _PRICE_KEYWORDS):
        if Intent.PRICE not in intents:
            intents.append(Intent.PRICE)

    if not intents:
        intents = [Intent.GENERAL_QUESTION]

    return MessageClassification(
        intents=intents,
        urgency=urgency,
        importance_score=importance_score,
        requires_owner=requires_owner,
    )


# ─────────────────────────────────────────────
# AI-powered classifier
# ─────────────────────────────────────────────
_CLASSIFIER_SYSTEM_PROMPT = """\
You are a message classification engine for Nargis AI.

Your job: analyze an incoming Telegram message and return a JSON classification.

Return ONLY valid JSON (no explanation, no markdown, no code block):
{
  "intents": ["INTENT1", "INTENT2"],
  "urgency": "LOW|NORMAL|HIGH|CRITICAL",
  "importance_score": 0-100,
  "requires_owner": true|false,
  "deadline_hint": "string or null",
  "reason_summary": "one sentence summary of why user is writing"
}

Available intents (can pick multiple):
GREETING, GENERAL_QUESTION, ABOUT_ASADBEK, COURSE, AI, IT, PROJECT,
BUSINESS, PRICE, MEETING, PERSONAL, FINANCIAL, COMPLAINT, URGENT,
DOCUMENT, COLLABORATION, JOB, OTHER, UNKNOWN

Urgency guide:
- LOW: simple greeting, general curiosity, no action needed
- NORMAL: question that needs a reply
- HIGH: business proposal, meeting request, important topic — Asadbek should see soon
- CRITICAL: emergency, urgent deal, security issue, time-sensitive — notify immediately

importance_score: 0=trivial, 100=maximum emergency
requires_owner: true if Asadbek must personally respond
deadline_hint: extract any mentioned deadline ("ertaga", "2 kun", etc.) or null

Be conservative — do NOT label as CRITICAL unless truly urgent. Do NOT fabricate urgency.\
"""


class IntentClassifier:
    """Classifies incoming messages using AI with rule-based fallback."""

    def __init__(self, ai_generator=None) -> None:
        self.ai = ai_generator

    async def classify(self, message_text: str) -> MessageClassification:
        """Classify message. Uses AI if available, otherwise rule-based."""
        # Quick rule-based pre-check for simple greetings
        lower = message_text.lower().strip()
        if len(lower) <= 20 and any(k in lower for k in _GREETING_KEYWORDS):
            return MessageClassification(
                intents=[Intent.GREETING],
                urgency=UrgencyLevel.LOW,
                importance_score=15,
                requires_owner=False,
            )

        if self.ai is not None:
            try:
                return await self._ai_classify(message_text)
            except Exception as e:
                logger.warning("AI classification failed, using rules: %s", e)

        return _rule_classify(message_text)

    async def _ai_classify(self, message_text: str) -> MessageClassification:
        """Use AI to classify the message."""
        user_prompt = f"Classify this Telegram message:\n\n{message_text}"
        raw = await self.ai.generate(_CLASSIFIER_SYSTEM_PROMPT, user_prompt)

        # Extract JSON from response
        raw = raw.strip()
        # Remove markdown code blocks if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        raw_intents = data.get("intents", ["UNKNOWN"])
        intents = []
        for i in raw_intents:
            try:
                intents.append(Intent(i.upper()))
            except ValueError:
                intents.append(Intent.UNKNOWN)
        if not intents:
            intents = [Intent.UNKNOWN]

        try:
            urgency = UrgencyLevel(data.get("urgency", "NORMAL").upper())
        except ValueError:
            urgency = UrgencyLevel.NORMAL

        importance_score = max(0, min(100, int(data.get("importance_score", 50))))
        requires_owner = bool(data.get("requires_owner", False))
        deadline_hint = data.get("deadline_hint") or None
        reason_summary = data.get("reason_summary") or None

        return MessageClassification(
            intents=intents,
            urgency=urgency,
            importance_score=importance_score,
            requires_owner=requires_owner,
            deadline_hint=deadline_hint,
            reason_summary=reason_summary,
        )
