"""Security and ownership protection tests."""
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
async def test_task_ownership_protection(test_db):
    """Ensure user A cannot view, complete, or log accountability for user B's tasks."""
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_a = 11111
        user_b = 22222

        # User A creates a task
        task = await repo.create_task(user_a, TaskCreate(title="User A Secret Task"))
        await session.commit()

        # User B tries to view it
        b_view = await repo.get_task(task.id, user_b)
        assert b_view is None

        # User B tries to mark it completed
        b_complete = await repo.mark_task_completed(task.id, user_b)
        assert b_complete is None

        # User B tries to record accountability for User A's task
        with pytest.raises(ValueError, match="not found for user"):
            await repo.record_accountability(
                user_id=user_b,
                task_id=task.id,
                reason_category="time_shortage",
            )
