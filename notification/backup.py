"""Nightly SQLite backup delivered to the owner as a Telegram file (database only, never .env or keys)."""
from __future__ import annotations

import datetime
import gzip
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

TELEGRAM_BOT_FILE_LIMIT = 50 * 1024 * 1024  # bots can upload up to 50 MB


def sqlite_path_from_url(db_url: Optional[str]) -> Optional[Path]:
    """Return the file path for a file-backed SQLite URL, else None (PostgreSQL, in-memory)."""
    if not db_url or not db_url.startswith("sqlite"):
        return None
    path = unquote(urlparse(db_url).path)
    if not path or path in (":memory:", "/:memory:"):
        return None
    if len(path) > 2 and path[0] == "/" and path[2] == ":":  # /C:/dir/file.db on Windows
        path = path[1:]
    p = Path(path)
    return p if p.is_file() else None


def create_backup_bytes(db_file: Path) -> bytes:
    """Consistent snapshot via SQLite's online backup API, gzip-compressed."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return gzip.compress(snapshot.read_bytes(), compresslevel=6)


def backup_filename(local_date: datetime.date) -> str:
    return f"nargis_backup_{local_date.isoformat()}.db.gz"


async def send_backup(bot, owner_id: int, db_url: Optional[str], local_date: datetime.date) -> bool:
    """Send today's backup to the owner. Returns False when skipped (non-SQLite) or too large."""
    db_file = sqlite_path_from_url(db_url)
    if db_file is None:
        logger.info("Backup skipped: database is not a file-based SQLite database.")
        return False

    data = create_backup_bytes(db_file)
    if len(data) > TELEGRAM_BOT_FILE_LIMIT:
        await bot.send_message(owner_id, "⚠️ Zaxira nusxa 50 MB dan katta, Telegram orqali yuborib bo'lmadi.")
        return False

    from aiogram.types import BufferedInputFile

    await bot.send_document(
        owner_id,
        BufferedInputFile(data, filename=backup_filename(local_date)),
        caption=f"🗄 Kunlik zaxira nusxa — {local_date.strftime('%d.%m.%Y')}\n(faqat baza, kalit va parollarsiz)",
    )
    return True
