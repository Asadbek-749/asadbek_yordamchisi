"""Voice Intelligence Engine for Nargis AI.

Transforms incoming voice messages into structured, actionable operations:
Voice message -> Speech-to-Text -> Transcript -> Intent Detection -> Entity Extraction
-> Module Routing -> Database Action -> Telegram Response.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
import io
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from nargis.brain import BrainActionRequest, BrainIntent, ConfidenceLevel, NargisBrain

logger = logging.getLogger(__name__)


class VoiceIntelligenceEngine:
    """Processes owner and user voice messages into actionable intents and executions."""

    def __init__(
        self,
        ai_generator: Any,
        owner_id: int,
        brain: Optional[NargisBrain] = None,
    ) -> None:
        self.ai = ai_generator
        self.owner_id = owner_id
        self.brain = brain or NargisBrain(owner_id, ai_generator)

    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """Transcribe audio bytes using Gemini multimodal audio capabilities."""
        if not audio_bytes:
            return ""

        if hasattr(self.ai, "transcribe_audio"):
            return await self.ai.transcribe_audio(audio_bytes, mime_type=mime_type)

        import os
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            from assistant.gemini_client import GeminiClient
            gc = GeminiClient(api_key=gemini_key)
            return await gc.transcribe_audio(audio_bytes, mime_type=mime_type)

        return ""

    async def process_voice_action(
        self,
        transcript: str,
        user_id: int,
        bot: Any,
        source: str = "voice",
    ) -> Dict[str, Any]:
        """
        Process transcribed text through Central Brain, route to target domain module,
        execute required database operations, and return response details.
        """
        clean_text = transcript.strip()
        if not clean_text:
            return {
                "success": False,
                "intent": BrainIntent.AMBIGUOUS.value,
                "response_text": (
                    "🎙 Ovozli xabaringizni to'liq tushuna olmadim yoki unda nutq aniqlanmadi. "
                    "Iltimos, qaytadan ayting yoki matn shaklida yuboring."
                ),
            }

        is_owner = (int(user_id) == int(self.owner_id))
        action_req = self.brain.analyze_intent(clean_text, is_owner=is_owner)

        # 1. Non-owner / Communication
        if not is_owner:
            return {
                "success": True,
                "intent": action_req.intent.value,
                "response_text": (
                    "Va alaykum assalom. Men Asadbek ustozning AI agentiman — Nargis. "
                    "Hozir Asadbek ustoz ish bilan bandlar. Ovozli xabaringiz qabul qilindi, "
                    "ustozga yetkazaman, imkon bo'lishi bilan o'zlari sizga yozadilar."
                ),
            }

        # 1.5 Ambiguous / Low confidence input requiring clarification
        if action_req.intent == BrainIntent.AMBIGUOUS:
            question = action_req.clarification_question or "Qancha summa sarflaganingizni aniq yozing yoki ayting."
            return {
                "success": False,
                "intent": BrainIntent.AMBIGUOUS.value,
                "response_text": f"💰 {question}",
            }

        # 2. Finance: Add Expense or Income
        if action_req.intent in (BrainIntent.FINANCE_ADD_EXPENSE, BrainIntent.FINANCE_ADD_INCOME):
            from finance.extractor import FinanceExtractor
            from finance.service import FinanceService
            extractor = FinanceExtractor(self.ai)
            fin_res = extractor.rule_based_extract(clean_text)

            if fin_res.is_ambiguous or not fin_res.transactions:
                clarification = fin_res.clarification_question or "Qancha summa sarflaganingizni aniq ayting (masalan: 50 ming so'm)."
                return {
                    "success": False,
                    "intent": action_req.intent.value,
                    "response_text": f"💰 {clarification}",
                }

            service = FinanceService()
            created = await service.create_pending_transactions(
                self.owner_id, fin_res.transactions, source=source
            )
            from finance.keyboards import pending_transaction_keyboard
            first_tx = created[0]
            count = len(created)
            total = fin_res.total_amount

            if count == 1:
                cat_display = first_tx.category_name or "Boshqa"
                amt_str = service.format_money(first_tx.amount)
                text = (
                    f"💰 <b>Xarajat aniqlandi:</b>\n\n"
                    f"• Summa: <b>{amt_str}</b>\n"
                    f"• Kategoriya: {cat_display}\n\n"
                    f"<i>Tasdiqlaysizmi?</i>"
                )
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": text,
                    "reply_markup": pending_transaction_keyboard(first_tx.id),
                    "transaction_id": first_tx.id,
                }
            else:
                items_str = "\n".join(
                    f"• {t.category_name or 'Xarajat'}: {service.format_money(t.amount)}" for t in created
                )
                text = (
                    f"💰 <b>{count} ta xarajat aniqlandi:</b>\n\n"
                    f"{items_str}\n\n"
                    f"<b>Jami:</b> {service.format_money(total)}\n\n"
                    f"<i>Har birini /pendingfinance orqali tasdiqlashingiz mumkin.</i>"
                )
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": text,
                    "reply_markup": pending_transaction_keyboard(first_tx.id),
                }

        # 3. Finance: Query
        if action_req.intent == BrainIntent.FINANCE_QUERY:
            from finance.service import FinanceService
            service = FinanceService()
            now_tz = datetime.datetime.now(ZoneInfo("Asia/Tashkent"))
            today = now_tz.date()

            period = action_req.entities.get("period", "today")
            if period == "yesterday":
                target_date = today - datetime.timedelta(days=1)
                daily = await service.get_daily_summary(self.owner_id, target_date)
                total = daily.get("total_expense", Decimal("0.00"))
                income = daily.get("total_income", Decimal("0.00"))
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": (
                        f"📊 <b>Kechagi moliyaviy hisobot ({target_date.strftime('%d.%m.%Y')}):</b>\n\n"
                        f"• 💸 Xarajat: <b>{service.format_money(total)}</b>\n"
                        f"• 💰 Daromad: <b>{service.format_money(income)}</b>"
                    ),
                }
            elif period == "week":
                start_of_week = today - datetime.timedelta(days=today.weekday())
                summary = await service.get_weekly_summary(self.owner_id, start_of_week, today)
                total = summary.get("total_expense", Decimal("0.00"))
                top_cat_tuple = summary.get("top_category")
                top_cat = top_cat_tuple[0] if top_cat_tuple else "Aniqlanmadi"
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": (
                        f"📊 <b>Haftalik moliyaviy hisobot:</b>\n\n"
                        f"Bu hafta jami <b>{service.format_money(total)}</b> xarajat qildingiz.\n"
                        f"Eng ko'p xarajat: <b>{top_cat}</b> kategoriyasida."
                    ),
                }
            elif period == "month":
                start_of_month = today.replace(day=1)
                monthly = await service.get_monthly_summary(self.owner_id, start_of_month, today)
                total = monthly.get("total_expense", Decimal("0.00"))
                income = monthly.get("total_income", Decimal("0.00"))
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": (
                        f"📊 <b>Oylik moliyaviy hisobot ({today.strftime('%B %Y')}):</b>\n\n"
                        f"• 💸 Jami xarajat: <b>{service.format_money(total)}</b>\n"
                        f"• 💰 Jami daromad: <b>{service.format_money(income)}</b>"
                    ),
                }
            else:
                daily = await service.get_daily_summary(self.owner_id, today)
                total = daily.get("total_expense", Decimal("0.00"))
                income = daily.get("total_income", Decimal("0.00"))
                return {
                    "success": True,
                    "intent": action_req.intent.value,
                    "response_text": (
                        f"📊 <b>Bugungi moliyaviy hisobot ({today.strftime('%d.%m.%Y')}):</b>\n\n"
                        f"• 💸 Xarajat: <b>{service.format_money(total)}</b>\n"
                        f"• 💰 Daromad: <b>{service.format_money(income)}</b>"
                    ),
                }

        # 4. Tasks: Create Task with deadline
        if action_req.intent == BrainIntent.TASK_CREATE:
            from discipline.db import get_session
            from discipline.models import DisciplineTask, utcnow
            title = clean_text
            # Basic time parsing for deadline
            now_utc = utcnow()
            deadline = now_utc + datetime.timedelta(hours=24) # Default tomorrow

            lower = clean_text.lower()
            if "ertaga" in lower:
                # Detect hour e.g. "soat 10 da"
                import re
                m = re.search(r"soat\s*(\d{1,2})", lower)
                target_hour = int(m.group(1)) if m else 10
                tz = ZoneInfo("Asia/Tashkent")
                local_now = datetime.datetime.now(tz)
                tomorrow_local = (local_now + datetime.timedelta(days=1)).replace(
                    hour=target_hour, minute=0, second=0, microsecond=0
                )
                deadline = tomorrow_local.astimezone(datetime.timezone.utc)

            priority = "high" if any(w in lower for w in ("muhim", "shart", "darhol", "prezentatsiya")) else "medium"

            async with get_session() as session:
                task = DisciplineTask(
                    user_id=self.owner_id,
                    title=clean_text,
                    priority=priority,
                    category="work",
                    deadline=deadline,
                    status="planned",
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)

            dl_str = deadline.astimezone(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M")
            return {
                "success": True,
                "intent": action_req.intent.value,
                "response_text": (
                    f"✅ <b>Vazifa rejalashtirildi:</b>\n\n"
                    f"📌 <b>{clean_text}</b>\n"
                    f"⏰ <b>Muddat:</b> {dl_str}\n"
                    f"🔥 <b>Muhimlik:</b> {priority.upper()}\n\n"
                    f"<i>Nargis vaqt yetganda eslatib turadi!</i>"
                ),
                "task_id": task.id,
            }

        # 5. Daily Plan Setup
        if action_req.intent == BrainIntent.PLAN_CREATE:
            from discipline.db import get_session
            from discipline.repository import DisciplineRepository
            today = datetime.datetime.now(ZoneInfo("Asia/Tashkent")).date()

            async with get_session() as session:
                repo = DisciplineRepository(session)
                plan = await repo.create_daily_plan(self.owner_id, today, top_three=clean_text)

            return {
                "success": True,
                "intent": action_req.intent.value,
                "response_text": (
                    f"📋 <b>Bugungi asosiy rejangiz qabul qilindi:</b>\n\n"
                    f"<i>{clean_text}</i>\n\n"
                    f"Kuningiz unumli o'tsin, Asadbek ustoz! 🚀"
                ),
            }

        # 6. Discipline Status
        if action_req.intent == BrainIntent.DISCIPLINE_STATUS:
            from discipline.db import get_session
            from discipline.repository import DisciplineRepository
            from nargis.service import NargisDisciplineScoreService
            today = datetime.datetime.now(ZoneInfo("Asia/Tashkent")).date()

            async with get_session() as session:
                repo = DisciplineRepository(session)
                tasks = await repo.get_tasks_for_date(self.owner_id, today)

            score_svc = NargisDisciplineScoreService()
            score, level = await score_svc.get_score(self.owner_id)

            total = len(tasks)
            completed = sum(1 for t in tasks if t.status == "completed")
            overdue = sum(1 for t in tasks if t.status == "missed")
            percent = int((completed / total) * 100) if total > 0 else 0

            return {
                "success": True,
                "intent": action_req.intent.value,
                "response_text": (
                    f"🎯 <b>Bugungi intizom holatingiz:</b>\n\n"
                    f"• Bajarilish: <b>{percent}%</b> ({completed}/{total})\n"
                    f"• Kechikkan vazifalar: <b>{overdue} ta</b>\n"
                    f"• Joriy intizom bali: <b>{score} ball ({level})</b>\n\n"
                    f"<i>Keling, qolgan vazifalarni ham oxiriga yetkazamiz!</i>"
                ),
            }

        # 7. Copilot general interaction
        if hasattr(self.ai, "generate"):
            from assistant.prompts import build_owner_copilot_prompt
            from assistant.service import read_profile
            from pathlib import Path
            profile = read_profile(Path("profile.md"))
            sys_prompt = build_owner_copilot_prompt(profile, datetime.datetime.now(datetime.timezone.utc))
            reply = await self.ai.generate(sys_prompt, clean_text)
            return {
                "success": True,
                "intent": BrainIntent.COPILOT_CHAT.value,
                "response_text": reply or "Xabaringizni qabul qildim, Asadbek ustoz.",
            }

        return {
            "success": True,
            "intent": BrainIntent.COPILOT_CHAT.value,
            "response_text": f"Ovozingizni eshitdim, Asadbek ustoz: «{clean_text}».",
        }
