"""Proactive Engine for Nargis AI 2.0.

Manages scheduled proactive audits and actions:
- 07:00 Morning Control (AIBossEngine)
- 13:00 Midday Progress Check
- 18:00 Evening Finance Check
- 21:00 Evening Review & Rating
- 22:00 Tomorrow Planning Suggestion
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from nargis.boss import AIBossEngine

logger = logging.getLogger(__name__)


class ProactiveEngine:
    """Coordinates daily proactive checks and deliveries."""

    def __init__(self, owner_id: int, bot: Any) -> None:
        self.owner_id = owner_id
        self.bot = bot
        self.boss = AIBossEngine(owner_id, bot)

    async def execute_minute_tick(self, now_utc: datetime.datetime) -> None:
        """Executed every 60s by the master scheduler."""
        tz = ZoneInfo("Asia/Tashkent")
        local_now = now_utc.astimezone(tz)
        local_time_str = local_now.strftime("%H:%M")
        today = local_now.date()

        # 1. Morning Control Escalation (07:00, 07:15, 07:30, 08:00, 08:30)
        morning_step = await self.boss.check_morning_control_step(local_time_str, today)
        if morning_step and self.bot:
            try:
                await self.bot.send_message(
                    self.owner_id,
                    morning_step["message"],
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Failed to send morning step to owner: %s", e)

        # 2. Midday Progress Audit (13:00)
        if local_time_str == "13:00" and self.bot:
            state = await self.boss.get_or_create_boss_state(today)
            if not state.midday_checked:
                report = await self.boss.get_midday_progress_report(today)
                try:
                    await self.bot.send_message(
                        self.owner_id,
                        report["text"],
                        parse_mode="HTML",
                    )
                    from sqlalchemy import update
                    from discipline.db import get_session
                    from discipline.models import NargisBossState
                    async with get_session() as session:
                        await session.execute(
                            update(NargisBossState)
                            .where(
                                NargisBossState.user_id == self.owner_id,
                                NargisBossState.check_date == today,
                            )
                            .values(midday_checked=True)
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning("Failed to send midday report to owner: %s", e)

        # 3. Tomorrow Planning (22:00)
        if local_time_str == "22:00" and self.bot:
            state = await self.boss.get_or_create_boss_state(today)
            if not state.tomorrow_planned:
                tomorrow = today + datetime.timedelta(days=1)
                text = (
                    f"🌙 <b>ERTANGI KUNNI REJALASHTIRISH (22:00)</b>\n\n"
                    f"Ertaga ({tomorrow.strftime('%d-%m-%Y')}) uchun qaysi asosiy vazifalarni belgilaymiz?\n"
                    f"Ertangi rejangizni hozir yozib qo'ying yoki /plan buyrug'idan foydalaning 🚀"
                )
                try:
                    await self.bot.send_message(self.owner_id, text, parse_mode="HTML")
                    from sqlalchemy import update
                    from discipline.db import get_session
                    from discipline.models import NargisBossState
                    async with get_session() as session:
                        await session.execute(
                            update(NargisBossState)
                            .where(
                                NargisBossState.user_id == self.owner_id,
                                NargisBossState.check_date == today,
                            )
                            .values(tomorrow_planned=True)
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning("Failed to send tomorrow planning prompt: %s", e)
