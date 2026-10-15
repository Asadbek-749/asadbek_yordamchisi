"""Service layer encapsulating discipline business logic."""
from __future__ import annotations

import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from discipline.coach import DisciplineCoach
from discipline.db import get_session
from discipline.models import (
    AccountabilityLog,
    DisciplineHabit,
    DisciplineSettings,
    DisciplineTask,
    FocusSession,
    HabitLog,
)
from discipline.repository import DisciplineRepository
from discipline.schemas import DailyMetrics, TaskCreate, TaskUpdate


class DisciplineService:
    """High-level service for tasks, plans, habits, focus sessions, and AI coaching."""

    def __init__(self, coach: DisciplineCoach) -> None:
        self.coach = coach

    # ==================== SETTINGS ====================
    async def get_settings(self, user_id: int) -> DisciplineSettings:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.get_or_create_settings(user_id)

    async def update_settings(self, user_id: int, **kwargs) -> DisciplineSettings:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.update_settings(user_id, **kwargs)

    async def set_reminders_enabled(self, user_id: int, enabled: bool) -> DisciplineSettings:
        return await self.update_settings(user_id, reminders_enabled=enabled)

    # ==================== TASKS ====================
    async def create_task(self, user_id: int, task_in: TaskCreate) -> DisciplineTask:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.create_task(user_id, task_in)

    async def get_task(self, task_id: int, user_id: int) -> Optional[DisciplineTask]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.get_task(task_id, user_id)

    async def list_today_tasks(
        self, user_id: int, date: Optional[datetime.date] = None
    ) -> Sequence[DisciplineTask]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            target_date = date or datetime.datetime.now(tz).date()
            return await repo.list_tasks(user_id, date=target_date, tz_name=settings.timezone)

    async def list_active_tasks(self, user_id: int) -> Sequence[DisciplineTask]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.list_active_tasks(user_id)

    async def complete_task(self, task_id: int, user_id: int) -> Optional[DisciplineTask]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.mark_task_completed(task_id, user_id)

    async def update_task(self, task_id: int, user_id: int, task_up: TaskUpdate) -> Optional[DisciplineTask]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.update_task(task_id, user_id, task_up)

    # ==================== ACCOUNTABILITY & DELAYS ====================
    async def record_task_delay(
        self,
        user_id: int,
        task_id: int,
        reason_category: str,
        reason_details: Optional[str] = None,
        action_taken: str = "rescheduled",
    ) -> tuple[AccountabilityLog, str]:
        """Record delay reason and query AI coach for firm practical guidance."""
        async with get_session() as session:
            repo = DisciplineRepository(session)
            task = await repo.get_task(task_id, user_id)
            if not task:
                raise ValueError("Vazifa topilmadi yoki sizga tegishli emas.")

            recent = await repo.get_recent_accountability_logs(user_id, limit=3)
            previous_reasons = [r.reason_category for r in recent]

            # Generate AI coach advice
            ai_advice = await self.coach.coach_overdue_task(
                task=task,
                reason_category=reason_category,
                reason_details=reason_details,
                previous_reasons=previous_reasons,
            )

            log = await repo.record_accountability(
                user_id=user_id,
                task_id=task_id,
                reason_category=reason_category,
                reason_details=reason_details,
                action_taken=action_taken,
                ai_feedback=ai_advice,
            )
            return log, ai_advice

    # ==================== HABITS ====================
    async def create_habit(
        self,
        user_id: int,
        name: str,
        goal: Optional[str] = None,
        frequency: str = "daily",
        target_count: int = 1,
        reminder_time: Optional[str] = None,
    ) -> DisciplineHabit:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.create_habit(
                user_id=user_id,
                name=name,
                goal=goal,
                frequency=frequency,
                target_count=target_count,
                reminder_time=reminder_time,
            )

    async def list_habits(self, user_id: int) -> Sequence[DisciplineHabit]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.list_habits(user_id, active_only=True)

    async def toggle_habit_today(
        self, habit_id: int, user_id: int
    ) -> tuple[HabitLog, DisciplineHabit]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            today = datetime.datetime.now(tz).date()

            # Check existing
            logs = await repo.get_habit_logs_for_date(user_id, today)
            is_completed = any(log.habit_id == habit_id and log.completed for log in logs)
            new_status = not is_completed

            return await repo.log_habit_completion(
                habit_id=habit_id,
                user_id=user_id,
                log_date=today,
                completed=new_status,
            )

    async def get_today_habit_status(self, user_id: int) -> list[dict]:
        """Return habits joined with today's completion boolean."""
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            today = datetime.datetime.now(tz).date()

            habits = await repo.list_habits(user_id, active_only=True)
            logs = await repo.get_habit_logs_for_date(user_id, today)
            completed_ids = {l.habit_id for l in logs if l.completed}

            res = []
            for h in habits:
                res.append({
                    "id": h.id,
                    "name": h.name,
                    "goal": h.goal,
                    "streak": h.current_streak,
                    "completed_today": h.id in completed_ids,
                })
            return res

    # ==================== FOCUS SESSIONS ====================
    async def start_focus(
        self, user_id: int, duration_minutes: int, task_id: Optional[int] = None
    ) -> FocusSession:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.start_focus_session(user_id, duration_minutes, task_id)

    async def get_active_focus(self, user_id: int) -> Optional[FocusSession]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.get_active_focus_session(user_id)

    async def finish_focus(self, user_id: int, notes: Optional[str] = None) -> Optional[FocusSession]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.finish_focus_session(user_id, notes=notes)

    async def cancel_focus(self, user_id: int) -> Optional[FocusSession]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            return await repo.cancel_focus_session(user_id)

    # ==================== REPORTS ====================
    async def get_daily_metrics(
        self, user_id: int, target_date: Optional[datetime.date] = None
    ) -> DailyMetrics:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            dt = target_date or datetime.datetime.now(tz).date()
            return await repo.compile_daily_metrics(user_id, dt, settings.timezone)

    async def generate_and_save_daily_report(
        self, user_id: int, target_date: Optional[datetime.date] = None
    ) -> tuple[DailyMetrics, str]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            dt = target_date or datetime.datetime.now(tz).date()

            metrics = await repo.compile_daily_metrics(user_id, dt, settings.timezone)
            tasks = await repo.list_tasks(user_id, date=dt, tz_name=settings.timezone)
            completed_tasks = [t for t in tasks if t.status == "completed"]
            missed_tasks = [t for t in tasks if t.status == "missed"]

            habits_summary = f"{metrics.habits_completed}/{metrics.habits_total} ta bajarildi"

            ai_review = await self.coach.generate_daily_review(
                completed_tasks=completed_tasks,
                missed_tasks=missed_tasks,
                focus_minutes=metrics.focus_minutes,
                habits_summary=habits_summary,
            )

            await repo.save_daily_report(user_id, metrics, ai_review)
            return metrics, ai_review

    async def generate_weekly_report(self, user_id: int) -> tuple[dict, str]:
        async with get_session() as session:
            repo = DisciplineRepository(session)
            settings = await repo.get_or_create_settings(user_id)
            tz = ZoneInfo(settings.timezone)
            today = datetime.datetime.now(tz).date()
            start_date = today - datetime.timedelta(days=6)

            stats = await repo.get_weekly_metrics(user_id, start_date, today, settings.timezone)
            ai_review = await self.coach.generate_weekly_review(stats)
            return stats, ai_review
