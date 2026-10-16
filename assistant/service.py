"""Business logic service for the Telegram Business assistant."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

from aiogram.exceptions import TelegramAPIError

from assistant.claude_cli import ClaudeCLI, ClaudeError, ClaudeLimitError
from assistant.config import Settings
from assistant.gemini_client import GeminiClient
from assistant.language import detect_language
from assistant.prompts import SKIP_TOKEN, build_system_prompt, build_user_prompt
from assistant.store import (
    HistoryStore,
    JsonFile,
    RuntimeState,
    SeenMessages,
    ensure_private_dir,
)

logger = logging.getLogger(__name__)

# Nargis AI Agent (lazy import to avoid circular deps)
try:
    from nargis.agent import NargisAgent
    from nargis.classifier import IntentClassifier
    from nargis.validator import ResponseValidator
    from nargis.service import NargisContactService
    _NARGIS_AVAILABLE = True
except ImportError:
    _NARGIS_AVAILABLE = False
    NargisAgent = None  # type: ignore
    logger.warning("Nargis agent module not found. Running in basic mode.")


class Outcome(str, Enum):
    """Possible outcomes of processing a business message."""

    previewed = "previewed"
    sent = "sent"
    duplicate = "duplicate"
    ignored_bot = "ignored_bot"
    no_connection = "no_connection"
    foreign_connection = "foreign_connection"
    connection_disabled = "connection_disabled"
    unsupported = "unsupported"
    owner_message = "owner_message"
    owner_active = "owner_active"
    paused = "paused"
    limited = "limited"
    skipped = "skipped"
    permission_denied = "permission_denied"
    error = "error"


@dataclass
class Preview:
    """Preview of a generated reply before sending."""

    connection_id: str
    chat_id: int | str
    chat_name: str
    language: Optional[str]
    incoming: str
    reply: str
    note: Optional[str] = None


def print_preview(preview: Preview) -> None:
    """Print preview nicely to log/stdout."""
    lines = [
        "=== PREVIEW (not sent) ===",
        f"Connection: {preview.connection_id}",
        f"Chat: {preview.chat_name} ({preview.chat_id})",
        f"Language: {preview.language}",
        f"Incoming: {preview.incoming}",
        f"Reply: {preview.reply}",
    ]
    if preview.note:
        lines.append(f"Note: {preview.note}")
    lines.append("==========================")
    text = "\n".join(lines)
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass


def describe(message: Any) -> Optional[str]:
    """Describe message content (text or media description). Returns None for service messages."""
    text = getattr(message, "text", None)
    if text:
        return str(text)

    caption = getattr(message, "caption", None)
    caption_str = f" {caption}" if caption else ""

    if getattr(message, "voice", None) is not None:
        return "[voice message]"
    if getattr(message, "video_note", None) is not None:
        return "[video message]"
    if getattr(message, "photo", None):
        return f"[photo]{caption_str}"
    if getattr(message, "sticker", None) is not None:
        st = message.sticker
        emoji = getattr(st, "emoji", None)
        return f"[sticker] {emoji}" if emoji else "[sticker]"
    if getattr(message, "video", None) is not None:
        return f"[video]{caption_str}"
    if getattr(message, "audio", None) is not None:
        return f"[audio]{caption_str}"
    if getattr(message, "document", None) is not None:
        return f"[document]{caption_str}"
    if getattr(message, "location", None) is not None:
        return "[location]"
    if getattr(message, "contact", None) is not None:
        return "[contact]"

    return None


def can_reply(connection: Any) -> bool:
    """Check if connection has permission to reply."""
    if hasattr(connection, "rights") and connection.rights is not None:
        return bool(getattr(connection.rights, "can_reply", False))
    return bool(getattr(connection, "can_reply", False))


def read_profile(path: Path | str) -> str:
    """Read profile from markdown file and strip HTML comments."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        content = p.read_text(encoding="utf-8")
        clean = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()
        return clean
    except OSError:
        return ""


