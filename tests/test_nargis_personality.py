"""Tests for Nargis emotional vitality, emoji tone, and two-tier rude handling protocol."""
from __future__ import annotations

import datetime
from assistant.prompts import (
    build_owner_copilot_prompt,
    build_system_prompt,
    build_system_prompt_for_new_user,
    NARGIS_IDENTITY,
)
from nargis.classifier import Intent, UrgencyLevel, _rule_classify
from nargis.validator import ResponseValidator


def test_prompts_contain_emojis_and_emotional_vitality():
    now = datetime.datetime.now(datetime.timezone.utc)
    profile = "Teacher at Aipply"

    sys_prompt = build_system_prompt(profile, now)
    assert "🌸" in sys_prompt or "😊" in sys_prompt
    assert "TWO-TIER PROTOCOL" in sys_prompt
    assert "TIER 1" in sys_prompt
    assert "TIER 2" in sys_prompt
    assert "Og'zingizga qarab gapiring" in sys_prompt

    owner_prompt = build_owner_copilot_prompt(profile, now)
    assert "EMOTIONAL VITALITY" in owner_prompt
    assert "🌸" in owner_prompt or "🫡" in owner_prompt

    new_user_prompt = build_system_prompt_for_new_user(profile, now)
    assert "🌸" in new_user_prompt or "😊" in new_user_prompt


def test_classifier_detects_rude_words():
    # Uzbek rude words
    res = _rule_classify("Ey ahmoq bot san")
    assert Intent.COMPLAINT in res.intents
    assert res.urgency == UrgencyLevel.HIGH
    assert res.requires_owner is True
    assert "qo'pol" in res.reason_summary

    # Multi-lingual rude words
    res2 = _rule_classify("Sen debil jinni ekansan")
    assert Intent.COMPLAINT in res2.intents
    assert res2.requires_owner is True


def test_validator_allows_sharp_rebuttal():
    validator = ResponseValidator()
    # Sharp rebuttal from Tier 2 should NOT be blocked as unsafe
    rebuttal = "Og'zingizga qarab gapiring! Bu yerda sizga hech kim haqorat qilishga ruxsat bermagan."
    res = validator.validate(rebuttal)
    assert res.is_safe is True
    assert validator.get_safe_response(rebuttal) == rebuttal
