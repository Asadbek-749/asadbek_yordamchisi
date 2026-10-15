"""Unit tests for scheduler idempotency and alerts."""
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
async def test_reminder_idempotency(test_db):
    """Ensure a reminder cannot be marked/sent twice with the same key."""
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 12345
        key = "morning_12345_2026-10-15"

        # Not delivered initially
        assert await repo.is_reminder_delivered(key) is False

        # Deliver
        await repo.record_reminder_delivered(key, user_id, "morning_plan")
        await session.commit()

        # Now marked delivered
        assert await repo.is_reminder_delivered(key) is True


@pytest.mark.asyncio
async def test_focus_session_lifecycle(test_db):
    """Test start, active query, and finish for focus sessions."""
    async with test_db() as session:
        repo = DisciplineRepository(session)
        user_id = 12345

        # Start 25m session
        session_obj = await repo.start_focus_session(user_id, 25)
        await session.commit()

        assert session_obj.status == "active"
        assert session_obj.duration_minutes == 25

        # Query active
        active = await repo.get_active_focus_session(user_id)
        assert active is not None
        assert active.id == session_obj.id

        # Finish session
        finished = await repo.finish_focus_session(user_id, notes="AI test yozildi")
        await session.commit()

        assert finished.status == "completed"
        assert finished.notes == "AI test yozildi"

        # No active session left
        assert await repo.get_active_focus_session(user_id) is None
