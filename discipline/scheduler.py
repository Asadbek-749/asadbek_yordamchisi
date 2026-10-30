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

CATCH_UP_WINDOW_MINUTES = 90


def is_reminder_due(
    local_now: datetime.datetime, hhmm: str, window_minutes: int = CATCH_UP_WINDOW_MINUTES
) -> bool:
    """True when local_now is within [hhmm, hhmm + window) on the same local day.

    The window lets a reminder still go out if the bot was restarting at the exact minute;
    the idempotency key prevents sending it twice.
    """
    try:
        hour, minute = (int(p) for p in hhmm.strip().split(":"))
        target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, AttributeError):
        return False
    delta = (local_now - target).total_seconds()
    return 0 <= delta < window_minutes * 60


class DisciplineScheduler:
    """Manages scheduled morning plans, evening accountability, deadline alerts, and focus timer alerts."""

    def __init__(self, bot: Bot, service: DisciplineService, owner_id: int) -> None:
        self.bot = bot
        self.service = service
        self.owner_id = owner_id
        self.scheduler = AsyncIOScheduler()
        self._is_running = False
        self.alerter = None  # optional OwnerAlerter, set by bot.py
        try:
            from nargis.proactive import ProactiveEngine
            self.proactive_engine = ProactiveEngine(owner_id, bot)
        except Exception:
            self.proactive_engine = None

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
            if self.proactive_engine:
                await self.proactive_engine.execute_minute_tick(now_utc)
            await self._check_focus_sessions(now_utc)
            await self._check_upcoming_deadlines(now_utc)
            await self._check_morning_and_evening_schedules(now_utc)
        except Exception as e:
            logger.error("Error in DisciplineScheduler.tick_check: %s", e, exc_info=True)
            if self.alerter is not None:
                self.alerter.alert_nowait("scheduler", type(e).__name__)

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
            local_date_str = local_now.strftime("%Y-%m-%d")

            # 1. Morning reminder (e.g. 07:00)
            if is_reminder_due(local_now, settings.morning_reminder_time):
                key = f"morning_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(key):
                    await repo.record_reminder_delivered(key, self.owner_id, "morning_plan")
                    await self._send_morning_plan(local_now.date())

            # 2. Evening report and finance close (e.g. 21:00)
            if is_reminder_due(local_now, settings.evening_report_time):
                key = f"evening_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(key):
                    await repo.record_reminder_delivered(key, self.owner_id, "evening_report")
                    await self._send_evening_review(local_now.date())
                    await self._send_evening_finance_check(local_now.date())

            # 3. Weekly finance & discipline report on Sunday at 20:00
            if local_now.weekday() == 6 and is_reminder_due(local_now, "20:00"):
                w_key = f"weekly_report_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(w_key):
                    await repo.record_reminder_delivered(w_key, self.owner_id, "weekly_report")
                    await self._send_weekly_master_report(local_now.date())

            # 3b. Monthly recurring payments (rent, internet...): reminder at 09:00 with a one-tap button
            if is_reminder_due(local_now, "09:00"):
                await self._send_recurring_reminders(repo, local_now.date())

            # 3c. Nightly database backup to the owner (SQLite only), 03:00 local with catch-up
            if is_reminder_due(local_now, "03:00"):
                b_key = f"backup_{self.owner_id}_{local_date_str}"
                if not await repo.is_reminder_delivered(b_key):
                    await repo.record_reminder_delivered(b_key, self.owner_id, "nightly_backup")
                    await self._send_nightly_backup(local_now.date())

            # 4. Check monthly budget alerts periodically
            period_key = local_now.strftime("%Y-%m")
            try:
                from finance.service import FinanceService
                fin_svc = FinanceService()
                alerts = await fin_svc.check_budget_alerts(self.owner_id, period_key)
                for alert in alerts:
                    await self.bot.send_message(self.owner_id, alert, parse_mode="HTML")
            except Exception as _b_err:
                logger.warning("Budget alert check error: %s", _b_err)

    async def _send_nightly_backup(self, today: datetime.date) -> None:
        try:
            from discipline.db import get_database_url
            from notification.backup import send_backup
            await send_backup(self.bot, self.owner_id, get_database_url(), today)
        except Exception as e:
            logger.error("Nightly backup failed: %s", type(e).__name__)
            if self.alerter is not None:
                self.alerter.alert_nowait("scheduler", f"zaxira nusxa: {type(e).__name__}")

    async def _send_recurring_reminders(self, repo: DisciplineRepository, today: datetime.date) -> None:
        """Remind about recurring payments due today; the idempotency key makes this once per month."""
        try:
            from finance.keyboards import recurring_reminder_keyboard
            from finance.service import FinanceService
            fin_svc = FinanceService()
            for rec in await fin_svc.recurring_due_on(self.owner_id, today):
                key = f"recurring_reminder_{rec.id}_{today.strftime('%Y-%m')}"
                if await repo.is_reminder_delivered(key):
                    continue
                await repo.record_reminder_delivered(key, self.owner_id, "recurring_payment")
                await self.bot.send_message(
                    self.owner_id,
                    f"🔁 <b>Doimiy to'lov kuni</b>\n\n"
                    f"📌 {rec.title} — <b>{fin_svc.format_money(rec.amount, rec.currency)}</b>\n"
                    f"Xarajatga qo'shaymi?",
                    parse_mode="HTML",
                    reply_markup=recurring_reminder_keyboard(rec.id),
                )
        except Exception as e:
            logger.warning("Recurring payment reminder error: %s", e)

    async def _send_evening_finance_check(self, today: datetime.date) -> None:
        """Remind owner to record today's income and expenses if not already done."""
        try:
            from finance.service import FinanceService
            from finance.keyboards import main_finance_keyboard
            fin_svc = FinanceService()
            summary = await fin_svc.get_daily_summary(self.owner_id, today)

            pending_count = summary.get("pending_count", 0)
            pending_text = ""
            if pending_count > 0:
                pending_text = f"\n\n⏳ <b>Eslatma:</b> Sizda <b>{pending_count} ta</b> tasdiqlanmagan (Pending) xarajat mavjud. Ularni /pendingfinance orqali tasdiqlang."

            inc = fin_svc.format_money(summary["total_income"])
            exp = fin_svc.format_money(summary["total_expense"])

            text = (
                f"💰 <b>KUN YAKUNI — MOLIYAVIY HISOBOT</b>\n\n"
                f"Bugungi tasdiqlangan holat:\n"
                f"• Daromad: {inc}\n"
                f"• Xarajat: {exp}"
                f"{pending_text}\n\n"
                f"<i>Bugungi barcha daromad va xarajatlaringizni to'liq kiritdingizmi?</i>"
            )
            await self.bot.send_message(self.owner_id, text, parse_mode="HTML", reply_markup=main_finance_keyboard())
        except Exception as e:
            logger.error("Failed to send evening finance check: %s", e)

    async def _send_weekly_master_report(self, today: datetime.date) -> None:
        """Compose and deliver master weekly report combining discipline and finances."""
        try:
            from finance.service import FinanceService
            fin_svc = FinanceService()
            start = today - datetime.timedelta(days=6)
            fin_summary = await fin_svc.get_weekly_summary(self.owner_id, start, today)

            inc_str = fin_svc.format_money(fin_summary["total_income"])
            exp_str = fin_svc.format_money(fin_summary["total_expense"])
            net_str = fin_svc.format_money(fin_summary["net_balance"])
            net_prefix = "+" if fin_summary["net_balance"] >= 0 else ""

            text = (
                f"📊 <b>NARGIS — HAFTALIK MASTER HISOBOT</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>MOLIYA:</b>\n"
                f"• Daromad: {inc_str}\n"
                f"• Xarajat: {exp_str}\n"
                f"• Sof balans: <b>{net_prefix}{net_str}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔥 <b>INTIZOM:</b>\n"
                f"Intizom paneli va haftalik xulosani /week orqali ko'rishingiz mumkin.\n\n"
                f"<i>Barqarorlik va intizom — eng katta yutuq kaliti! 🤖 Nargis</i>"
            )
            await self.bot.send_message(self.owner_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error("Failed to send weekly master report: %s", e)

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
