"""Unit tests for Goals Hierarchy and Memory Engine in Nargis 2.0."""
from __future__ import annotations

import datetime
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from nargis.goals import GoalsEngine
from nargis.memory import MemoryEngine


@pytest_asyncio.fixture
async def goals_memory_env(tmp_path):
    await init_db(data_dir=tmp_path)
    yield
    await close_db()


@pytest.mark.asyncio
async def test_goals_and_milestones_hierarchy(goals_memory_env):
    owner_id = 5146977644
    engine = GoalsEngine(owner_id=owner_id)

    # 1. Create Goal
    goal = await engine.create_goal(
        title="AI Business Assistant System",
        category="business",
        deadline=datetime.date(2026, 12, 31),
        description="To'liq avtomatlashgan shaxsiy AI tizimi",
    )
    assert goal.id is not None
    assert goal.status == "ACTIVE"

    # 2. Add Milestones
    m1 = await engine.add_milestone(goal.id, "MVP reliz")
    m2 = await engine.add_milestone(goal.id, "Voice & Finance integratsiyasi")
    assert m1.id is not None
    assert m2.id is not None

    # 3. Summary
    summary = await engine.get_active_goals_summary()
    assert "AI Business Assistant System" in summary
    assert "MVP reliz" in summary
    assert "Voice & Finance integratsiyasi" in summary


@pytest.mark.asyncio
async def test_memory_engine_storage_and_context(goals_memory_env):
    owner_id = 5146977644
    engine = MemoryEngine(owner_id=owner_id)

    # 1. Store preferences
    await engine.store_memory(
        key="ish_vaqti",
        content="Ertalab 08:00 dan 18:00 gacha darslar va asosiy ish",
        memory_type="PREFERENCE",
    )
    await engine.store_memory(
        key="dam_olish",
        content="Yakshanba kunlari faqat dam olish",
        memory_type="BEHAVIORAL",
    )

    # 2. Retrieve
    prefs = await engine.get_memories_by_type("PREFERENCE")
    assert len(prefs) == 1
    assert prefs[0].key == "ish_vaqti"

    # 3. Context assembly
    ctx = await engine.get_relevant_context()
    assert "<user_memory>" in ctx
    assert "ish_vaqti" in ctx
    assert "dam_olish" in ctx
    assert "</user_memory>" in ctx
