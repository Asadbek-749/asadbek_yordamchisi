"""SQLAlchemy 2.x async models for the Discipline & Productivity System + Nargis AI Agent."""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime.datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.datetime.now(datetime.timezone.utc)


class Base(DeclarativeBase):
    """Base model class."""
    pass


class DisciplineSettings(Base):
    """User discipline settings and schedule configuration."""
    __tablename__ = "discipline_settings"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    morning_reminder_time: Mapped[str] = mapped_column(String(10), default="07:00")
    evening_report_time: Mapped[str] = mapped_column(String(10), default="21:00")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Tashkent")
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sunday_mode: Mapped[str] = mapped_column(String(20), default="rest")  # "work" or "rest"
    work_start: Mapped[str] = mapped_column(String(10), default="08:00")
    work_end: Mapped[str] = mapped_column(String(10), default="18:00")
    free_time_start: Mapped[str] = mapped_column(String(10), default="20:40")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DailyPlan(Base):
    """Daily plan record for a user."""
    __tablename__ = "daily_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    plan_date: Mapped[datetime.date] = mapped_column(Date, index=True, nullable=False)
    top_three_tasks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or text summary
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "plan_date", name="uq_user_plan_date"),
    )


class DisciplineTask(Base):
    """Individual planned or executed task."""
    __tablename__ = "discipline_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium")  # high, medium, low
    category: Mapped[str] = mapped_column(String(30), default="work")   # work, study, project, habit, other
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=30)
    start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="planned", index=True
    )  # planned, in_progress, completed, missed, cancelled
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    accountability_logs: Mapped[list["AccountabilityLog"]] = relationship(
        "AccountabilityLog", back_populates="task", cascade="all, delete-orphan"
    )


class DisciplineHabit(Base):
    """Habit tracked by user."""
    __tablename__ = "discipline_habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    frequency: Mapped[str] = mapped_column(String(50), default="daily")  # daily, weekdays
    target_count: Mapped[int] = mapped_column(Integer, default=1)
    reminder_time: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    logs: Mapped[list["HabitLog"]] = relationship(
        "HabitLog", back_populates="habit", cascade="all, delete-orphan"
    )


class HabitLog(Base):
    """Daily completion log for a habit."""
    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    habit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("discipline_habits.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    log_date: Mapped[datetime.date] = mapped_column(Date, index=True, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    habit: Mapped["DisciplineHabit"] = relationship("DisciplineHabit", back_populates="logs")

    __table_args__ = (
        UniqueConstraint("habit_id", "log_date", name="uq_habit_date"),
    )


class FocusSession(Base):
    """Timer and outcome record for a focus/Pomodoro block."""
    __tablename__ = "focus_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("discipline_tasks.id", ondelete="SET NULL"), nullable=True
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_end_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_end_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", index=True
    )  # active, completed, cancelled, paused
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AccountabilityLog(Base):
    """Reason and resolution record for missed or delayed tasks."""
    __tablename__ = "accountability_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("discipline_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason_category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # time_shortage, distraction, fatigue, unclear_task, unexpected_event, low_motivation, other
    reason_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_taken: Mapped[str] = mapped_column(String(50), default="rescheduled")  # rescheduled, cancelled, split
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped["DisciplineTask"] = relationship("DisciplineTask", back_populates="accountability_logs")


class DailyReport(Base):
    """Compiled end-of-day report metrics."""
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    report_date: Mapped[datetime.date] = mapped_column(Date, index=True, nullable=False)
    planned_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    missed_count: Mapped[int] = mapped_column(Integer, default=0)
    focus_minutes: Mapped[int] = mapped_column(Integer, default=0)
    habits_completed: Mapped[int] = mapped_column(Integer, default=0)
    habits_total: Mapped[int] = mapped_column(Integer, default=0)
    ai_review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "report_date", name="uq_user_report_date"),
    )


class ReminderDelivery(Base):
    """Idempotency record to prevent duplicate reminder deliveries."""
    __tablename__ = "reminder_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    reminder_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # morning_plan, evening_report, task_deadline, focus_end
    delivered_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NARGIS AI AGENT MODELS
# ─────────────────────────────────────────────────────────────────────────────

class NargisContact(Base):
    """Contact / lead record for every unique user who messaged Nargis."""
    __tablename__ = "nargis_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Lead tracking
    lead_status: Mapped[str] = mapped_column(
        String(30), default="NEW", index=True
    )  # NEW, CONTACTED, QUALIFIED, WAITING, CONVERTED, LOST, ARCHIVED

    # Last known intent / reason
    last_intent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    importance_score: Mapped[int] = mapped_column(Integer, default=50)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # CSV

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["NargisMessage"]] = relationship(
        "NargisMessage", back_populates="contact", cascade="all, delete-orphan"
    )


class NargisMessage(Base):
    """Logged incoming message from a user to Nargis."""
    __tablename__ = "nargis_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nargis_contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(
        String(10), default="IN"
    )  # IN (from user), OUT (Nargis reply)

    # Classification
    intent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    urgency: Mapped[str] = mapped_column(String(20), default="NORMAL")
    importance_score: Mapped[int] = mapped_column(Integer, default=50)
    requires_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_hint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(30), default="NEW", index=True
    )  # NEW, VIEWED, IN_PROGRESS, WAITING, REPLIED, RESOLVED, ARCHIVED

    owner_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_generated_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    contact: Mapped["NargisContact"] = relationship("NargisContact", back_populates="messages")


class NargisEscalation(Base):
    """Record of critical/high-importance escalation notifications sent to owner."""
    __tablename__ = "nargis_escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("nargis_messages.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("nargis_contacts.id", ondelete="SET NULL"), nullable=True
    )
    urgency_level: Mapped[str] = mapped_column(String(20), nullable=False)  # HIGH, CRITICAL
    notification_text: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DisciplineScore(Base):
    """Current discipline score for the owner."""
    __tablename__ = "discipline_score"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    score: Mapped[int] = mapped_column(Integer, default=100)
    level: Mapped[str] = mapped_column(
        String(30), default="STRONG"
    )  # ELITE, STRONG, NORMAL, WEAK, CRITICAL, DISCIPLINE_FAILURE
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["DisciplineScoreEvent"]] = relationship(
        "DisciplineScoreEvent", back_populates="score_record", cascade="all, delete-orphan"
    )


class DisciplineScoreEvent(Base):
    """Individual score change event for audit trail."""
    __tablename__ = "discipline_score_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discipline_score.user_id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # TASK_COMPLETED, TASK_MISSED, TASK_POSTPONED, FOCUS_COMPLETED,
       # HABIT_COMPLETED, PLAN_CREATED, PLAN_IGNORED, DISCIPLINE_WARNING, etc.
    score_delta: Mapped[int] = mapped_column(Integer, nullable=False)   # positive or negative
    score_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("discipline_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    score_record: Mapped["DisciplineScore"] = relationship("DisciplineScore", back_populates="events")

