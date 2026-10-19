"""Database configuration and async session factory."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from discipline.models import Base

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def resolve_database_url(raw_url: Optional[str] = None, data_dir: Optional[Path] = None) -> str:
    """Normalize database URL for async drivers (asyncpg for PostgreSQL, aiosqlite for SQLite)."""
    url = raw_url or os.environ.get("DATABASE_URL")

    if not url:
        # Default fallback to SQLite in data directory
        target_dir = data_dir or Path("data")
        target_dir.mkdir(parents=True, exist_ok=True)
        db_path = (target_dir / "discipline.db").resolve()
        return f"sqlite+aiosqlite:///{db_path}"

    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    elif url.startswith("sqlite://") and not url.startswith("sqlite+"):
        url = "sqlite+aiosqlite://" + url[len("sqlite://") :]

    return url


def create_engine_and_factory(
    db_url: str, echo: bool = False
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create async SQLAlchemy engine and session factory."""
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_async_engine(
        db_url,
        echo=echo,
        connect_args=connect_args,
        future=True,
    )
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    return engine, factory


async def init_db(
    db_url: Optional[str] = None, data_dir: Optional[Path] = None
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialize database engine, create tables if not present, and cache sessionmaker."""
    global _engine, _session_factory

    resolved_url = resolve_database_url(db_url, data_dir)
    # Mask password for logs
    safe_log_url = resolved_url.split("@")[-1] if "@" in resolved_url else resolved_url
    logger.info("Initializing database connection: %s", safe_log_url)

    engine, factory = create_engine_and_factory(resolved_url)
    _engine = engine
    _session_factory = factory
    # Ensure all domain models are registered in Base.metadata before create_all
    try:
        import finance.models  # noqa: F401
    except ImportError:
        pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Safe dynamic schema extensions for existing databases
        alterations = [
            "ALTER TABLE discipline_tasks ADD COLUMN postponement_count INTEGER DEFAULT 0",
            "ALTER TABLE discipline_tasks ADD COLUMN delay_reason TEXT",
            "ALTER TABLE discipline_tasks ADD COLUMN actual_minutes INTEGER",
            "ALTER TABLE discipline_tasks ADD COLUMN started_at TIMESTAMP",
            "ALTER TABLE nargis_messages ADD COLUMN chat_id BIGINT",
            "ALTER TABLE nargis_messages ADD COLUMN sender_name VARCHAR(255)",
            "ALTER TABLE nargis_messages ADD COLUMN sender_username VARCHAR(255)",
            "ALTER TABLE nargis_messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0",
            "ALTER TABLE nargis_messages ADD COLUMN deleted_at TIMESTAMP",
        ]
        from sqlalchemy import text
        for alt in alterations:
            try:
                await conn.execute(text(alt))
            except Exception:
                pass  # Column already exists or table freshly created

    logger.info("Database schema verified and ready.")
    return engine, factory


async def close_db() -> None:
    """Close active engine connections."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed.")


def is_db_initialized() -> bool:
    """Return True if the database sessionmaker is initialized and ready."""
    return _session_factory is not None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide transactional async session scope."""
    if _session_factory is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
