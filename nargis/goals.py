"""Goals & Milestones Hierarchy Engine for Nargis 2.0.

Hierarchy:
Goal (High Level) -> Milestone (Checkpoint) -> Project (Domain) -> Task (Action)
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from discipline.db import get_session
from discipline.models import (
    DisciplineGoal,
    DisciplineMilestone,
    DisciplineProject,
    utcnow,
)

logger = logging.getLogger(__name__)


class GoalsEngine:
    """Manages the full goal hierarchy for personal and business mastery."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def create_goal(
        self,
        title: str,
        category: str = "business",
        deadline: Optional[datetime.date] = None,
        description: Optional[str] = None,
    ) -> DisciplineGoal:
        """Create a new high-level goal."""
        async with get_session() as session:
            goal = DisciplineGoal(
                user_id=self.owner_id,
                title=title.strip(),
                category=category,
                deadline=deadline,
                description=description,
                progress_percent=0,
                status="ACTIVE",
            )
            session.add(goal)
            await session.commit()
            await session.refresh(goal)
            return goal

    async def add_milestone(
        self,
        goal_id: int,
        title: str,
        deadline: Optional[datetime.date] = None,
    ) -> DisciplineMilestone:
        """Add milestone under a goal."""
        async with get_session() as session:
            ms = DisciplineMilestone(
                goal_id=goal_id,
                title=title.strip(),
                deadline=deadline,
                status="PENDING",
            )
            session.add(ms)
            await session.commit()
            await session.refresh(ms)
            return ms

    async def get_active_goals_summary(self) -> str:
        """Return formatted summary of all active goals and milestones."""
        async with get_session() as session:
            stmt = (
                select(DisciplineGoal)
                .where(
                    DisciplineGoal.user_id == self.owner_id,
                    DisciplineGoal.status == "ACTIVE",
                )
                .options(selectinload(DisciplineGoal.milestones))
            )
            res = await session.execute(stmt)
            goals = list(res.scalars().all())

        if not goals:
            return "🎯 Hozircha faol maqsadlar kiritilmagan. /goals orqali yangi maqsad belgilashingiz mumkin."

        lines = ["🎯 <b>ASADBEK USTOZ — MAQSADLAR VA MILESTONELAR</b>\n"]
        for g in goals:
            dl_str = f" (Muddat: {g.deadline})" if g.deadline else ""
            lines.append(f"🏆 <b>{g.title}</b> [{g.category.upper()}]{dl_str} — {g.progress_percent}%")
            if g.milestones:
                for ms in g.milestones:
                    status_icon = "✅" if ms.status == "COMPLETED" else "⏳"
                    lines.append(f"   {status_icon} <i>{ms.title}</i>")
            lines.append("")

        return "\n".join(lines).strip()
