"""User roles and permissions for Nargis AI Agent."""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    USER = "USER"


def get_owner_id() -> Optional[int]:
    """Get owner Telegram ID from environment."""
    val = os.environ.get("OWNER_TELEGRAM_ID") or os.environ.get("OWNER_ID")
    if val:
        try:
            return int(val.strip())
        except ValueError:
            return None
    return None


def get_user_role(telegram_id: int, configured_owner_id: Optional[int] = None) -> UserRole:
    """Determine the user role for a given Telegram ID."""
    owner_id = configured_owner_id if configured_owner_id is not None else get_owner_id()
    if owner_id is not None and telegram_id == owner_id:
        return UserRole.OWNER
    return UserRole.USER


def is_owner(telegram_id: int, configured_owner_id: Optional[int] = None) -> bool:
    """Return True if the telegram_id belongs to the OWNER."""
    return get_user_role(telegram_id, configured_owner_id) == UserRole.OWNER
