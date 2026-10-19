"""Unit and integration tests for Nargis 2.0 Central AI Brain and Voice Intelligence Engine."""
from __future__ import annotations

import datetime
from decimal import Decimal
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from finance.service import FinanceService
from nargis.brain import BrainActionRequest, BrainIntent, ConfidenceLevel, NargisBrain
from nargis.voice import VoiceIntelligenceEngine


class DummyAI:
    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        return "Bugun tushlikka 50 ming sarfladim"

    async def generate(self, sys_prompt: str, user_prompt: str) -> str:
        return "Albatta, Asadbek ustoz!"


@pytest_asyncio.fixture
async def voice_env(tmp_path):
    await init_db(data_dir=tmp_path)
    service = FinanceService()
    await service.ensure_default_categories()
    yield service
    await close_db()


@pytest.mark.asyncio
async def test_brain_intent_detection_and_classification():
    owner_id = 5146977644
    brain = NargisBrain(owner_id=owner_id)

    # 1. Non-owner message
    req_guest = brain.analyze_intent("Assalomu alaykum", is_owner=False)
    assert req_guest.intent == BrainIntent.COMMUNICATION_LEAD
    assert req_guest.requires_owner is False

    # 2. Finance expense
    req_exp = brain.analyze_intent("Bugun 100 ming ovqatga sarfladim", is_owner=True)
    assert req_exp.intent == BrainIntent.FINANCE_ADD_EXPENSE
    assert req_exp.requires_confirmation is True

    # 3. Finance query
    req_q = brain.analyze_intent("Bu hafta qancha xarajat qildim?", is_owner=True)
    assert req_q.intent == BrainIntent.FINANCE_QUERY
    assert req_q.entities.get("period") == "week"

    # 4. Task creation
    req_task = brain.analyze_intent("Ertaga soat 10 da prezentatsiyani tugatishim kerak", is_owner=True)
    assert req_task.intent == BrainIntent.TASK_CREATE
    assert req_task.requires_confirmation is True

    # 5. Daily plan creation
    req_plan = brain.analyze_intent("Bugungi reja: Excel darsi va AI loyiha", is_owner=True)
    assert req_plan.intent == BrainIntent.PLAN_CREATE

    # 6. Ambiguous finance without amount
    req_amb = brain.analyze_intent("Bugun ovqatga ancha pul ketdi", is_owner=True)
    assert req_amb.intent == BrainIntent.AMBIGUOUS
    assert req_amb.confidence == ConfidenceLevel.LOW
    assert "aniq yozing" in req_amb.clarification_question


@pytest.mark.asyncio
async def test_voice_expense_execution(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    # Actionable voice expense: "Bugun tushlikka 50 ming sarfladim"
    res = await engine.process_voice_action(
        transcript="Bugun tushlikka 50 ming sarfladim",
        user_id=owner_id,
        bot=None,
        source="voice",
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "50 000" in res["response_text"]
    assert "FOOD" in res["response_text"].upper() or "OVQAT" in res["response_text"].upper() or "TASDIQLAYSIZMI" in res["response_text"].upper()
    assert "transaction_id" in res


@pytest.mark.asyncio
async def test_voice_task_creation(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Ertaga soat 10 da prezentatsiyani tugatishim kerak",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.TASK_CREATE.value
    assert "Vazifa rejalashtirildi" in res["response_text"]
    assert "prezentatsiyani tugatishim kerak" in res["response_text"]
    assert "task_id" in res


@pytest.mark.asyncio
async def test_voice_daily_plan_creation(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Bugun rejalarim: Excel darsi, prezentatsiya va AI loyiha",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.PLAN_CREATE.value
    assert "rejangiz qabul qilindi" in res["response_text"].lower()


@pytest.mark.asyncio
async def test_voice_finance_query(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Bu hafta qancha pul sarfladim?",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.FINANCE_QUERY.value
    assert "Haftalik moliyaviy hisobot" in res["response_text"]


@pytest.mark.asyncio
async def test_voice_ambiguous_message(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Bugun ovqatga ancha pul sarfladim",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is False
    assert "aniq ayting" in res["response_text"].lower() or "qancha summa" in res["response_text"].lower()


@pytest.mark.asyncio
async def test_voice_income_execution(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Bugun 500 ming oylik tushdi",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.FINANCE_ADD_INCOME.value
    assert "500 000" in res["response_text"]


@pytest.mark.asyncio
async def test_voice_multiple_transactions(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    # Multi-item sentence from prompt: "Bugun taksiga 40 ming, tushlikka 55 ming va internetga 100 ming to'ladim"
    res = await engine.process_voice_action(
        transcript="Bugun taksiga 40 ming, tushlikka 55 ming va internetga 100 ming to'ladim",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "3 ta xarajat aniqlandi" in res["response_text"]
    assert "195 000" in res["response_text"]


@pytest.mark.asyncio
async def test_voice_discipline_query(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    res = await engine.process_voice_action(
        transcript="Bugun rejalarim bo'yicha qanday ketyapman?",
        user_id=owner_id,
        bot=None,
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.DISCIPLINE_STATUS.value
    assert "intizom holatingiz" in res["response_text"].lower()
    assert "intizom bali" in res["response_text"].lower()


@pytest.mark.asyncio
async def test_finance_query_yesterday(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    # User's exact prompt: "Meni kechagi xarajatlarim qancha edi"
    res = await engine.process_voice_action(
        transcript="Meni kechagi xarajatlarim qancha edi",
        user_id=owner_id,
        bot=None,
        source="text",
    )
    assert res["success"] is True
    assert res["intent"] == BrainIntent.FINANCE_QUERY.value
    assert "Kechagi moliyaviy hisobot" in res["response_text"]
    assert "Xarajat:" in res["response_text"]


