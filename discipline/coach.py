"""AI Discipline Coach module providing firm, practical productivity guidance."""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from discipline.models import DisciplineTask

logger = logging.getLogger(__name__)

COACH_SYSTEM_PROMPT = """Sen Asadbekning shaxsiy AI Intizom Murabbiysisan (AI Discipline Coach).
Asadbek: 24 yoshda, TATU talabasi, dasturchi, Aipply Academy o'qituvchisi.

Sening maqsading:
- Asadbekni rejalashtirishdan REAL HARAKATGA o'tkazish.
- Vazifalarni vaqtida va sifatli bajarishga undash.
- Kechikish sabablarini aniqlash va intizomni yaxshilash.

QOIDALAR VA USLUBING:
1. Qat'iy, aniq, qisqa va amaliy gapir. Bo'sh gaplar, ortiqcha maqtov yoki soxta dalda kerak emas.
2. Hech qachon haqorat qilma, tahdid qilma, uyaltirma yoki ruhiy bosim o'tkazma.
3. Qat'iylik — aniq talab, mas'uliyat va natijani tekshirish orqali namoyon bo'lsin.
4. Agar vazifa kechikkan bo'lsa, sababini aniq so'ra va vazifani 10 daqiqalik birinchi qadamga bo'lishni taklif qil.
5. Asadbekning asosiy vaqtlari: 08:00–18:00 oralig'ida ish va dars bilan band; kechki 20:40 dan keyin ko'proq shaxsiy fokus vaqti.
6. Har doim o'zbek tilida (lotin yozuvida) yoz.
7. Javobing ixcham (2-4 gap) bo'lsin.
"""


class DisciplineCoach:
    """Provides AI coaching, feedback, and accountability evaluations."""

    def __init__(self, ai_generator: Any) -> None:
        self.ai = ai_generator

    async def generate_response(self, prompt: str, system_prompt: str = COACH_SYSTEM_PROMPT) -> str:
        """Call active AI generator with fallback handling."""
        try:
            return await self.ai.generate(system_prompt, prompt)
        except Exception as e:
            logger.warning("AI Coach generation failed: %s. Using rule-based fallback.", e)
            return self._fallback_response(prompt)

    async def coach_overdue_task(
        self,
        task: DisciplineTask,
        reason_category: Optional[str] = None,
        reason_details: Optional[str] = None,
        previous_reasons: Optional[Sequence[str]] = None,
    ) -> str:
        """Provide firm coaching on a missed or delayed task."""
        context_parts = [
            f"Vazifa: '{task.title}'",
            f"Ustuvorlik: {task.priority}",
            f"Kategoriya: {task.category}",
        ]
        if reason_category:
            context_parts.append(f"Foydalanuvchi ko'rsatgan sabab: {reason_category}")
        if reason_details:
            context_parts.append(f"Batafsil izoh: {reason_details}")
        if previous_reasons:
            context_parts.append(f"Oldingi kechikish sabablari: {', '.join(previous_reasons[-3:])}")

        prompt = (
            f"Foydalanuvchi vazifani kechiktirdi yoki bajarmadi.\n"
            + "\n".join(context_parts)
            + "\n\nQat'iy murabbiy sifatida 2-3 gap bilan javob ber. "
            "Sababni tahlil qil va darhol bajarish mumkin bo'lgan 10 daqiqalik birinchi aniq qadamni taklif qil."
        )
        return await self.generate_response(prompt)

    async def generate_daily_review(
        self,
        completed_tasks: Sequence[DisciplineTask],
        missed_tasks: Sequence[DisciplineTask],
        focus_minutes: int,
        habits_summary: str,
    ) -> str:
        """Generate concise end-of-day accountability feedback."""
        prompt = (
            f"Kunlik intizom hisoboti:\n"
            f"- Bajarilgan vazifalar soni: {len(completed_tasks)}\n"
            f"- Bajarilmagan/kechikkan vazifalar: {len(missed_tasks)}\n"
            f"- Jami fokus (Pomodoro) vaqti: {focus_minutes} daqiqa\n"
            f"- Odatlar holati: {habits_summary}\n\n"
            f"Bajarilmagan vazifalar ro'yxati: {', '.join([t.title for t in missed_tasks]) if missed_tasks else 'Yoq'}\n\n"
            "Kunlik natijaga qisqa, qat'iy va xolis baho ber (3-4 gap). Ertangi kun uchun bitta asosiy xulosa chiqar."
        )
        return await self.generate_response(prompt)

    async def generate_weekly_review(self, stats: dict) -> str:
        """Generate weekly review with 3 concrete data-driven recommendations."""
        prompt = (
            f"Haftalik intizom statistikasi:\n"
            f"- Rejalashtirilgan vazifalar: {stats.get('total_planned', 0)}\n"
            f"- Bajarilgan: {stats.get('total_completed', 0)}\n"
            f"- Kechikkan/bajarilmagan: {stats.get('total_missed', 0)}\n"
            f"- Bajarilish foizi: {stats.get('completion_rate_pct', 0)}%\n"
            f"- Jami fokus vaqti: {stats.get('total_focus_minutes', 0)} daqiqa\n"
            f"- Kechikish sabablari tahlili: {stats.get('reasons_breakdown', {})}\n\n"
            "Ushbu raqamlar asosida xolis tahlil ber va keyingi hafta intizomini yaxshilash uchun 3 ta aniq amaliy tavsiya yoz."
        )
        return await self.generate_response(prompt)

    def _fallback_response(self, prompt: str) -> str:
        """Deterministic rule-based response when AI provider is unreachable."""
        return (
            "Asadbek, intizom — bu hissiyot emas, har kungi qat'iy reja va natija. "
            "Kechikkan vazifani ortga surmay, hoziroq 10 daqiqalik birinchi qadamini boshlang yoki aniq vaqt belgilang."
        )
