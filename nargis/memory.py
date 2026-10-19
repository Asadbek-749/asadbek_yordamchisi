"""Memory Engine for Nargis 2.0.

Supports:
- SHORT_TERM, LONG_TERM, EPISODIC, SEMANTIC, PREFERENCE, BEHAVIORAL, FINANCIAL
- Confidence scoring, privacy levels, and retrieval
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from discipline.db import get_session
from discipline.models import NargisMemory, utcnow

logger = logging.getLogger(__name__)


class MemoryEngine:
    """Manages persistent semantic, preference, and behavioral memory for the owner."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def store_memory(
        self,
        key: str,
        content: str,
        memory_type: str = "PREFERENCE",
        confidence: int = 100,
        source: str = "conversation",
        privacy_level: str = "PRIVATE",
    ) -> NargisMemory:
        """Store or update a memory item."""
        clean_key = key.strip().lower()

        async with get_session() as session:
            stmt = select(NargisMemory).where(
                NargisMemory.user_id == self.owner_id,
                NargisMemory.key == clean_key,
            )
            res = await session.execute(stmt)
            mem = res.scalar_one_or_none()

            if mem:
                mem.content = content.strip()
                mem.memory_type = memory_type.upper()
                mem.confidence = confidence
                mem.updated_at = utcnow()
            else:
                mem = NargisMemory(
                    user_id=self.owner_id,
                    memory_type=memory_type.upper(),
                    key=clean_key,
                    content=content.strip(),
                    confidence=confidence,
                    source=source,
                    privacy_level=privacy_level,
                )
                session.add(mem)

            await session.commit()
            await session.refresh(mem)
            return mem

    async def get_memories_by_type(self, memory_type: str) -> List[NargisMemory]:
        """Fetch all memories of a specific type."""
        async with get_session() as session:
            stmt = (
                select(NargisMemory)
                .where(
                    NargisMemory.user_id == self.owner_id,
                    NargisMemory.memory_type == memory_type.upper(),
                )
                .order_by(NargisMemory.created_at.desc())
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_relevant_context(self) -> str:
        """Format high-confidence active preferences and behavioral notes into prompt context."""
        async with get_session() as session:
            stmt = (
                select(NargisMemory)
                .where(
                    NargisMemory.user_id == self.owner_id,
                    NargisMemory.confidence >= 70,
                )
                .limit(10)
            )
            res = await session.execute(stmt)
            mems = list(res.scalars().all())

        if not mems:
            return ""

        lines = ["<user_memory>"]
        for m in mems:
            lines.append(f"- [{m.memory_type}] {m.key}: {m.content}")
        lines.append("</user_memory>")
        return "\n".join(lines)
