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


@pytest.mark.asyncio
async def test_voice_owner_spoken_expenses_with_confirm_keyboards(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    # 1. "Kecha 168 000 so'mga obed qilgandim."
    r1 = await engine.process_voice_action(
        transcript="Kecha 168 000 so'mga obed qilgandim.",
        user_id=owner_id,
        bot=None,
        source="voice",
    )
    assert r1["success"] is True
    assert r1["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "168 000 so'm" in r1["response_text"]
    assert r1.get("reply_markup") is not None
    assert "fin:confirm" in str(r1["reply_markup"])

    # 2. "8000 so'mga kofe oldim."
    r2 = await engine.process_voice_action(
        transcript="8000 so'mga kofe oldim.",
        user_id=owner_id,
        bot=None,
        source="voice",
    )
    assert r2["success"] is True
    assert r2["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "8 000 so'm" in r2["response_text"]
    assert r2.get("reply_markup") is not None

    # 3. "Bugun abetga 26 000 so'mga somsa olib yedim."
    r3 = await engine.process_voice_action(
        transcript="Bugun abetga 26 000 so'mga somsa olib yedim.",
        user_id=owner_id,
        bot=None,
        source="voice",
    )
    assert r3["success"] is True
    assert r3["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "26 000 so'm" in r3["response_text"]
    assert r3.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_voice_spoken_suffixes_and_no_tashkent_collision(voice_env: FinanceService):
    owner_id = 5146977644
    engine = VoiceIntelligenceEngine(ai_generator=DummyAI(), owner_id=owner_id)

    # 1. Uzbek suffix "-ga" on ming: "Dorixonadan 85 mingga dori oldim"
    r_med = await engine.process_voice_action(
        transcript="Dorixonadan 85 mingga dori oldim",
        user_id=owner_id,
        bot=None,
    )
    assert r_med["success"] is True
    assert r_med["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "85 000" in r_med["response_text"]
    assert "HEALTH" in r_med["response_text"].upper() or "DORI" in r_med["response_text"].upper()

    # 2. Avoid "osh" collision in "Toshkentga": should be TRANSPORT, not FOOD
    r_tax = await engine.process_voice_action(
        transcript="Toshkentga taksiga 50 ming berdim",
        user_id=owner_id,
        bot=None,
    )
    assert r_tax["success"] is True
    assert r_tax["intent"] == BrainIntent.FINANCE_ADD_EXPENSE.value
    assert "50 000" in r_tax["response_text"]
    assert "TRANSPORT" in r_tax["response_text"].upper()

    # 3. Meeting task without "kerak": "Ertaga soat 9 da uchrashuv bor"
    r_meet = await engine.process_voice_action(
        transcript="Ertaga soat 9 da uchrashuv bor",
        user_id=owner_id,
        bot=None,
    )
    assert r_meet["success"] is True
    assert r_meet["intent"] == BrainIntent.TASK_CREATE.value
    assert "uchrashuv" in r_meet["response_text"].lower()

    # 4. Weekly comparison query via voice
    r_comp = await engine.process_voice_action(
        transcript="Bu haftalik xarajatlarni taqqoslab ber",
        user_id=owner_id,
        bot=None,
    )
    assert r_comp["success"] is True
    assert r_comp["intent"] == BrainIntent.FINANCE_QUERY.value
    assert "HAFTALIK" in r_comp["response_text"].upper()



