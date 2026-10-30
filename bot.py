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
from discipline import (
    DisciplineCoach,
    DisciplineScheduler,
    DisciplineService,
    build_discipline_router,
    close_db,
    init_db,
)

# Nargis AI Agent router (optional — graceful fallback if not available)
try:
    from nargis.handlers import build_nargis_owner_router
    _NARGIS_ROUTER_AVAILABLE = True
except ImportError:
    _NARGIS_ROUTER_AVAILABLE = False
    build_nargis_owner_router = None  # type: ignore

# Nargis Personal Finance router
try:
    from finance.handlers import build_finance_router
    from finance.service import FinanceService
    _FINANCE_AVAILABLE = True
except ImportError:
    _FINANCE_AVAILABLE = False
    build_finance_router = None  # type: ignore
    FinanceService = None  # type: ignore

# Nargis Mini App router and server
try:
    from nargis.webapp_router import build_webapp_router
    from webapp.server import start_webapp_server, stop_webapp_server
    _WEBAPP_AVAILABLE = True
except ImportError:
    _WEBAPP_AVAILABLE = False
    build_webapp_router = None  # type: ignore
    start_webapp_server = None  # type: ignore
    stop_webapp_server = None  # type: ignore

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


async def _handle_health_ping(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read(1024)
        body = b"OK"
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n" + body
        )
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _start_health_server_if_port(port_str: Optional[str]) -> Optional[asyncio.Server]:
    if not port_str:
        return None
    try:
        port = int(port_str)
    except ValueError:
        return None

    try:
        server = await asyncio.start_server(_handle_health_ping, "0.0.0.0", port)
        logger.info("Health check server listening on 0.0.0.0:%d", port)
        return server
    except Exception as e:
        logger.warning("Could not start health check server on port %d: %s", port, e)
        return None


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
    logger.info("🤖 NARGIS AI is running as @%s", bot_user.username)
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

    # Initialize Database for Discipline & Productivity System
    await init_db(data_dir=settings.data_dir)

    discipline_coach = DisciplineCoach(assistant.generator)
    discipline_service = DisciplineService(discipline_coach)
    discipline_router = build_discipline_router(discipline_service, settings.owner_id)

    dp = Dispatcher()
    sheets_task = None

    # Short owner alerts for quota / key / unexpected errors (max one Telegram message per hour)
    from assistant import gemini_client
    from notification.alerts import OwnerAlerter
    alerter = OwnerAlerter(bot, settings.owner_id)
    gemini_client.error_hook = alerter.alert_nowait

    @dp.errors()
    async def on_unhandled_error(event: Any) -> bool:
        logger.error("Unhandled error in update handler: %s", event.exception, exc_info=event.exception)
        alerter.alert_nowait("error", type(event.exception).__name__)
        return True

    # Nargis owner inbox router (registered first for priority)
    if _NARGIS_ROUTER_AVAILABLE and settings.owner_id:
        try:
            nargis_router = build_nargis_owner_router(settings.owner_id)
            dp.include_router(nargis_router)
            logger.info("🤖 Nargis owner inbox router registered.")
        except Exception as _nr_err:
            logger.warning("Could not register Nargis router: %s", _nr_err)

    # Nargis personal finance router
    if _FINANCE_AVAILABLE and settings.owner_id:
        try:
            finance_service = FinanceService()
            from finance import service as finance_service_module
            from finance.sheets import build_sync_from_env
            from finance.timeutil import today_local

            # Google Sheets mirror (optional). Read once, then remove the secret from the environment.
            sheets_sync = build_sync_from_env(
                os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None),
                os.environ.pop("GOOGLE_SHEETS_ID", None),
                settings.owner_id,
                apps_script_url=os.environ.pop("GOOGLE_APPS_SCRIPT_URL", None),
                apps_script_secret=os.environ.pop("GOOGLE_APPS_SCRIPT_SECRET", None),
            )
            if sheets_sync is not None:
                finance_service_module.change_hook = sheets_sync.mark_dirty
                sheets_sync.mark_dirty(today_local())  # refresh current month on startup
                sheets_task = asyncio.create_task(sheets_sync.run_forever(alerter))
            finance_router = build_finance_router(
                finance_service,
                settings.owner_id,
                assistant.generator,
                sheets_sync=sheets_sync,
                webapp_url=settings.webapp_url,
            )
            dp.include_router(finance_router)
            logger.info("💰 Nargis personal finance router registered.")
        except Exception as _fin_err:
            logger.warning("Could not register Finance router: %s", _fin_err)

    dp.include_router(discipline_router)

    # Nargis Mini App router (for /webapp command and WebApp data)
    if _WEBAPP_AVAILABLE:
        try:
            webapp_router = build_webapp_router(settings.owner_id, settings.webapp_url)
            dp.include_router(webapp_router)
            logger.info("📱 Nargis WebApp router registered.")
        except Exception as _wa_err:
            logger.warning("Could not register WebApp router: %s", _wa_err)

    dp.include_router(build_router(assistant))


    discipline_scheduler = DisciplineScheduler(bot, discipline_service, settings.owner_id)
    discipline_scheduler.alerter = alerter
    discipline_scheduler.start()

    port_env = os.environ.get("PORT") or os.environ.get("WEBAPP_PORT")
    webapp_runner = None
    health_server = None
    if _WEBAPP_AVAILABLE and port_env:
        try:
            p_val = int(port_env)
            webapp_runner = await start_webapp_server(
                port=p_val,
                bot=bot,
                owner_id=settings.owner_id,
                data_dir=settings.data_dir,
            )
        except Exception as e:
            logger.warning("Could not start WebApp server on port %s: %s", port_env, e)
    elif port_env:
        health_server = await _start_health_server_if_port(port_env)

    # Configure Telegram native Menu button with commands
    try:
        from aiogram.types import BotCommand, BotCommandScopeDefault
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🚀 Bosh sahifa va menyuni ochish"),
                BotCommand(command="finance", description="💰 Shaxsiy moliya boshqaruvi"),
                BotCommand(command="discipline", description="🎯 Intizom boshqaruv paneli"),
                BotCommand(command="boss", description="🚨 Nargis AI Boss nazorat paneli"),
                BotCommand(command="today", description="📋 Bugungi reja va vazifalar"),
                BotCommand(command="plan", description="📝 Kunlik rejani kiritish"),
                BotCommand(command="postpone", description="⚠️ Vazifani ko'chirish (sabab bilan)"),
                BotCommand(command="goals", description="🎯 Maqsadlar va Milestonelar"),
                BotCommand(command="memories", description="🧠 Nargis xotirasi va kontekst"),
                BotCommand(command="focus", description="⏳ Fokus (Pomodoro) taymer"),
                BotCommand(command="habits", description="🔥 Kundalik odatlar nazorati"),
                BotCommand(command="addtask", description="➕ Yangi vazifa yaratish"),
                BotCommand(command="report", description="📊 Kunlik intizom hisoboti"),
                BotCommand(command="week", description="📈 Haftalik tahlil"),
                BotCommand(command="score", description="🏆 Nargis intizom bali"),
                BotCommand(command="budget", description="🎯 Oylik byudjet holati"),
                BotCommand(command="export", description="📊 Oylik hisobot Excelda"),
                BotCommand(command="panel", description="📱 Moliya paneli (Mini App)"),
                BotCommand(command="recurring", description="🔁 Doimiy to'lovlar"),
                BotCommand(command="setrate", description="💵 Dollar kursini belgilash"),
                BotCommand(command="finance_compare", description="📈 Haftalik xarajatlar taqqoslash"),
                BotCommand(command="pendingfinance", description="⏳ Kutilayotgan tranzaksiyalar"),
                BotCommand(command="messages", description="📥 Kiruvchi xabarlar"),
                BotCommand(command="important", description="🔥 Muhim xabarlar"),
                BotCommand(command="contacts", description="👥 Kontaktlar va leadlar"),
                BotCommand(command="status", description="⚙️ Bot faoliyati va holati"),
                BotCommand(command="settings", description="🔧 Kun tartibi sozlamalari"),
            ],
            scope=BotCommandScopeDefault(),
        )

        # Configure Telegram Menu Button for Web App
        webapp_url = settings.webapp_url or os.environ.get("WEBAPP_URL")
        if webapp_url:
            from aiogram.types import MenuButtonWebApp, WebAppInfo
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Qabul Mini App",
                    web_app=WebAppInfo(url=webapp_url),
                )
            )
            logger.info("📱 Telegram Chat Menu Button set to 'Qabul Mini App' (%s)", webapp_url)

            # Owner's own chat: the menu button opens the private finance Mini App instead.
            from finance.keyboards import finance_app_url
            await bot.set_chat_menu_button(
                chat_id=settings.owner_id,
                menu_button=MenuButtonWebApp(text="Moliya", web_app=WebAppInfo(url=finance_app_url(webapp_url))),
            )
            logger.info("📱 Owner chat menu button set to 'Moliya' (%s)", finance_app_url(webapp_url))
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)

    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        discipline_scheduler.stop()
        if sheets_task is not None:
            sheets_task.cancel()
        await close_db()
        if webapp_runner and _WEBAPP_AVAILABLE:
            await stop_webapp_server(webapp_runner)
        if health_server:
            health_server.close()
            await health_server.wait_closed()
        await bot.session.close()


def main() -> None:
    """Parse arguments and start corresponding workflow."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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
