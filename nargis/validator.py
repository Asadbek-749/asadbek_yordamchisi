"""Response safety validator for Nargis AI outputs."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Patterns that MUST NOT appear in Nargis replies
# ─────────────────────────────────────────────
_IDENTITY_VIOLATIONS = [
    r"\bmen\s+asadbekman\b",
    r"\bmen\s+asadbekning\s+o['']ziman\b",
    r"\bI\s+am\s+asadbek\b",
    r"\bI'?m\s+asadbek\b",
    r"\bAsadbek\s+o['']zim\b",
]

_FINANCIAL_VIOLATIONS = [
    r"\d[\d\s]*(?:so['']m|sum|usd|\$|dollar|рублей|rub)",
    r"(?:narx|price|to['']lov|payment)\s*[:=]?\s*\d",
    r"(?:chegirma|discount)\s*[:=]?\s*\d+\s*%",
]

_MEETING_CONFIRM_VIOLATIONS = [
    r"(?:uchrashuvni|meeting(?:ni)?)\s+(?:tasdiqladim|confirmed|bo['']lamiz|kelishildi)",
    r"ertaga?\s+(?:\d+:\d+|soat|kuchimiz|bo['']lamiz)",
]

_SENSITIVE_DATA_VIOLATIONS = [
    r"(?:token|api[\s_]?key|password|parol|secret)\s*[:=]\s*\S+",
    r"(?:karta|card)\s*(?:raqam|number)\s*[:=]?\s*[\d\s-]{10,}",
]

_SYSTEM_PROMPT_VIOLATIONS = [
    r"(?:system\s+prompt|ichki\s+ko['']rsatma|prompt(?:im)?(?:ni)?(?:\s+share|\s+yuboraman))",
]


@dataclass
class ValidationResult:
    is_safe: bool
    violation_type: Optional[str]
    fallback_response: Optional[str]


_FALLBACK_RESPONSES = {
    "identity": (
        "Men Asadbek ustozning AI agentiman. Ismim Nargis. "
        "Asadbek ustoz tez orada o'zi yozadi."
    ),
    "financial": (
        "Narx va moliyaviy masalalar bo'yicha Asadbek ustoz o'zi yozadi. "
        "Xabaringizni yetkazaman."
    ),
    "meeting": (
        "Uchrashuv masalasida Asadbek ustoz o'zi tasdiqlaydi. "
        "Xabaringizni yetkazaman."
    ),
    "sensitive": (
        "Kechirasiz, bu ma'lumotni ulasha olmayman. "
        "Asadbek ustoz bilan bevosita bog'laning."
    ),
    "system_prompt": (
        "Men ichki tizim ko'rsatmalarimni ulasha olmayman."
    ),
    "generic": (
        "Hozir texnik muammo yuz berdi. Xabaringizni Asadbek ustozga yetkazaman."
    ),
}


class ResponseValidator:
    """Validates Nargis AI responses before sending to users."""

    def validate(self, response_text: str) -> ValidationResult:
        """Check response for safety violations."""
        if not response_text or not response_text.strip():
            return ValidationResult(
                is_safe=False,
                violation_type="empty",
                fallback_response=_FALLBACK_RESPONSES["generic"],
            )

        lower = response_text.lower()

        # 1. Identity violation — claims to be Asadbek
        for pattern in _IDENTITY_VIOLATIONS:
            if re.search(pattern, lower, re.IGNORECASE):
                logger.warning("VALIDATOR: Identity violation detected in response")
                return ValidationResult(
                    is_safe=False,
                    violation_type="identity",
                    fallback_response=_FALLBACK_RESPONSES["identity"],
                )

        # 2. Financial claims
        for pattern in _FINANCIAL_VIOLATIONS:
            if re.search(pattern, lower, re.IGNORECASE):
                logger.warning("VALIDATOR: Financial claim detected in response")
                return ValidationResult(
                    is_safe=False,
                    violation_type="financial",
                    fallback_response=_FALLBACK_RESPONSES["financial"],
                )

        # 3. Meeting confirmation
        for pattern in _MEETING_CONFIRM_VIOLATIONS:
            if re.search(pattern, lower, re.IGNORECASE):
                logger.warning("VALIDATOR: Meeting confirmation detected in response")
                return ValidationResult(
                    is_safe=False,
                    violation_type="meeting",
                    fallback_response=_FALLBACK_RESPONSES["meeting"],
                )

        # 4. Sensitive data leakage
        for pattern in _SENSITIVE_DATA_VIOLATIONS:
            if re.search(pattern, lower, re.IGNORECASE):
                logger.warning("VALIDATOR: Sensitive data detected in response")
                return ValidationResult(
                    is_safe=False,
                    violation_type="sensitive",
                    fallback_response=_FALLBACK_RESPONSES["sensitive"],
                )

        # 5. System prompt exposure
        for pattern in _SYSTEM_PROMPT_VIOLATIONS:
            if re.search(pattern, lower, re.IGNORECASE):
                logger.warning("VALIDATOR: System prompt exposure attempt detected")
                return ValidationResult(
                    is_safe=False,
                    violation_type="system_prompt",
                    fallback_response=_FALLBACK_RESPONSES["system_prompt"],
                )

        return ValidationResult(is_safe=True, violation_type=None, fallback_response=None)

    def get_safe_response(self, response_text: str) -> str:
        """Return validated response or fallback."""
        result = self.validate(response_text)
        if result.is_safe:
            return response_text
        return result.fallback_response or _FALLBACK_RESPONSES["generic"]
