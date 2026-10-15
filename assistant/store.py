"""Persistence layer for history, deduplication, and runtime state."""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional


def ensure_private_dir(path: Path) -> Path:
    """Ensure directory exists and has private 0700 permissions."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


class JsonFile:
    """Atomic JSON storage."""

    @staticmethod
    def load(path: Path, default: Any = None) -> Any:
        if not path.is_file():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default
                return json.loads(content)
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def save(path: Path, data: Any) -> None:
        ensure_private_dir(path.parent)
        dir_path = path.parent
        # Atomic write via temp file in same filesystem
        fd, tmp_name = tempfile.mkstemp(dir=dir_path, prefix="tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.name == "posix":
                try:
                    os.chmod(tmp_name, 0o600)
                except OSError:
                    pass
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise


class HistoryStore:
    """Per-connection and per-chat conversation history."""

    def __init__(self, path: Path, limit: int = 30) -> None:
        self.path = path
        self.limit = max(1, limit)
        self._data: Dict[str, List[Dict[str, Any]]] = JsonFile.load(path, default={})

    def _key(self, connection_id: str, chat_id: int | str) -> str:
        return f"{connection_id}:{chat_id}"

    def get(self, connection_id: str, chat_id: int | str) -> List[Dict[str, Any]]:
        return list(self._data.get(self._key(connection_id, chat_id), []))

    def add(
        self,
        connection_id: str,
        chat_id: int | str,
        role: str,
        name: str,
        text: str,
        lang: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        key = self._key(connection_id, chat_id)
        items = self._data.setdefault(key, [])
        items.append(
            {
                "role": role,
                "name": name,
                "text": text,
                "lang": lang,
                "ts": time.time() if ts is None else ts,
            }
        )
        if len(items) > self.limit:
            self._data[key] = items[-self.limit :]
        JsonFile.save(self.path, self._data)

    def last_language(self, connection_id: str, chat_id: int | str) -> Optional[str]:
        items = self.get(connection_id, chat_id)
        for item in reversed(items):
            if item.get("lang"):
                return item["lang"]
        return None

    def chat_count(self) -> int:
        return len(self._data)


class SeenMessages:
    """Deduplication tracker backed by OrderedDict and persisted to disk."""

    def __init__(self, path: Path, capacity: int = 5000) -> None:
        self.path = path
        self.capacity = max(1, capacity)
        raw_list = JsonFile.load(path, default=[])
        self._data: OrderedDict[str, None] = OrderedDict()
        for k in raw_list:
            self._data[k] = None

    def first_time(self, key: str) -> bool:
        """Return True if message key has not been seen before, False otherwise."""
        if key in self._data:
            return False
        self._data[key] = None
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)
        JsonFile.save(self.path, list(self._data.keys()))
        return True


class RuntimeState:
    """Persistent runtime state across process restarts (pause and limit)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data = JsonFile.load(path, default={})
        self._paused: bool = bool(data.get("paused", False))
        self._limit_until: float = float(data.get("limit_until", 0.0))

    def _save(self) -> None:
        JsonFile.save(
            self.path,
            {
                "paused": self._paused,
                "limit_until": self._limit_until,
            },
        )

    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._save()

    def resume(self) -> None:
        self._paused = False
        self._limit_until = 0.0
        self._save()

    def set_limit(self, until: float) -> None:
        self._limit_until = float(until)
        self._save()

    @property
    def limit_until(self) -> float:
        return self._limit_until
