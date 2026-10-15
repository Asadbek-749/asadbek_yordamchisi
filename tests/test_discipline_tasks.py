"""Unit tests for Discipline task operations."""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from discipline.coach import DisciplineCoach
from discipline.db import Base, create_engine_and_factory
from discipline.repository import DisciplineRepository
from discipline.schemas import TaskCreate, TaskUpdate
from discipline.service import DisciplineService


class DummyCoach(DisciplineCoach):
    def __init__(self):
        pass

    async def coach_overdue_task(self, task, reason_category=None, reason_details=None, previous_reasons=None):
        return "Qat'iy harakat qiling: hoziroq 10 daqiqa vaqt ajrating."


@pytest.fixture
async def test_db():
    engine, factory = create_engine_and_factory("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_complete_task(test_db):
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 12345

        # 1. Create task
        task_in = TaskCreate(
            title="Database migratsiyasi",
            expected_result="Barcha jadvallar yaratilishi",
            priority="high",
            category="work",
            estimated_minutes=45,
        )
        task = await repo.create_task(user_id, task_in)
        await session.commit()

        assert task.id is not None
        assert task.status == "planned"
        assert task.priority == "high"

        # 2. Mark completed
        completed = await repo.mark_task_completed(task.id, user_id)
        await session.commit()

        assert completed is not None
        assert completed.status == "completed"
        assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_overdue_task_detection(test_db):
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 12345

        # Past deadline
        past_deadline = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        task = await repo.create_task(
            user_id,
            TaskCreate(
                title="Eski topshiriq",
                deadline=past_deadline,
            ),
        )
        await session.commit()

        # Mark overdue
        overdue = await repo.mark_overdue_tasks(user_id, datetime.datetime.now(datetime.timezone.utc))
        await session.commit()

        assert len(overdue) == 1
        assert overdue[0].id == task.id
        assert overdue[0].status == "missed"
