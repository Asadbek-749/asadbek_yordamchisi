"""Unit tests for daily and weekly metrics compilation."""
import datetime
import pytest
from discipline.db import Base, create_engine_and_factory
from discipline.repository import DisciplineRepository
from discipline.schemas import TaskCreate


@pytest.fixture
async def test_db():
    engine, factory = create_engine_and_factory("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_metrics_calculation(test_db):
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 999
        from zoneinfo import ZoneInfo
        today = datetime.datetime.now(ZoneInfo("Asia/Tashkent")).date()

        # 1. Add tasks
        t1 = await repo.create_task(user_id, TaskCreate(title="Task 1"))
        t2 = await repo.create_task(user_id, TaskCreate(title="Task 2"))
        await repo.mark_task_completed(t1.id, user_id)

        # 2. Add habit
        h = await repo.create_habit(user_id, "Odat 1")
        await repo.log_habit_completion(h.id, user_id, today, completed=True)

        # 3. Add focus session
        fs = await repo.start_focus_session(user_id, 25)
        await repo.finish_focus_session(user_id)

        await session.commit()

        # Compile metrics
        metrics = await repo.compile_daily_metrics(user_id, today)

        assert metrics.planned_count == 2
        assert metrics.completed_count == 1
        assert metrics.completion_rate_pct == 50.0
        assert metrics.habits_completed == 1
        assert metrics.habits_total == 1
        assert metrics.focus_minutes == 25
