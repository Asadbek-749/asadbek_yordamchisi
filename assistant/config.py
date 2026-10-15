"""Configuration management for the Telegram Business assistant."""
from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import dotenv_values

SETTING_KEYS: Tuple[str, ...] = (
    "TELEGRAM_BOT_TOKEN",
    "OWNER_ID",
    "AUTO_SEND",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_BIN",
    "CLAUDE_MODEL",
    "CLAUDE_EFFORT",
    "CLAUDE_TIMEOUT_SECONDS",
    "LIMIT_COOLDOWN_MINUTES",
    "OWNER_TAKEOVER_MINUTES",
    "HISTORY_LIMIT",
    "DATA_DIR",
    "PROFILE_PATH",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "AI_PROVIDER",
)

ALLOWED_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
TRUE_VALUES = {"true", "1", "yes", "on"}
FALSE_VALUES = {"false", "0", "no", "off"}


class ConfigError(Exception):
    """Raised when configuration validation fails."""


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    bot_token: Optional[str] = field(default=None, repr=False)
    owner_id: Optional[int] = field(default=None)
    auto_send: bool = False
    claude_oauth_token: Optional[str] = field(default=None, repr=False)
    claude_bin: str = "claude"
    claude_model: Optional[str] = None
    claude_effort: str = "low"
    claude_timeout_seconds: int = 120
    limit_cooldown_minutes: int = 30
    owner_takeover_minutes: int = 15
    history_limit: int = 30
    data_dir: Path = field(default_factory=lambda: Path("data"))
    profile_path: Path = field(default_factory=lambda: Path("profile.md"))
    from_environment: bool = False
    gemini_api_key: Optional[str] = field(default=None, repr=False)
    gemini_model: str = "gemini-3.1-flash-lite"
    ai_provider: str = "claude"

    @property
    def claude_auth_mode(self) -> str:
        if not self.claude_oauth_token:
            return "this machine's claude login"
        if self.from_environment:
            return "subscription token from environment variables"
        return "subscription token from .env"

    @property
    def provider_label(self) -> str:
        if self.ai_provider == "gemini":
            return f"Google Gemini ({self.gemini_model})"
        return f"Claude Code ({self.claude_auth_mode})"


