"""Pydantic schemas for data validation and typing."""
from __future__ import annotations

import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

TaskPriority = Literal["high", "medium", "low"]
TaskCategory = Literal["work", "study", "project", "habit", "other"]
TaskStatus = Literal["planned", "in_progress", "completed", "missed", "cancelled"]
ReasonCategory = Literal[
    "time_shortage",
    "distraction",
    "fatigue",
    "unclear_task",
    "unexpected_event",
    "low_motivation",
    "other",
]
SundayMode = Literal["work", "rest"]


class DisciplineSettingsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    morning_reminder_time: str = "07:00"
    evening_report_time: str = "21:00"
    timezone: str = "Asia/Tashkent"
    reminders_enabled: bool = True
    sunday_mode: SundayMode = "rest"
    work_start: str = "08:00"
    work_end: str = "18:00"
    free_time_start: str = "20:40"


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    expected_result: Optional[str] = None
    priority: TaskPriority = "medium"
    category: TaskCategory = "work"
    estimated_minutes: int = Field(default=30, ge=5, le=720)
    start_time: Optional[datetime.datetime] = None
    deadline: Optional[datetime.datetime] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    expected_result: Optional[str] = None
    priority: Optional[TaskPriority] = None
    category: Optional[TaskCategory] = None
    estimated_minutes: Optional[int] = None
    start_time: Optional[datetime.datetime] = None
    deadline: Optional[datetime.datetime] = None
    status: Optional[TaskStatus] = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    expected_result: Optional[str] = None
    priority: str
    category: str
    estimated_minutes: int
    start_time: Optional[datetime.datetime] = None
    deadline: Optional[datetime.datetime] = None
    status: str
    completed_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class HabitCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    goal: Optional[str] = None
    frequency: str = "daily"
    target_count: int = 1
    reminder_time: Optional[str] = None


class HabitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    goal: Optional[str] = None
    frequency: str
    target_count: int
    reminder_time: Optional[str] = None
    current_streak: int
    best_streak: int
    is_active: bool


class FocusStart(BaseModel):
    duration_minutes: int = Field(default=25, ge=5, le=180)
    task_id: Optional[int] = None


class FocusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: Optional[int] = None
    duration_minutes: int
    started_at: datetime.datetime
    target_end_at: datetime.datetime
    actual_end_at: Optional[datetime.datetime] = None
    status: str


class AccountabilityCreate(BaseModel):
    task_id: int
    reason_category: ReasonCategory
    reason_details: Optional[str] = None
    action_taken: str = "rescheduled"
    ai_feedback: Optional[str] = None


class DailyMetrics(BaseModel):
    report_date: datetime.date
    planned_count: int = 0
    completed_count: int = 0
    missed_count: int = 0
    focus_minutes: int = 0
    habits_completed: int = 0
    habits_total: int = 0
    completion_rate_pct: float = 0.0
