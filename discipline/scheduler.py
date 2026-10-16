"""Scheduler and automated reminder dispatching with idempotency protection."""
from __future__ import annotations

import datetime
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from discipline.db import get_session
from discipline.models import utcnow
from discipline.repository import DisciplineRepository
from discipline.service import DisciplineService

logger = logging.getLogger(__name__)


class DisciplineScheduler:
    """Manages scheduled morning plans, evening accountability, deadline alerts, and focus timer alerts."""

    def __init__(self, bot: Bot, service: DisciplineService, owner_id: int) -> None:
        self.bot = bot
        self.service = service
        self.owner_id = owner_id
        self.scheduler = AsyncIOScheduler()
        self._is_running = False

    def start(self) -> None:
        """Configure jobs and start the scheduler."""
        if self._is_running:
            return

        # 1. Periodic tick every 60 seconds (deadlines, focus timer completion, daily reminder trigger checks)
        self.scheduler.add_job(
            self.tick_check,
            "interval",
            seconds=60,
            id="discipline_tick",
            replace_existing=True,
        )

        self.scheduler.start()
        self._is_running = True
        logger.info("DisciplineScheduler started (owner_id=%d).", self.owner_id)

    def stop(self) -> None:
        """Shutdown the scheduler."""
        if self._is_running:
            self.scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("DisciplineScheduler stopped.")

    async def tick_check(self) -> None:
        """Master periodic check running every minute."""
        now_utc = utcnow()
        try:
            await self._check_focus_sessions(now_utc)
            await self._check_upcoming_deadlines(now_utc)
            await self._check_morning_and_evening_schedules(now_utc)
        except Exception as e:
            logger.error("Error in DisciplineScheduler.tick_check: %s", e, exc_info=True)

    async def _check_focus_sessions(self, now_utc: datetime.datetime) -> None:
        """Alert user when active focus session has finished."""
        async with get_session() as session:
            repo = DisciplineRepository(session)
            due_sessions = await repo.get_due_focus_sessions(now_utc)
            for fs in due_sessions:
                key = f"focus_end_{fs.id}"
                if await repo.is_reminder_delivered(key):
                    continue

                fs.status = "completed"
                fs.actual_end_at = now_utc

                await repo.record_reminder_delivered(key, fs.user_id, "focus_end")

                # Send Telegram alert
                try:
                    await self.bot.send_message(
                        fs.user_id,
                        f"🔔 <b>Fokus sessiyasi yakunlandi!</b> ({fs.duration_minutes} daqiqa)\n\n"
                        f"Zo'r natija! Qisqa dam oling yoki natijani qayd eting. "
                        f"Yangi sessiya boshlash uchun /focus ni bosing.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to send focus completion alert to %d: %s", fs.user_id, e)

    async def _check_upcoming_deadlines(self, now_utc: datetime.datetime) -> None:
        """Alert user 30 minutes before task deadline."""
        window_start = now_utc
        window_end = now_utc + datetime.timedelta(minutes=30)

        async with get_session() as session:
            repo = DisciplineRepository(session)
            upcoming = await repo.get_tasks_with_upcoming_deadline(window_start, window_end)
            for task in upcoming:
                key = f"deadline_30m_{task.id}"
                if await repo.is_reminder_delivered(key):
                    continue

                await repo.record_reminder_delivered(key, task.user_id, "task_deadline")

                try:
                    await self.bot.send_message(
                        task.user_id,
                        f"⚠️ <b>Vazifa muddati yaqinlashmoqda!</b>\n\n"
                        f"📌 <b>{task.title}</b>\n"
                        f"⏳ Muddat: 30 daqiqadan kam vaqt qoldi.\n"
                        f"Vazifani tugatgan bo'lsangiz /done yoki /today orqali belgilang.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to send deadline reminder to %d: %s", task.user_id, e)

            # Also flag overdue tasks
            await repo.mark_overdue_tasks(self.owner_id, now_utc)

    async def _check_morning_and_evening_schedules(self, now_utc: datetime.datetime) -> None:
        """Trigger morning plan and evening report according to user's local timezone."""
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(self.owner_id)

            if not settings.reminders_enabled:
                return

            tz = ZoneInfo(settings.timezone)
            local_now = now_utc.astimezone(tz)
            local_time_str = local_now.strftime("%H:%M")
            local_date_str = local_now.strftime("%Y-%m-%d")

            # 1. Morning reminder (e.g. 07:00)
            if local_time_str == settings.morning_reminder_time:
                key = f"morning_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(key):
                    await repo.record_reminder_delivered(key, self.owner_id, "morning_plan")
                    await self._send_morning_plan(local_now.date())

            # 2. Evening report (e.g. 21:00)
            if local_time_str == settings.evening_report_time:
                key = f"evening_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(key):
                    await repo.record_reminder_delivered(key, self.owner_id, "evening_report")
                    await self._send_evening_review(local_now.date())

    async def _send_morning_plan(self, today: datetime.date) -> None:
        """Compose and deliver daily morning planning message."""
        tasks = await self.service.list_today_tasks(self.owner_id, today)
        active_count = len([t for t in tasks if t.status != "completed"])

        text = (
            f"☀️ <b>Xayrli tong, Asadbek!</b>\n\n"
            f"Bugun yangi kun — intizom va natijalar kuni.\n"
            f"📅 Bugungi rejangizda: <b>{active_count} ta faol vazifa</b> bor.\n\n"
            f"💡 <i>Eslatma:</i> 08:00–18:00 oralig'ida ish va darslar. "
            f"Kechki 20:40 dan keyin asosiy fokus bloklari uchun qulay vaqt.\n\n"
            f"Rejani ko'rish va yangi vazifa qo'shish uchun: /today yoki /discipline\n\n"
            f"🤖 <i>Nargis</i>"
        )
        try:
            await self.bot.send_message(self.owner_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error("Failed to send morning plan message: %s", e)

    async def _send_evening_review(self, today: datetime.date) -> None:
        """Compose and deliver evening accountability review."""
        metrics, ai_review = await self.service.generate_and_save_daily_report(self.owner_id, today)

        text = (
            f"🌙 <b>Kechki intizom va javobgarlik hisoboti</b>\n\n"
            f"📊 <b>Bugungi ko'rsatkichlar:</b>\n"
            f"• Rejalashtirilgan: {metrics.planned_count}\n"
            f"• Bajarilgan: ✅ {metrics.completed_count} ({metrics.completion_rate_pct}%)\n"
            f"• Bajarilmagan/kechikkan: ⚠️ {metrics.missed_count}\n"
            f"• Fokus vaqti: ⏳ {metrics.focus_minutes} daqiqa\n"
            f"• Odatlar: 🔥 {metrics.habits_completed}/{metrics.habits_total}\n\n"
            f"🤖 <b>Nargis fikri:</b>\n"
            f"{ai_review}\n\n"
            f"Qolib ketgan vazifalar sababini qayd etish uchun /today ni bosing."
        )
        try:
            await self.bot.send_message(self.owner_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error("Failed to send evening review message: %s", e)
