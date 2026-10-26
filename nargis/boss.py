"""Strict AI Boss & Accountability Engine for Nargis 2.0.

Manages:
- Morning Control Escalation (07:00, 07:15, 07:30, 08:00, 08:30)
- Midday Progress Audit (13:00)
- Evening Accountability Review & 1-10 Rating (21:00)
- Tomorrow Planning Assistance (22:00)
- Postponement Guard (strictly enforces delay reasons on 3+ postponements)
- Procrastination Analysis (planned vs actual duration)
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from discipline.db import get_session
from discipline.models import (
    AccountabilityLog,
    DailyPlan,
    DisciplineScore,
    DisciplineScoreEvent,
    DisciplineTask,
    NargisBossState,
    utcnow,
)
from discipline.repository import DisciplineRepository
from nargis.service import NargisDisciplineScoreService

logger = logging.getLogger(__name__)


class AIBossEngine:
    """Orchestrates strict daily accountability and discipline enforcement."""

    def __init__(self, owner_id: int, bot: Any = None) -> None:
        self.owner_id = owner_id
        self.bot = bot

    async def get_or_create_boss_state(self, check_date: datetime.date) -> NargisBossState:
        """Fetch or create daily boss tracking state."""
        async with get_session() as session:
            stmt = select(NargisBossState).where(
                NargisBossState.user_id == self.owner_id,
                NargisBossState.check_date == check_date,
            )
            result = await session.execute(stmt)
            state = result.scalar_one_or_none()

            if not state:
                state = NargisBossState(
                    user_id=self.owner_id,
                    check_date=check_date,
                    morning_level=0,
                    morning_completed=False,
                    midday_checked=False,
                    evening_checked=False,
                )
                session.add(state)
                await session.commit()
                await session.refresh(state)

            return state

    async def check_morning_control_step(
        self,
        local_time_str: str,
        today: datetime.date,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluate morning accountability step based on current local time.
        07:00 -> Initial plan request
        07:15 -> Level 1 Reminder
        07:30 -> Level 2 Strict Reminder (-5 score penalty)
        08:00 -> Level 3/4 Reason Required
        08:30 -> Level 5 Escalation
        """
        # First verify if daily plan was already submitted
        async with get_session() as session:
            repo = DisciplineRepository(session)
            plan = await repo.get_daily_plan(self.owner_id, today)
            if plan and (plan.top_three_tasks or plan.is_confirmed):
                # Plan already submitted, mark completed in state
                await session.execute(
                    update(NargisBossState)
                    .where(
                        NargisBossState.user_id == self.owner_id,
                        NargisBossState.check_date == today,
                    )
                    .values(morning_completed=True)
                )
                await session.commit()
                return None

        state = await self.get_or_create_boss_state(today)
        if state.morning_completed:
            return None

        # Morning control only applies during morning hours (< 12:00)
        if local_time_str < "07:00" or local_time_str >= "12:00":
            return None

        # Level 5: 08:30+ escalation
        if local_time_str >= "08:30" and state.morning_level < 5:
            await self._update_morning_level(today, 5)
            return {
                "level": 5,
                "message": (
                    "🚨 <b>NARGIS AI BOSS — KUNLIK REJA BO'YICHA ESKALATSIYA!</b>\n\n"
                    "Bugun ertalabki rejalashtirish to'liq o'tkazib yuborildi.\n"
                    "Bu intizom qoidalarining jiddiy buzilishi sifatida hisobotga kiritiladi."
                ),
            }

        # Level 4: 08:00+ reason required
        if local_time_str >= "08:00" and state.morning_level < 4:
            await self._update_morning_level(today, 4)
            return {
                "level": 4,
                "message": (
                    "🛑 <b>Reja hali ham kiritilmagan!</b>\n\n"
                    "Soat 08:00 bo'ldi. Rejasiz ishlash samaradorlikni 50% ga pasaytiradi.\n"
                    "Iltimos, kechikish sababini yozing va bugungi rejangizni kiritib o'ting."
                ),
            }

        # Level 3: 07:30+ strict warning + delay penalty
        if local_time_str >= "07:30" and state.morning_level < 3:
            await self._update_morning_level(today, 3)
            # Deduct 5 discipline points
            score_svc = NargisDisciplineScoreService()
            await score_svc.penalize(
                user_id=self.owner_id,
                event_type="PLAN_DELAYED",
                reason="07:30 gacha kunlik reja kiritilmadi",
                delta=-5,
            )
            return {
                "level": 3,
                "message": (
                    "⚠️ <b>Asadbek, sizdan javob hali kelmadi!</b>\n\n"
                    "Bu bugungi intizom nazoratidagi kechikish sifatida qayd qilindi <b>(-5 ball)</b>.\n"
                    "Kechikish sababini bartaraf eting va zudlik bilan rejangizni kiriting!"
                ),
            }

        # Level 2: 07:15+ reminder
        if local_time_str >= "07:15" and state.morning_level < 2:
            await self._update_morning_level(today, 2)
            return {
                "level": 2,
                "message": (
                    "⏳ <b>Rejangiz hali kiritilmadi!</b>\n\n"
                    "Bugungi kunni rejasiz boshlamang. Har qanday muvaffaqiyat aniq rejalashtirishdan boshlanadi.\n"
                    "Hozir 3 ta asosiy vazifangizni yuboring!"
                ),
            }

        # Level 1: 07:00+ initial plan prompt
        if local_time_str >= "07:00" and state.morning_level < 1:
            await self._update_morning_level(today, 1)
            return {
                "level": 1,
                "message": (
                    "🌅 <b>Xayrli tong, Asadbek ustoz!</b>\n\n"
                    "Bugungi kuningiz unumli o'tishi uchun <b>3 ta eng muhim asosiy vazifangizni</b> yozing yoki ovozli xabar qilib yuboring 🎯\n\n"
                    "<i>Masalan: 1. Python darsi, 2. AI loyiha, 3. Sport mashg'uloti</i>"
                ),
            }

        return None

    async def _update_morning_level(self, today: datetime.date, level: int) -> None:
        async with get_session() as session:
            await session.execute(
                update(NargisBossState)
                .where(
                    NargisBossState.user_id == self.owner_id,
                    NargisBossState.check_date == today,
                )
                .values(morning_level=level)
            )
            await session.commit()

    async def get_midday_progress_report(self, today: datetime.date) -> Dict[str, Any]:
        """Compile 13:00 midday progress audit."""
        async with get_session() as session:
            repo = DisciplineRepository(session)
            tasks = await repo.get_tasks_for_date(self.owner_id, today)

        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == "completed")
        in_progress = sum(1 for t in tasks if t.status == "in_progress")
        overdue = sum(1 for t in tasks if t.status == "missed")
        planned = sum(1 for t in tasks if t.status == "planned")
        pct = int((completed / total) * 100) if total > 0 else 0

        text = (
            f"☀️ <b>KUN O'RTASI — INTIZOM NAZORATI (13:00)</b>\n\n"
            f"Bugungi vazifalar holati:\n"
            f"• Rejalashtirilgan: <b>{total} ta</b>\n"
            f"• Bajarilgan: <b>{completed} ta</b> ({pct}%)\n"
            f"• Jarayonda: <b>{in_progress} ta</b>\n"
            f"• Kechikkan / Muddati o'tgan: <b>{overdue} ta</b>\n\n"
        )
        if overdue > 0:
            text += f"⚠️ <b>{overdue} ta muhim vazifa kechikmoqda!</b> Qaysi birini hozir boshlaysiz?"
        else:
            text += "🎯 Sur'at yaxshi! Qolgan vazifalarni ham sifatli davom ettiring."

        return {
            "total": total,
            "completed": completed,
            "overdue": overdue,
            "in_progress": in_progress,
            "percent": pct,
            "text": text,
        }

    async def handle_postponement_request(
        self,
        task_id: int,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Strict postponement guard:
        If postponement count >= 3, strictly requires reason before allowing postponement.
        """
        async with get_session() as session:
            stmt = select(DisciplineTask).where(DisciplineTask.id == task_id, DisciplineTask.user_id == self.owner_id)
            res = await session.execute(stmt)
            task = res.scalar_one_or_none()

            if not task:
                return {"success": False, "message": "Vazifa topilmadi."}

            current_count = task.postponement_count or 0

            # Guard check: 3+ postponements REQUIRE explanation!
            if current_count >= 3 and (not reason or len(reason.strip()) < 5):
                return {
                    "success": False,
                    "blocked": True,
                    "postponement_count": current_count,
                    "message": (
                        f"⚠️ <b>DIQQAT: Ushbu vazifa {current_count} marta ko'chirilgan!</b>\n\n"
                        f"📌 <b>{task.title}</b>\n\n"
                        f"Nargis AI Boss qoidalariga ko'ra, 3 martadan ortiq kechiktirilgan vazifani "
                        f"yana ko'chirishdan oldin <b>haqiqiy sababini yozishingiz shart</b>.\n\n"
                        f"<i>Iltimos, nima sababdan kechikayotganini yozing.</i>"
                    ),
                }

            # Increment postponement count and record penalty
            task.postponement_count = current_count + 1
            task.status = "planned"
            if reason:
                task.delay_reason = reason.strip()
                log = AccountabilityLog(
                    user_id=self.owner_id,
                    task_id=task.id,
                    reason_category="delayed_postponement",
                    reason_details=reason.strip(),
                    action_taken="rescheduled",
                )
                session.add(log)

            await session.commit()

            score_svc = NargisDisciplineScoreService()
            await score_svc.penalize(
                user_id=self.owner_id,
                event_type="TASK_POSTPONED",
                reason=f"Vazifa ko'chirildi ({task.title})",
                task_id=task.id,
                delta=-10,
            )

            return {
                "success": True,
                "blocked": False,
                "postponement_count": task.postponement_count,
                "message": (
                    f"📅 <b>Vazifa ko'chirildi ({task.postponement_count}-marta).</b>\n\n"
                    f"📌 <b>{task.title}</b>\n"
                    f"Intizom bali: <b>-10 ball</b> chegirildi."
                ),
            }

    async def get_boss_status_summary(self, today: datetime.date) -> str:
        """Compile comprehensive real-time accountability summary for owner."""
        state = await self.get_or_create_boss_state(today)
        score_svc = NargisDisciplineScoreService()
        score, level = await score_svc.get_score(self.owner_id)

        midday = await self.get_midday_progress_report(today)

        async with get_session() as session:
            repo = DisciplineRepository(session)
            plan = await repo.get_daily_plan(self.owner_id, today)

        plan_status = "✅ Kiritilgan" if (plan and (plan.top_three_tasks or plan.is_confirmed)) else f"⚠️ Kiritilmagan (Nazorat bosqichi: {state.morning_level}/5)"

        text = (
            f"🎯 <b>NARGIS AI BOSS — INTIZOM VA NAZORAT PANELI</b>\n\n"
            f"🏆 <b>Intizom bali:</b> {score} ball (Daraja: <b>{level}</b>)\n"
            f"📋 <b>Kunlik reja:</b> {plan_status}\n\n"
            f"📊 <b>Bugungi vazifalar sur'ati:</b>\n"
            f"• Jami: <b>{midday['total']} ta</b>\n"
            f"• Bajarildi: <b>{midday['completed']} ta</b> ({midday['percent']}%)\n"
            f"• Jarayonda: <b>{midday['in_progress']} ta</b>\n"
            f"• Kechikkan / Muddati o'tgan: <b>{midday['overdue']} ta</b>\n\n"
        )
        if state.morning_level >= 3 and not (plan and plan.is_confirmed):
            text += "⚠️ <i>Eslatma: Reja kechikkani sababli bugun jarima qo'llanilgan.</i>\n\n"
        if midday['overdue'] > 0:
            text += "🚨 <i>Kechikayotgan vazifalarni zudlik bilan yakunlang yoki /postpone orqali sabab ko'rsating!</i>"
        else:
            text += "🔥 <i>Sur'at a'lo darajada! Rejalashtirilgan natijalarga erishishda davom eting!</i>"

        return text
