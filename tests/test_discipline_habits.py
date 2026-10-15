"""Unit tests for habits tracking and streaks."""
import datetime
import pytest
from discipline.db import Base, create_engine_and_factory
from discipline.repository import DisciplineRepository


@pytest.fixture
async def test_db():
    engine, factory = create_engine_and_factory("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_habit_creation_and_streak(test_db):
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 12345

        habit = await repo.create_habit(user_id, "20 daqiqa kitob o'qish")
        await session.commit()

        assert habit.id is not None
        assert habit.current_streak == 0

        # Day 1 completion
        d1 = datetime.date(2026, 10, 1)
        log1, habit1 = await repo.log_habit_completion(habit.id, user_id, d1, completed=True)
        await session.commit()

        assert log1.completed is True
        assert habit1.current_streak == 1

        # Day 2 completion
        d2 = datetime.date(2026, 10, 2)
        log2, habit2 = await repo.log_habit_completion(habit.id, user_id, d2, completed=True)
        await session.commit()

        assert habit2.current_streak == 2
        assert habit2.best_streak == 2
