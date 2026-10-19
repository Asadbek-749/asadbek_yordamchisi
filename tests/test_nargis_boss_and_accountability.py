"""Unit and integration tests for Nargis 2.0 AI Boss Mode, Accountability, and Postponement Guard."""
from __future__ import annotations

import datetime
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from discipline.models import DisciplineTask
from discipline.repository import DisciplineRepository
from nargis.boss import AIBossEngine


@pytest_asyncio.fixture
async def boss_env(tmp_path):
    await init_db(data_dir=tmp_path)
    yield
    await close_db()


@pytest.mark.asyncio
async def test_morning_control_sequence_and_score_penalty(boss_env):
    owner_id = 5146977644
    boss = AIBossEngine(owner_id=owner_id)
    today = datetime.date(2026, 10, 19)

    # 1. 07:00 -> Level 1 Initial plan prompt
    s1 = await boss.check_morning_control_step("07:00", today)
    assert s1 is not None
    assert s1["level"] == 1
    assert "vazifangizni" in s1["message"].lower() and "yozing" in s1["message"].lower()

    # 2. 07:15 -> Level 2 Reminder
    s2 = await boss.check_morning_control_step("07:15", today)
    assert s2 is not None
    assert s2["level"] == 2
    assert "rejangiz hali kiritilmadi" in s2["message"].lower()

    # 3. 07:30 -> Level 3 Strict warning + Score penalty (-5)
    s3 = await boss.check_morning_control_step("07:30", today)
    assert s3 is not None
    assert s3["level"] == 3
    assert "-5 ball" in s3["message"]

    from nargis.service import NargisDisciplineScoreService
    score_svc = NargisDisciplineScoreService()
    score, _ = await score_svc.get_score(owner_id)
    assert score == 95  # 100 - 5 = 95

    # 4. 08:00 -> Level 4 Reason required
    s4 = await boss.check_morning_control_step("08:00", today)
    assert s4 is not None
    assert s4["level"] == 4
    assert "sababini yozing" in s4["message"].lower()

    # 5. 08:30 -> Level 5 Escalation
    s5 = await boss.check_morning_control_step("08:30", today)
    assert s5 is not None
    assert s5["level"] == 5
    assert "eskalatsiya" in s5["message"].lower()


@pytest.mark.asyncio
async def test_morning_control_silences_once_plan_submitted(boss_env):
    owner_id = 5146977644
    boss = AIBossEngine(owner_id=owner_id)
    today = datetime.date(2026, 10, 19)

    # Submit plan
    from discipline.db import get_session
    async with get_session() as session:
        repo = DisciplineRepository(session)
        await repo.create_daily_plan(owner_id, today, top_three="1. Kod yozish, 2. Test qilish")

    # Subsequent morning checks must return None
    s = await boss.check_morning_control_step("07:00", today)
    assert s is None
    s_late = await boss.check_morning_control_step("08:00", today)
    assert s_late is None


@pytest.mark.asyncio
async def test_postponement_guard_enforces_reason_on_repeated_delays(boss_env):
    owner_id = 5146977644
    boss = AIBossEngine(owner_id=owner_id)

    from discipline.db import get_session
    async with get_session() as session:
        task = DisciplineTask(
            user_id=owner_id,
            title="AI Loyihasi arxitekturasi",
            priority="high",
            status="planned",
            postponement_count=0,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    # 1. Postponement 1 -> Allowed
    res1 = await boss.handle_postponement_request(task_id)
    assert res1["success"] is True
    assert res1["postponement_count"] == 1

    # 2. Postponement 2 -> Allowed
    res2 = await boss.handle_postponement_request(task_id)
    assert res2["success"] is True
    assert res2["postponement_count"] == 2

    # 3. Postponement 3 -> Allowed
    res3 = await boss.handle_postponement_request(task_id)
    assert res3["success"] is True
    assert res3["postponement_count"] == 3

    # 4. Postponement 4 WITHOUT reason -> BLOCKED by Guard!
    res_blocked = await boss.handle_postponement_request(task_id, reason=None)
    assert res_blocked["success"] is False
    assert res_blocked["blocked"] is True
    assert "sababini yozishingiz shart" in res_blocked["message"].lower()

    # 5. Postponement 4 WITH valid reason -> ALLOWED
    res_allowed = await boss.handle_postponement_request(
        task_id, reason="Kutilmagan texnik nosozlik yuz berdi"
    )
    assert res_allowed["success"] is True
    assert res_allowed["blocked"] is False
    assert res_allowed["postponement_count"] == 4


@pytest.mark.asyncio
async def test_midday_progress_report(boss_env):
    owner_id = 5146977644
    boss = AIBossEngine(owner_id=owner_id)
    today = datetime.date(2026, 10, 19)

    from discipline.db import get_session
    async with get_session() as session:
        t1 = DisciplineTask(user_id=owner_id, title="Vazifa 1", status="completed", deadline=datetime.datetime.now(datetime.timezone.utc))
        t2 = DisciplineTask(user_id=owner_id, title="Vazifa 2", status="in_progress")
        t3 = DisciplineTask(user_id=owner_id, title="Vazifa 3", status="missed")
        session.add_all([t1, t2, t3])
        await session.commit()

    rep = await boss.get_midday_progress_report(today)
    assert rep["total"] == 3
    assert rep["completed"] == 1
    assert rep["overdue"] == 1
    assert rep["percent"] == 33
    assert "33%" in rep["text"]
    assert "1 ta muhim vazifa kechikmoqda" in rep["text"]
