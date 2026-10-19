"""Database repository layer with strict user ownership enforcement."""
from __future__ import annotations

import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from discipline.models import (
    AccountabilityLog,
    DailyPlan,
    DailyReport,
    DisciplineHabit,
    DisciplineSettings,
    DisciplineTask,
    FocusSession,
    HabitLog,
    ReminderDelivery,
    utcnow,
)
from discipline.schemas import DailyMetrics, TaskCreate, TaskUpdate


class DisciplineRepository:
    """Encapsulates all database operations for Discipline system."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================== SETTINGS ====================
    async def get_or_create_settings(self, user_id: int) -> DisciplineSettings:
        stmt = select(DisciplineSettings).where(DisciplineSettings.user_id == user_id)
        result = await self.session.execute(stmt)
        settings = result.scalar_one_or_none()
        if not settings:
            settings = DisciplineSettings(user_id=user_id)
            self.session.add(settings)
            await self.session.flush()
        return settings

    async def update_settings(self, user_id: int, **kwargs) -> DisciplineSettings:
        settings = await self.get_or_create_settings(user_id)
        for k, v in kwargs.items():
            if hasattr(settings, k) and v is not None:
                setattr(settings, k, v)
        await self.session.flush()
        return settings

    # ==================== DAILY PLANS ====================
    async def get_daily_plan(self, user_id: int, plan_date: datetime.date) -> Optional[DailyPlan]:
        stmt = select(DailyPlan).where(
            and_(DailyPlan.user_id == user_id, DailyPlan.plan_date == plan_date)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_daily_plan(
        self, user_id: int, plan_date: datetime.date, top_three: Optional[str] = None
    ) -> DailyPlan:
        plan = await self.get_or_create_daily_plan(user_id, plan_date)
        if top_three is not None:
            plan.top_three_tasks = top_three
            plan.is_confirmed = True
        await self.session.flush()
        return plan

    async def get_or_create_daily_plan(self, user_id: int, plan_date: datetime.date) -> DailyPlan:
        stmt = select(DailyPlan).where(
            and_(DailyPlan.user_id == user_id, DailyPlan.plan_date == plan_date)
        )
        result = await self.session.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            plan = DailyPlan(user_id=user_id, plan_date=plan_date)
            self.session.add(plan)
            await self.session.flush()
        return plan

    async def update_daily_plan(
        self, user_id: int, plan_date: datetime.date, top_three: Optional[str] = None, notes: Optional[str] = None, confirmed: Optional[bool] = None
    ) -> DailyPlan:
        plan = await self.get_or_create_daily_plan(user_id, plan_date)
        if top_three is not None:
            plan.top_three_tasks = top_three
        if notes is not None:
            plan.notes = notes
        if confirmed is not None:
            plan.is_confirmed = confirmed
        await self.session.flush()
        return plan

    # ==================== TASKS ====================
    async def create_task(self, user_id: int, task_in: TaskCreate) -> DisciplineTask:
        task = DisciplineTask(
            user_id=user_id,
            title=task_in.title,
            expected_result=task_in.expected_result,
            priority=task_in.priority,
            category=task_in.category,
            estimated_minutes=task_in.estimated_minutes,
            start_time=task_in.start_time,
            deadline=task_in.deadline,
            status="planned",
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task(self, task_id: int, user_id: int) -> Optional[DisciplineTask]:
        """Fetch task enforcing strict user ownership."""
        stmt = select(DisciplineTask).where(
            and_(DisciplineTask.id == task_id, DisciplineTask.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_tasks(
        self,
        user_id: int,
        status: Optional[str] = None,
        date: Optional[datetime.date] = None,
        tz_name: str = "Asia/Tashkent",
    ) -> Sequence[DisciplineTask]:
        """List tasks with optional filtering by status and local date."""
        query = select(DisciplineTask).where(DisciplineTask.user_id == user_id)
        if status:
            query = query.where(DisciplineTask.status == status)

        if date:
            tz = ZoneInfo(tz_name)
            # Local day start and end in UTC
            day_start = datetime.datetime.combine(date, datetime.time.min, tzinfo=tz).astimezone(datetime.timezone.utc)
            day_end = datetime.datetime.combine(date, datetime.time.max, tzinfo=tz).astimezone(datetime.timezone.utc)

            # Task matches if deadline is today OR start_time is today OR created today
            query = query.where(
                (
                    (DisciplineTask.deadline >= day_start) & (DisciplineTask.deadline <= day_end)
                ) | (
                    (DisciplineTask.start_time >= day_start) & (DisciplineTask.start_time <= day_end)
                ) | (
                    (DisciplineTask.deadline == None) & (DisciplineTask.created_at >= day_start) & (DisciplineTask.created_at <= day_end)
                )
            )

        query = query.order_by(
            DisciplineTask.status == "completed",  # unfinished first
            DisciplineTask.priority == "high",
            DisciplineTask.priority == "medium",
            DisciplineTask.deadline.asc().nulls_last(),
            DisciplineTask.id.desc(),
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_tasks_for_date(self, user_id: int, date: datetime.date) -> Sequence[DisciplineTask]:
        """Fetch all tasks for a specific date."""
        return await self.list_tasks(user_id=user_id, date=date)

    async def list_active_tasks(self, user_id: int) -> Sequence[DisciplineTask]:
        """List all uncompleted tasks for user."""
        stmt = (
            select(DisciplineTask)
            .where(
                and_(
                    DisciplineTask.user_id == user_id,
                    DisciplineTask.status.in_(["planned", "in_progress", "missed"]),
                )
            )
            .order_by(
                DisciplineTask.priority == "high",
                DisciplineTask.deadline.asc().nulls_last(),
                DisciplineTask.id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_task(self, task_id: int, user_id: int, task_up: TaskUpdate) -> Optional[DisciplineTask]:
        task = await self.get_task(task_id, user_id)
        if not task:
            return None

        update_data = task_up.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(task, k, v)
        if task_up.status == "completed" and not task.completed_at:
            task.completed_at = utcnow()
        await self.session.flush()
        return task

    async def mark_task_completed(self, task_id: int, user_id: int) -> Optional[DisciplineTask]:
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        task.status = "completed"
        task.completed_at = utcnow()
        await self.session.flush()
        return task

    async def mark_overdue_tasks(self, user_id: int, current_time_utc: datetime.datetime) -> Sequence[DisciplineTask]:
        """Find planned/in_progress tasks whose deadline has passed and flag them as missed."""
        stmt = select(DisciplineTask).where(
            and_(
                DisciplineTask.user_id == user_id,
                DisciplineTask.status.in_(["planned", "in_progress"]),
                DisciplineTask.deadline != None,
                DisciplineTask.deadline < current_time_utc,
            )
        )
        res = await self.session.execute(stmt)
        overdue = res.scalars().all()
        for task in overdue:
            task.status = "missed"
        await self.session.flush()
        return overdue

    async def get_tasks_with_upcoming_deadline(
        self, window_start_utc: datetime.datetime, window_end_utc: datetime.datetime
    ) -> Sequence[DisciplineTask]:
        """Find tasks approaching deadline across active users."""
        stmt = select(DisciplineTask).where(
            and_(
                DisciplineTask.status.in_(["planned", "in_progress"]),
                DisciplineTask.deadline >= window_start_utc,
                DisciplineTask.deadline <= window_end_utc,
            )
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

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
        habit = DisciplineHabit(
            user_id=user_id,
            name=name,
            goal=goal,
            frequency=frequency,
            target_count=target_count,
            reminder_time=reminder_time,
            current_streak=0,
            best_streak=0,
            is_active=True,
        )
        self.session.add(habit)
        await self.session.flush()
        return habit

    async def get_habit(self, habit_id: int, user_id: int) -> Optional[DisciplineHabit]:
        stmt = select(DisciplineHabit).where(
            and_(DisciplineHabit.id == habit_id, DisciplineHabit.user_id == user_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_habits(self, user_id: int, active_only: bool = True) -> Sequence[DisciplineHabit]:
        query = select(DisciplineHabit).where(DisciplineHabit.user_id == user_id)
        if active_only:
            query = query.where(DisciplineHabit.is_active == True)
        query = query.order_by(DisciplineHabit.id.asc())
        res = await self.session.execute(query)
        return res.scalars().all()

    async def log_habit_completion(
        self, habit_id: int, user_id: int, log_date: datetime.date, completed: bool = True, notes: Optional[str] = None
    ) -> tuple[HabitLog, DisciplineHabit]:
        habit = await self.get_habit(habit_id, user_id)
        if not habit:
            raise ValueError(f"Habit {habit_id} not found for user {user_id}")

        stmt = select(HabitLog).where(
            and_(HabitLog.habit_id == habit_id, HabitLog.log_date == log_date)
        )
        res = await self.session.execute(stmt)
        log = res.scalar_one_or_none()

        if log:
            log.completed = completed
            if notes is not None:
                log.notes = notes
        else:
            log = HabitLog(
                habit_id=habit_id,
                user_id=user_id,
                log_date=log_date,
                completed=completed,
                notes=notes,
            )
            self.session.add(log)

        # Update streak
        if completed:
            habit.current_streak += 1
            if habit.current_streak > habit.best_streak:
                habit.best_streak = habit.current_streak
        await self.session.flush()
        return log, habit

    async def get_habit_logs_for_date(self, user_id: int, log_date: datetime.date) -> Sequence[HabitLog]:
        stmt = select(HabitLog).where(
            and_(HabitLog.user_id == user_id, HabitLog.log_date == log_date)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    # ==================== FOCUS SESSIONS ====================
    async def get_active_focus_session(self, user_id: int) -> Optional[FocusSession]:
        stmt = (
            select(FocusSession)
            .where(
                and_(
                    FocusSession.user_id == user_id,
                    FocusSession.status == "active",
                )
            )
            .order_by(FocusSession.id.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def start_focus_session(
        self, user_id: int, duration_minutes: int, task_id: Optional[int] = None
    ) -> FocusSession:
        # Check and cancel any dangling previous active session
        existing = await self.get_active_focus_session(user_id)
        if existing:
            existing.status = "cancelled"
            existing.actual_end_at = utcnow()

        now = utcnow()
        target_end = now + datetime.timedelta(minutes=duration_minutes)
        session = FocusSession(
            user_id=user_id,
            task_id=task_id,
            duration_minutes=duration_minutes,
            started_at=now,
            target_end_at=target_end,
            status="active",
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def finish_focus_session(
        self, user_id: int, session_id: Optional[int] = None, notes: Optional[str] = None
    ) -> Optional[FocusSession]:
        if session_id:
            stmt = select(FocusSession).where(
                and_(FocusSession.id == session_id, FocusSession.user_id == user_id)
            )
            res = await self.session.execute(stmt)
            session = res.scalar_one_or_none()
        else:
            session = await self.get_active_focus_session(user_id)

        if not session:
            return None

        session.status = "completed"
        session.actual_end_at = utcnow()
        if notes:
            session.notes = notes
        await self.session.flush()
        return session

    async def cancel_focus_session(self, user_id: int) -> Optional[FocusSession]:
        session = await self.get_active_focus_session(user_id)
        if not session:
            return None
        session.status = "cancelled"
        session.actual_end_at = utcnow()
        await self.session.flush()
        return session

    async def get_due_focus_sessions(self, current_time_utc: datetime.datetime) -> Sequence[FocusSession]:
        """Find active focus sessions whose target_end_at has passed."""
        stmt = select(FocusSession).where(
            and_(
                FocusSession.status == "active",
                FocusSession.target_end_at <= current_time_utc,
            )
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    # ==================== ACCOUNTABILITY LOGS ====================
    async def record_accountability(
        self,
        user_id: int,
        task_id: int,
        reason_category: str,
        reason_details: Optional[str] = None,
        action_taken: str = "rescheduled",
        ai_feedback: Optional[str] = None,
    ) -> AccountabilityLog:
        # Verify ownership
        task = await self.get_task(task_id, user_id)
        if not task:
            raise ValueError(f"Task {task_id} not found for user {user_id}")

        log = AccountabilityLog(
            user_id=user_id,
            task_id=task_id,
            reason_category=reason_category,
            reason_details=reason_details,
            action_taken=action_taken,
            ai_feedback=ai_feedback,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_recent_accountability_logs(self, user_id: int, limit: int = 5) -> Sequence[AccountabilityLog]:
        stmt = (
            select(AccountabilityLog)
            .where(AccountabilityLog.user_id == user_id)
            .order_by(AccountabilityLog.created_at.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    # ==================== IDEMPOTENT REMINDER DELIVERIES ====================
    async def is_reminder_delivered(self, idempotency_key: str) -> bool:
        stmt = select(ReminderDelivery).where(ReminderDelivery.idempotency_key == idempotency_key)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def record_reminder_delivered(
        self, idempotency_key: str, user_id: int, reminder_type: str
    ) -> ReminderDelivery:
        delivery = ReminderDelivery(
            idempotency_key=idempotency_key,
            user_id=user_id,
            reminder_type=reminder_type,
        )
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    # ==================== METRICS & REPORTS ====================
    async def compile_daily_metrics(
        self, user_id: int, target_date: datetime.date, tz_name: str = "Asia/Tashkent"
    ) -> DailyMetrics:
        tz = ZoneInfo(tz_name)
        day_start = datetime.datetime.combine(target_date, datetime.time.min, tzinfo=tz).astimezone(datetime.timezone.utc)
        day_end = datetime.datetime.combine(target_date, datetime.time.max, tzinfo=tz).astimezone(datetime.timezone.utc)

        # 1. Tasks
        tasks = await self.list_tasks(user_id, date=target_date, tz_name=tz_name)
        planned_count = len(tasks)
        completed_count = sum(1 for t in tasks if t.status == "completed")
        missed_count = sum(1 for t in tasks if t.status == "missed")

        # 2. Focus
        focus_stmt = select(func.sum(FocusSession.duration_minutes)).where(
            and_(
                FocusSession.user_id == user_id,
                FocusSession.status == "completed",
                FocusSession.started_at >= day_start,
                FocusSession.started_at <= day_end,
            )
        )
        focus_res = await self.session.execute(focus_stmt)
        focus_minutes = focus_res.scalar() or 0

        # 3. Habits
        habits = await self.list_habits(user_id, active_only=True)
        habits_total = len(habits)
        habit_logs = await self.get_habit_logs_for_date(user_id, target_date)
        habits_completed = sum(1 for log in habit_logs if log.completed)

        rate = (completed_count / planned_count * 100) if planned_count > 0 else 0.0

        return DailyMetrics(
            report_date=target_date,
            planned_count=planned_count,
            completed_count=completed_count,
            missed_count=missed_count,
            focus_minutes=int(focus_minutes),
            habits_completed=habits_completed,
            habits_total=habits_total,
            completion_rate_pct=round(rate, 1),
        )

    async def save_daily_report(
        self, user_id: int, metrics: DailyMetrics, ai_review: Optional[str] = None
    ) -> DailyReport:
        stmt = select(DailyReport).where(
            and_(DailyReport.user_id == user_id, DailyReport.report_date == metrics.report_date)
        )
        res = await self.session.execute(stmt)
        report = res.scalar_one_or_none()

        if not report:
            report = DailyReport(
                user_id=user_id,
                report_date=metrics.report_date,
                planned_count=metrics.planned_count,
                completed_count=metrics.completed_count,
                missed_count=metrics.missed_count,
                focus_minutes=metrics.focus_minutes,
                habits_completed=metrics.habits_completed,
                habits_total=metrics.habits_total,
                ai_review=ai_review,
            )
            self.session.add(report)
        else:
            report.planned_count = metrics.planned_count
            report.completed_count = metrics.completed_count
            report.missed_count = metrics.missed_count
            report.focus_minutes = metrics.focus_minutes
            report.habits_completed = metrics.habits_completed
            report.habits_total = metrics.habits_total
            if ai_review:
                report.ai_review = ai_review

        await self.session.flush()
        return report

    async def get_weekly_metrics(
        self, user_id: int, start_date: datetime.date, end_date: datetime.date, tz_name: str = "Asia/Tashkent"
    ) -> dict:
        """Aggregate stats over a week."""
        total_planned = 0
        total_completed = 0
        total_missed = 0
        total_focus = 0

        curr = start_date
        while curr <= end_date:
            m = await self.compile_daily_metrics(user_id, curr, tz_name)
            total_planned += m.planned_count
            total_completed += m.completed_count
            total_missed += m.missed_count
            total_focus += m.focus_minutes
            curr += datetime.timedelta(days=1)

        rate = (total_completed / total_planned * 100) if total_planned > 0 else 0.0

        # Frequency of delay reasons
        reason_stmt = (
            select(AccountabilityLog.reason_category, func.count(AccountabilityLog.id))
            .where(
                and_(
                    AccountabilityLog.user_id == user_id,
                )
            )
            .group_by(AccountabilityLog.reason_category)
        )
        reason_res = await self.session.execute(reason_stmt)
        reasons_breakdown = dict(reason_res.all())

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_planned": total_planned,
            "total_completed": total_completed,
            "total_missed": total_missed,
            "total_focus_minutes": total_focus,
            "completion_rate_pct": round(rate, 1),
            "reasons_breakdown": reasons_breakdown,
        }
