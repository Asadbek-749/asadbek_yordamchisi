"""Main entrypoint for the Telegram Business Assistant."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramUnauthorizedError

from assistant.claude_cli import ClaudeError, ClaudeLimitError
from assistant.config import (
    ConfigError,
    SETTING_KEYS,
    Settings,
    env_file_warning,
    load_settings,
)
from assistant.handlers import ALLOWED_UPDATES, build_router
from assistant.language import detect_language
from assistant.prompts import build_system_prompt, build_user_prompt
from assistant.service import (
    Assistant,
    build_assistant,
    make_ai_generator,
    make_claude_cli,
    read_profile,
)
from assistant.store import ensure_private_dir

logger = logging.getLogger("assistant")


def settings_from_process_env(require_telegram: bool = True) -> Settings:
    """Load settings from process environment variables and wipe them from os.environ."""
    env_dict: Dict[str, Any] = {}
    try:
        for k in SETTING_KEYS:
            if k in os.environ and os.environ[k] is not None:
                env_dict[k] = os.environ[k]
        return load_settings(source=env_dict, require_telegram=require_telegram)
    finally:
        for k in SETTING_KEYS:
            os.environ.pop(k, None)


async def run_check_claude(settings: Settings) -> int:
    """Inspect AI engine environment and verify connectivity."""
    generator = make_ai_generator(settings)
    if settings.ai_provider == "gemini":
        try:
            res = await generator.inspect()
            print(json.dumps(res, indent=2))
            print(f"\nGemini check succeeded! Auth mode: {res.get('auth')}")
            return 0
        except ClaudeError as e:
            print(f"Gemini API check failed: {e}", file=sys.stderr)
            return 1

    # Claude CLI checks
    try:
        res = await generator.inspect()
    except ClaudeError as e:
        print(f"Claude CLI check failed: {e}", file=sys.stderr)
        return 1

    print(json.dumps(res, indent=2))

    init = res.get("init", {})
    if not init:
        print("Check failed: No init event received from Claude CLI.", file=sys.stderr)
        return 1

    for field in ("tools", "mcp_servers", "plugins", "skills"):
        val = init.get(field)
        if val:
            print(
                f"Check failed: {field} is not empty: {val}",
                file=sys.stderr,
            )
            return 1

    api_key_source = init.get("apiKeySource")
    if api_key_source in ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key"):
        print(
            f"Check failed: Forbidden apiKeySource '{api_key_source}'. Expected subscription token.",
            file=sys.stderr,
        )
        return 1

    api_provider = init.get("apiProvider")
    if api_provider not in ("firstParty", None):
        print(
            f"Check failed: Unexpected apiProvider '{api_provider}'.",
            file=sys.stderr,
        )
        return 1

    print(f"\nClaude CLI check succeeded! Auth mode: {res.get('auth')}")
    return 0


async def run_try(settings: Settings, text: str) -> int:
    """Generate sample reply for simulated incoming message without connecting to Telegram."""
    generator = make_ai_generator(settings)
    lang = detect_language(text)

    profile = read_profile(settings.profile_path)
    now = datetime.now(timezone.utc)
    sys_prompt = build_system_prompt(profile, now)

    history = [
        {"role": "other", "name": "Interlocutor", "text": text, "lang": lang}
    ]
    user_prompt = build_user_prompt(history, language=lang)

    print(f"AI Engine: {settings.provider_label}")
    print(f"Simulated message: {text}")
    print(f"Detected language: {lang}")
    print("Generating reply...")

    try:
        reply = await generator.generate(sys_prompt, user_prompt)
        print("\n--- Reply ---")
        print(reply)
        print("-------------")
        return 0
    except ClaudeLimitError as e:
        print(f"Usage limit error: {e}", file=sys.stderr)
        return 1
    except ClaudeError as e:
        print(f"Generation error: {e}", file=sys.stderr)
        return 1


async def run_bot(settings: Settings) -> None:
    """Run Telegram Business Assistant polling."""
    assert settings.bot_token is not None

    ensure_private_dir(settings.data_dir)
    ensure_private_dir(settings.data_dir / "claude-run")

    bot = Bot(token=settings.bot_token)
    try:
        bot_user = await bot.get_me()
    except TelegramUnauthorizedError:
        logger.error(
            "Telegram bot token is unauthorized or invalid. Please check TELEGRAM_BOT_TOKEN."
        )
        sys.exit(2)
    except Exception as e:
        logger.error("Failed to connect to Telegram: %s", e)
        sys.exit(2)

    assistant = build_assistant(settings)

    # Check profile
    profile_content = read_profile(settings.profile_path)
    if not profile_content or not any(
        line.strip() and not line.strip().endswith(":") and not line.strip().startswith("#")
        for line in profile_content.splitlines()
    ):
        logger.warning(
            "Profile at %s is empty or contains only unfilled template headings. "
            "Please fill in profile.md so the assistant knows how to respond.",
            settings.profile_path,
        )

    # Startup logs
    logger.info("Bot @%s is running", bot_user.username)
    if settings.auto_send:
        logger.warning("Mode: AUTO-SEND (replies will be automatically sent to Telegram chats)")
    else:
        logger.info("Mode: PREVIEW (replies are only shown in the logs, not sent)")

    logger.info("AI Backend: %s", settings.provider_label)
    if settings.ai_provider == "claude":
        logger.info("Claude binary: %s", settings.claude_bin)

    if assistant.state.is_paused():
        logger.warning(
            "Assistant is currently PAUSED. Send /resume in private chat with @%s to activate.",
            bot_user.username,
        )

    dp = Dispatcher()
    dp.include_router(build_router(assistant))

    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await bot.session.close()


def main() -> None:
    """Parse arguments and start corresponding workflow."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Telegram Business Personal Assistant")
    parser.add_argument(
        "--settings-from-env",
        action="store_true",
        help="Load settings from environment variables and wipe them from os.environ",
    )
    parser.add_argument(
        "--check-claude",
        action="store_true",
        help="Inspect Claude CLI isolation and exit (Telegram not required)",
    )
    parser.add_argument(
        "--try",
        dest="try_text",
        type=str,
        help="Generate a test reply for a sample message (Telegram not required)",
    )

    args = parser.parse_args()

    require_telegram = not (args.check_claude or args.try_text)

    try:
        if args.settings_from_env:
            settings = settings_from_process_env(require_telegram=require_telegram)
        else:
            warning = env_file_warning(".env")
            if warning:
                logger.warning(warning)
            settings = load_settings(".env", require_telegram=require_telegram)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)

    if args.check_claude:
        rc = asyncio.run(run_check_claude(settings))
        sys.exit(rc)

    if args.try_text:
        rc = asyncio.run(run_try(settings, args.try_text))
        sys.exit(rc)

    try:
        asyncio.run(run_bot(settings))
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
