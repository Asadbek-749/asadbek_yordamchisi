"""Aiogram FSM states for Discipline interactive wizards."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TaskCreationForm(StatesGroup):
    waiting_for_title = State()
    waiting_for_expected_result = State()
    waiting_for_priority = State()
    waiting_for_duration = State()
    waiting_for_start_time = State()
    waiting_for_deadline = State()
    waiting_for_category = State()


class HabitCreationForm(StatesGroup):
    waiting_for_name = State()
    waiting_for_goal = State()


class ReasonDetailForm(StatesGroup):
    waiting_for_details = State()