class Assistant:
    """Coordinates business message processing and automated replies."""

    def __init__(
        self,
        settings: Settings,
        generator: Any,
        *,
        clock: Callable[[], float] = time.time,
        preview_sink: Callable[[Preview], None] = print_preview,
    ) -> None:
        self.settings = settings
        self.generator = generator
        self.clock = clock
        self.preview_sink = preview_sink

        ensure_private_dir(settings.data_dir)
        self.history = HistoryStore(
            settings.data_dir / "history.json", limit=settings.history_limit
        )
        self.seen = SeenMessages(settings.data_dir / "seen.json")
        self.state = RuntimeState(settings.data_dir / "state.json")

        self._connections: Dict[str, Any] = {}
        self.outcomes: deque[Outcome] = deque(maxlen=100)
        self._takeovers: Dict[str, float] = {}
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._last_error_notify: float = 0.0
        self._previews_path = settings.data_dir / "previews.jsonl"

        # Nargis AI Agent — optional, activated if nargis module is available
        if _NARGIS_AVAILABLE and settings.owner_id:
            self._nargis_agent: Optional[Any] = NargisAgent(
                ai_generator=generator,
                owner_id=settings.owner_id,
                classifier=IntentClassifier(generator),
                validator=ResponseValidator(),
                contact_service=NargisContactService(),
            )
            self._contact_service: Optional[Any] = NargisContactService()
        else:
            self._nargis_agent = None
            self._contact_service = None

    def _get_chat_lock(self, chat_key: str) -> asyncio.Lock:
        if chat_key not in self._chat_locks:
            self._chat_locks[chat_key] = asyncio.Lock()
        return self._chat_locks[chat_key]

    def _record_outcome(self, outcome: Outcome) -> Outcome:
        self.outcomes.append(outcome)
        return outcome

    def _record_preview(self, preview: Preview) -> None:
        self.preview_sink(preview)
        data = asdict(preview)
        data["ts"] = self.clock()
        ensure_private_dir(self._previews_path.parent)
        line = json.dumps(data, ensure_ascii=False) + "\n"
        try:
            with open(self._previews_path, "a", encoding="utf-8") as f:
                f.write(line)
            if os.name == "posix":
                try:
                    os.chmod(self._previews_path, 0o600)
                except OSError:
                    pass
        except OSError as e:
            logger.error("Failed to append preview to %s: %s", self._previews_path, e)

    async def on_business_message(self, bot: Any, message: Any) -> Outcome:
        """Process incoming business message through strict pipeline."""
        # 1. Check business_connection_id
        conn = getattr(message, "business_connection_id", None)
        if not conn:
            return self._record_outcome(Outcome.no_connection)

        # 2. Duplicate detection (must be checked BEFORE any await)
        chat_id = message.chat.id if message.chat else None
        msg_id = getattr(message, "message_id", None)
        key = f"{conn}:{chat_id}:{msg_id}"
        if not self.seen.first_time(key):
            return self._record_outcome(Outcome.duplicate)

        # 3. Ignore messages from business bots or other bots
        if (
            getattr(message, "sender_business_bot", None) is not None
            or (message.from_user and getattr(message.from_user, "is_bot", False))
        ):
            return self._record_outcome(Outcome.ignored_bot)

        # 4. Connection lookup and verification
        connection = self._connections.get(conn)
        if connection is None:
            try:
                connection = await bot.get_business_connection(business_connection_id=conn)
                self._connections[conn] = connection
            except Exception:
                return self._record_outcome(Outcome.no_connection)

        conn_user = getattr(connection, "user", None)
        conn_user_id = getattr(conn_user, "id", None) if conn_user else None
        if conn_user_id != self.settings.owner_id:
            return self._record_outcome(Outcome.foreign_connection)

        if not getattr(connection, "is_enabled", False):
            return self._record_outcome(Outcome.connection_disabled)

        # 5. Unsupported message types / service messages
        desc = describe(message)
        if desc is None:
            return self._record_outcome(Outcome.unsupported)

        # 6. Message from owner: register takeover, do not reply
        chat_key = f"{conn}:{chat_id}"
        from_user = getattr(message, "from_user", None)
        from_id = getattr(from_user, "id", None) if from_user else None
        if from_id == self.settings.owner_id:
            self.history.add(
                conn, chat_id, role="owner", name="You", text=desc, lang=None, ts=self.clock()
            )
            if self.settings.owner_takeover_minutes > 0:
                self._takeovers[chat_key] = self.clock() + (self.settings.owner_takeover_minutes * 60)
            return self._record_outcome(Outcome.owner_message)

        # 7. Language detection and record incoming message
        lang = detect_language(desc) or self.history.last_language(conn, chat_id)
        sender_name = getattr(from_user, "full_name", None) or "User"
        sender_username = getattr(from_user, "username", None)
        self.history.add(
            conn, chat_id, role="other", name=sender_name, text=desc, lang=lang, ts=self.clock()
        )

        # 7a. Nargis: classify message + notify owner if important
        classification = None
        if self._nargis_agent is not None:
            try:
                classification = await self._nargis_agent.process_and_classify(
                    message_text=desc,
                    sender_name=sender_name,
                    sender_username=sender_username,
                    sender_telegram_id=from_id,
                    connection_id=conn,
                    chat_id=chat_id,
                )
                logger.info(
                    "Nargis classify: intent=%s urgency=%s score=%d",
                    classification.primary_intent,
                    classification.urgency,
                    classification.importance_score,
                )
            except Exception as _nargis_err:
                logger.warning("Nargis classification error (non-fatal): %s", _nargis_err)

        # 8. Chat lock to serialize generation and check active blocks
        lock = self._get_chat_lock(chat_key)
        async with lock:
            now = self.clock()
            if self.state.is_paused():
                return self._record_outcome(Outcome.paused)
            if self.state.limit_until > now:
                return self._record_outcome(Outcome.limited)
            if self._takeovers.get(chat_key, 0.0) > now:
                return self._record_outcome(Outcome.owner_active)

            # 9. Reply generation
            profile = read_profile(self.settings.profile_path)
            now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
            sys_prompt = build_system_prompt(profile, now_dt)
            user_prompt = build_user_prompt(self.history.get(conn, chat_id), language=lang)

            try:
                reply = await self.generator.generate(sys_prompt, user_prompt)
            except ClaudeLimitError as err:
                if err.resets_at and err.resets_at > self.clock():
                    self.state.set_limit(float(err.resets_at))
                else:
                    self.state.set_limit(self.clock() + (self.settings.limit_cooldown_minutes * 60))
                logger.warning("Claude subscription limit reached: %s", err)
                provider_label = "Gemini" if self.settings.ai_provider == "gemini" else "Claude"
                if self.settings.auto_send and self.settings.owner_id:
                    try:
                        await bot.send_message(
                            self.settings.owner_id,
                            f"⚠️ {provider_label} obuna/so'rov limiti tugadi: {err}",
                        )
                    except Exception:
                        pass
                return self._record_outcome(Outcome.limited)
            except ClaudeError as err:
                logger.error("AI generation error: %s", err)
                if (
                    self.settings.auto_send
                    and self.settings.owner_id
                    and (self.clock() - self._last_error_notify > 600)
                ):
                    self._last_error_notify = self.clock()
                    provider_label = "Gemini API" if self.settings.ai_provider == "gemini" else "Claude CLI"
                    try:
                        await bot.send_message(
                            self.settings.owner_id,
                            f"⚠️ {provider_label} xatosi: {err}",
                        )
                    except Exception:
                        pass
                return self._record_outcome(Outcome.error)

            # 9a. Validate reply through Nargis safety layer
            if self._nargis_agent is not None:
                reply = self._nargis_agent.validate_reply(reply)

            # 10. Check empty or SKIP token
            if not reply or reply.strip() == SKIP_TOKEN:
                return self._record_outcome(Outcome.skipped)

            chat_title = getattr(message.chat, "title", None) or sender_name
            preview = Preview(
                connection_id=conn,
                chat_id=chat_id,
                chat_name=chat_title,
                language=lang,
                incoming=desc,
                reply=reply,
            )

            # 10a. Nargis: log message to DB + send owner notification if important
            if self._nargis_agent is not None and classification is not None:
                try:
                    owner_notified = await self._nargis_agent.send_owner_notification(
                        bot=bot,
                        classification=classification,
                        sender_name=sender_name,
                        sender_username=sender_username,
                        message_text=desc,
                    )
                    if self._contact_service is not None:
                        from discipline.db import is_db_initialized
                        if is_db_initialized():
                            await self._contact_service.log_incoming_message(
                                telegram_id=from_id,
                                sender_name=sender_name,
                                username=sender_username,
                                content=desc,
                                classification=classification,
                                telegram_message_id=getattr(message, "message_id", None),
                                business_connection_id=conn,
                                ai_reply=reply if reply.strip() != SKIP_TOKEN else None,
                                owner_notified=owner_notified,
                            )
                except Exception as _log_err:
                    logger.warning("Nargis logging error (non-fatal): %s", _log_err)

            # 11. Preview mode (AUTO_SEND=false)
            if not self.settings.auto_send:
                self._record_preview(preview)
                return self._record_outcome(Outcome.previewed)

            # 12. Auto-send mode
            # Re-check owner active during generation
            if self._takeovers.get(chat_key, 0.0) > self.clock():
                preview.note = "Owner became active during generation"
                self._record_preview(preview)
                return self._record_outcome(Outcome.owner_active)

            if self.state.is_paused():
                return self._record_outcome(Outcome.paused)
            if self.state.limit_until > self.clock():
                return self._record_outcome(Outcome.limited)

            # Re-query fresh connection from Telegram (NOT from cache)
            try:
                fresh_conn = await bot.get_business_connection(business_connection_id=conn)
            except Exception as e:
                preview.note = f"Failed to verify fresh connection: {e}"
                self._record_preview(preview)
                return self._record_outcome(Outcome.permission_denied)

            fresh_user = getattr(fresh_conn, "user", None)
            fresh_user_id = getattr(fresh_user, "id", None) if fresh_user else None
            if (
                fresh_user_id != self.settings.owner_id
                or not getattr(fresh_conn, "is_enabled", False)
                or not can_reply(fresh_conn)
            ):
                preview.note = "Permission denied or connection disabled"
                self._record_preview(preview)
                return self._record_outcome(Outcome.permission_denied)

            # Send reply in chunks of 4000 characters
            try:
                for i in range(0, len(reply), 4000):
                    chunk = reply[i : i + 4000]
                    await bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        business_connection_id=conn,
                    )
            except TelegramAPIError as e:
                logger.error("Failed to send message via Telegram API: %s", e)
                return self._record_outcome(Outcome.error)

            self.history.add(
                conn, chat_id, role="owner", name="You", text=reply, lang=lang, ts=self.clock()
            )
            return self._record_outcome(Outcome.sent)

    async def on_business_connection(self, connection: Any) -> None:
        """Handle incoming business connection update."""
        self._connections[connection.id] = connection
        conn_user = getattr(connection, "user", None)
        conn_user_id = getattr(conn_user, "id", None) if conn_user else None
        if conn_user_id != self.settings.owner_id:
            logger.warning(
                "Received business connection from foreign user: %s (expected owner: %s)",
                conn_user_id,
                self.settings.owner_id,
            )
        else:
            logger.info(
                "Business connection %s: user=%s enabled=%s can_reply=%s",
                connection.id,
                conn_user_id,
                getattr(connection, "is_enabled", False),
                can_reply(connection),
            )

    def pause(self) -> None:
        """Pause assistant replies."""
        self.state.pause()

    def resume(self) -> None:
        """Resume assistant replies and clear limits and takeover timers."""
        self.state.resume()
        self._takeovers.clear()

    def status_text(self) -> str:
        """Return status summary in English for the owner."""
        if self.settings.auto_send:
            mode_desc = "AUTO-SEND (replies are automatically sent)"
        else:
            mode_desc = "PREVIEW (replies are only shown in the logs)"

        state_desc = "Paused" if self.state.is_paused() else "Running"

        if self.state.limit_until > self.clock():
            t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.state.limit_until))
            limit_desc = f"Active until {t_str} UTC"
        else:
            limit_desc = "None"

        return (
            f"🤖 Telegram Business Assistant Status\n\n"
            f"Mode: {mode_desc}\n"
            f"Status: {state_desc}\n"
            f"Usage limit: {limit_desc}\n"
            f"Active chats in history: {self.history.chat_count()}"
        )

    async def on_direct_private_message(self, bot: Any, message: Any) -> None:
        """Handle incoming direct private message sent to the bot by a user or the owner."""
        from_user = message.from_user
        if not from_user or getattr(from_user, "is_bot", False):
            return

        chat_id = message.chat.id
        from_id = from_user.id
        raw_text = describe_message(message)
        if not raw_text:
            return

        is_owner = (from_id == self.settings.owner_id)
        sender_name = getattr(from_user, "full_name", None) or getattr(from_user, "first_name", None) or "User"
        sender_username = getattr(from_user, "username", None)
        conn = f"direct_{chat_id}"

        # If sender is the owner, check for message dispatch first, then finance input, else act as direct copilot
        if is_owner:
            from discipline.db import is_db_initialized
            if is_db_initialized():
                try:
                    from nargis.dispatcher import get_message_dispatcher
                    dispatcher = get_message_dispatcher()
                    dispatched = await dispatcher.handle_owner_message(
                        message=message,
                        text=raw_text,
                        owner_id=from_id,
                        bot=bot,
                        ai_generator=self.generator,
                    )
                    if dispatched:
                        return
                except Exception as _disp_err:
                    logger.warning("Nargis dispatcher check error: %s", _disp_err)

                try:
                    from finance.extractor import FinanceExtractor
                    from finance.handlers import _process_finance_text
                    from finance.service import FinanceService
                    fin_extractor = FinanceExtractor(self.generator)
                    fin_res = fin_extractor.rule_based_extract(raw_text)
                    if fin_res.transactions or fin_res.is_ambiguous:
                        fin_service = FinanceService()
                        await _process_finance_text(
                            message=message,
                            text=raw_text,
                            service=fin_service,
                            extractor=fin_extractor,
                            owner_id=from_id,
                            source="text",
                        )
                        return
                except Exception as _fe_err:
                    logger.warning("Finance extraction check error: %s", _fe_err)

            lang = detect_language(raw_text) or "uz"
            self.history.add(conn, chat_id, role="other", name="Asadbek", text=raw_text, lang=lang, ts=self.clock())
            profile = read_profile(self.settings.profile_path)
            now_dt = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
            from assistant.prompts import build_owner_copilot_prompt
            sys_prompt = build_owner_copilot_prompt(profile, now_dt)
            user_prompt = build_user_prompt(self.history.get(conn, chat_id), language=lang)
            try:
                reply = await self.generator.generate(sys_prompt, user_prompt)
                if reply and reply.strip() != SKIP_TOKEN:
                    self.history.add(conn, chat_id, role="owner", name="Nargis", text=reply, lang=lang, ts=self.clock())
                    await message.answer(reply)
            except Exception as e:
                logger.error("Owner direct reply generation error: %s", e)
                await message.answer("Kechirasiz, ustoz, javob tayyorlashda texnik xatolik yuz berdi.")
            return

        # Sender is an external user (student, client, lead, stranger)
        lang = detect_language(raw_text) or self.history.last_language(conn, chat_id) or "uz"
        self.history.add(conn, chat_id, role="other", name=sender_name, text=raw_text, lang=lang, ts=self.clock())

        # 1. Nargis classify & lead intake
        classification = None
        if self._nargis_agent is not None:
            try:
                classification = await self._nargis_agent.process_and_classify(
                    message_text=raw_text,
                    sender_name=sender_name,
                    sender_username=sender_username,
                    sender_telegram_id=from_id,
                    connection_id="direct",
                    chat_id=chat_id,
                )
                logger.info(
                    "Nargis direct classify: from=%s intent=%s urgency=%s score=%d",
                    sender_name,
                    classification.primary_intent,
                    classification.urgency,
                    classification.importance_score,
                )
            except Exception as _err:
                logger.warning("Nargis direct classification error: %s", _err)

        # 2. Notify owner if high or critical
        if self._nargis_agent is not None and classification is not None and self.settings.owner_id:
            try:
                await self._nargis_agent.send_owner_notification(
                    bot=bot,
                    classification=classification,
                    sender_name=sender_name,
                    sender_username=sender_username,
                    message_text=raw_text,
                )
            except Exception as _err:
                logger.warning("Nargis direct owner notify error: %s", _err)

        # 3. Generate response as Nargis
        profile = read_profile(self.settings.profile_path)
        now_dt = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
        sys_prompt = build_system_prompt(profile, now_dt)
        user_prompt = build_user_prompt(self.history.get(conn, chat_id), language=lang)

        try:
            reply = await self.generator.generate(sys_prompt, user_prompt)
        except Exception as err:
            logger.error("Nargis direct generation error: %s", err)
            reply = "Assalomu alaykum! Xabaringiz qabul qilindi. Asadbek ustozga yetkazaman, tez orada javob beradilar."

        # 4. Safety validation
        if self._nargis_agent is not None and reply:
            reply = self._nargis_agent.validate_reply(reply)

        if not reply or reply.strip() == SKIP_TOKEN:
            reply = "Xabaringiz qabul qilindi. Asadbek ustozga yetkazaman, tez orada javob beramiz."

        # 5. Send reply to user
        try:
            self.history.add(conn, chat_id, role="owner", name="Nargis", text=reply, lang=lang, ts=self.clock())
            await message.answer(reply)
        except Exception as e:
            logger.error("Failed to send direct reply to %d: %s", chat_id, e)

    def start_text(self) -> str:
        """Return detailed welcoming and setup guide in Uzbek for the owner."""
        if self.settings.auto_send:
            mode_str = "AUTO-SEND (avtomatik yuboriladi)"
        else:
            mode_str = "PREVIEW (faqat logda ko'rinadi)"

        state_str = "Pauzada" if self.state.is_paused() else "Faol ishlamoqda"

        return (
            f"🌸 <b>Assalomu alaykum, Asadbek ustoz!</b>\n\n"
            f"Men sizning shaxsiy AI assistentingiz — <b>Nargis</b>man.\n\n"
            f"📌 <b>Hozirgi holat:</b>\n"
            f"• Rejim: {mode_str}\n"
            f"• Holat: {state_str}\n\n"
            f"💰 <b>Shaxsiy Moliya Boshqaruvi:</b>\n"
            f"/finance — Moliya boshqaruv paneli\n"
            f"/budget — Oylik byudjet va tahlil\n"
            f"/pendingfinance — Tasdiqlanmagan xarajatlar\n"
            f"/finance_today — Bugungi moliyaviy hisobot\n"
            f"/finance_week — Haftalik hisobot\n\n"
            f"🎯 <b>Intizom & Hosildorlik paneli:</b>\n"
            f"/discipline — Asosiy intizom menyusi\n"
            f"/today — Bugungi reja va vazifalar\n"
            f"/focus — Pomodoro fokus taymeri\n"
            f"/habits — Kundalik odatlar nazorati\n"
            f"/report — Kunlik hisobot\n"
            f"/score — Kunlik Nargis intizom bali\n\n"
            f"📥 <b>Nargis Inbox:</b>\n"
            f"/messages — Kelgan barcha xabarlar ro'yxati\n"
            f"/important — Muhim va tezkor murojaatlar\n"
            f"/contacts — Kontaktlar va leadlar bazasi\n\n"
            f"💡 <i>Menga istalgan savol, topshiriq yoki xarajatni to'g'ridan-to'g'ri yozishingiz mumkin!</i>"
        )


def make_claude_cli(settings: Settings) -> ClaudeCLI:
    """Factory to create ClaudeCLI instance with settings."""
    return ClaudeCLI(
        claude_bin=settings.claude_bin,
        workdir=settings.data_dir / "claude-run",
        model=settings.claude_model,
        effort=settings.claude_effort,
        timeout=settings.claude_timeout_seconds,
        oauth_token=settings.claude_oauth_token,
        auth_label=settings.claude_auth_mode,
    )


def make_ai_generator(settings: Settings) -> Any:
    """Factory to build the active AI generator (Gemini or Claude)."""
    if settings.ai_provider == "gemini":
        return GeminiClient(
            api_key=settings.gemini_api_key or "",
            model=settings.gemini_model,
            timeout=settings.claude_timeout_seconds,
        )
    return make_claude_cli(settings)


def build_assistant(settings: Settings, **kwargs: Any) -> Assistant:
    """Factory to build configured Assistant instance."""
    generator = kwargs.pop("generator", None) or make_ai_generator(settings)
    return Assistant(settings, generator, **kwargs)
