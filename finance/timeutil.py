"""Tashkent-local time helpers (Railway runs in UTC, so date.today() is wrong between 00:00 and 05:00)."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

TASHKENT = ZoneInfo("Asia/Tashkent")

UZ_MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentyabr", "oktyabr", "noyabr", "dekabr",
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def now_local() -> datetime.datetime:
    """Current time in Asia/Tashkent."""
    return _utcnow().astimezone(TASHKENT)


def today_local() -> datetime.date:
    """Current calendar date in Asia/Tashkent."""
    return now_local().date()


def month_label(year: int, month: int) -> str:
    """Uzbek month label like 'oktyabr 2026'."""
    return f"{UZ_MONTHS[month - 1]} {year}"