def env_file_warning(path: Path | str) -> Optional[str]:
    """Check file permissions on POSIX systems and return warning if too open."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        st = p.stat()
        # Only check on POSIX systems where group/other permissions exist
        if os.name == "posix":
            if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                return (
                    f"Warning: {p} is accessible to group or others. "
                    f"Restrict permissions with: chmod 600 {p}"
                )
    except OSError:
        pass
    return None


def _resolve_claude_bin(explicit: Optional[str]) -> str:
    if explicit and explicit.strip():
        return explicit.strip()

    found = shutil.which("claude")
    if found:
        return found

    # Fallback to standard location
    local_bin = Path.home() / ".local" / "bin" / "claude"
    if local_bin.is_file():
        return str(local_bin)

    return "claude"


def load_settings(
    env_file: Path | str = ".env",
    *,
    require_telegram: bool = True,
    overrides: Optional[Dict[str, Any]] = None,
    source: Optional[Dict[str, Any]] = None,
) -> Settings:
    """Load and validate settings from .env file or explicit source dict."""
    env_path = Path(env_file).resolve()
    base_dir = env_path.parent

    from_env = source is not None
    source_label = "the environment variables" if from_env else f"{env_path}"

    raw: Dict[str, Any] = {}
    if from_env:
        assert source is not None
        for k in SETTING_KEYS:
            if k in source and source[k] is not None:
                raw[k] = source[k]
    else:
        if not env_path.is_file():
            raise ConfigError(
                f"Configuration file not found at {env_path}. "
                "Create it with: cp .env.example .env && chmod 600 .env"
            )
        file_vals = dotenv_values(str(env_path))
        for k in SETTING_KEYS:
            if k in file_vals and file_vals[k] is not None:
                raw[k] = file_vals[k]

    if overrides:
        raw.update(overrides)

    def _get_str(key: str) -> Optional[str]:
        val = raw.get(key)
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    # 1. Telegram bot token
    bot_token = _get_str("TELEGRAM_BOT_TOKEN")
    if require_telegram:
        if not bot_token:
            raise ConfigError(f"TELEGRAM_BOT_TOKEN is missing in {source_label}.")
        # Telegram bot token format: <digits>:<string>
        if not re.match(r"^\d+:[A-Za-z0-9_-]+$", bot_token):
            raise ConfigError(
                f"TELEGRAM_BOT_TOKEN in {source_label} has invalid format. "
                "Expected '<numbers>:<alphanumeric string>' from @BotFather."
            )

    # 2. Owner ID
    raw_owner = _get_str("OWNER_ID")
    owner_id: Optional[int] = None
    if require_telegram:
        if not raw_owner:
            raise ConfigError(f"OWNER_ID is missing in {source_label}.")
        if not raw_owner.isdigit():
            raise ConfigError(
                f"OWNER_ID in {source_label} must be numeric only (e.g. 123456789)."
            )
        owner_id = int(raw_owner)
    elif raw_owner:
        if not raw_owner.isdigit():
            raise ConfigError(
                f"OWNER_ID in {source_label} must be numeric only (e.g. 123456789)."
            )
        owner_id = int(raw_owner)

    # 3. AUTO_SEND
    raw_auto = raw.get("AUTO_SEND", "false")
    auto_str = str(raw_auto).strip().lower()
    if auto_str in TRUE_VALUES:
        auto_send = True
    elif auto_str in FALSE_VALUES:
        auto_send = False
    else:
        raise ConfigError(
            f"Invalid AUTO_SEND value in {source_label}. "
            "Must be one of: true, false, 1, 0, yes, no, on, off."
        )

    # 4. CLAUDE_CODE_OAUTH_TOKEN
    raw_oauth = raw.get("CLAUDE_CODE_OAUTH_TOKEN")
    oauth_token: Optional[str] = None
    if raw_oauth is not None:
        token_str = str(raw_oauth).strip()
        if token_str:
            if token_str.startswith("sk-ant-api"):
                raise ConfigError(
                    f"CLAUDE_CODE_OAUTH_TOKEN in {source_label} is an API key (sk-ant-api...), "
                    "not a subscription token. Run 'claude setup-token' to obtain an OAuth token."
                )
            if any(c.isspace() for c in token_str):
                raise ConfigError(
                    f"CLAUDE_CODE_OAUTH_TOKEN in {source_label} contains whitespace. "
                    "Ensure the token is pasted on a single uninterrupted line."
                )
            if not token_str.startswith("sk-ant-oat"):
                raise ConfigError(
                    f"CLAUDE_CODE_OAUTH_TOKEN in {source_label} must start with 'sk-ant-oat...'."
                )
            oauth_token = token_str

    # 5. CLAUDE_BIN
    raw_bin = _get_str("CLAUDE_BIN")
    claude_bin = _resolve_claude_bin(raw_bin)

    # 6. CLAUDE_MODEL
    claude_model = _get_str("CLAUDE_MODEL")

    # 7. CLAUDE_EFFORT
    raw_effort = (_get_str("CLAUDE_EFFORT") or "low").lower()
    if raw_effort not in ALLOWED_EFFORTS:
        raise ConfigError(
            f"Invalid CLAUDE_EFFORT in {source_label}. "
            f"Allowed values are: {', '.join(sorted(ALLOWED_EFFORTS))}."
        )
    claude_effort = raw_effort

    # 8. Numeric settings
    def _parse_int(key: str, default: int, min_val: int) -> int:
        val = raw.get(key)
        if val is None or str(val).strip() == "":
            return default
        try:
            num = int(str(val).strip())
        except ValueError:
            raise ConfigError(f"{key} in {source_label} must be an integer.")
        if num < min_val:
            raise ConfigError(
                f"{key} in {source_label} must be at least {min_val}."
            )
        return num

    claude_timeout_seconds = _parse_int("CLAUDE_TIMEOUT_SECONDS", 120, 1)
    limit_cooldown_minutes = _parse_int("LIMIT_COOLDOWN_MINUTES", 30, 1)
    owner_takeover_minutes = _parse_int("OWNER_TAKEOVER_MINUTES", 15, 0)
    history_limit = _parse_int("HISTORY_LIMIT", 30, 1)

    # 9. Paths
    raw_data_dir = _get_str("DATA_DIR")
    if raw_data_dir:
        data_dir = Path(raw_data_dir)
        if not data_dir.is_absolute():
            data_dir = (base_dir / data_dir).resolve()
    else:
        data_dir = (base_dir / "data").resolve()

    raw_profile = _get_str("PROFILE_PATH")
    if raw_profile:
        profile_path = Path(raw_profile)
        if not profile_path.is_absolute():
            profile_path = (base_dir / profile_path).resolve()
    else:
        profile_path = (base_dir / "profile.md").resolve()

    # 10. Gemini settings
    raw_gemini_key = _get_str("GEMINI_API_KEY")
    gemini_api_key = raw_gemini_key if raw_gemini_key else None
    gemini_model = _get_str("GEMINI_MODEL") or "gemini-3.1-flash-lite"

    raw_provider = _get_str("AI_PROVIDER")
    if raw_provider:
        ai_provider = raw_provider.lower()
    elif gemini_api_key:
        ai_provider = "gemini"
    else:
        ai_provider = "claude"

    return Settings(
        bot_token=bot_token,
        owner_id=owner_id,
        auto_send=auto_send,
        claude_oauth_token=oauth_token,
        claude_bin=claude_bin,
        claude_model=claude_model,
        claude_effort=claude_effort,
        claude_timeout_seconds=claude_timeout_seconds,
        limit_cooldown_minutes=limit_cooldown_minutes,
        owner_takeover_minutes=owner_takeover_minutes,
        history_limit=history_limit,
        data_dir=data_dir,
        profile_path=profile_path,
        from_environment=from_env,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        ai_provider=ai_provider,
    )
