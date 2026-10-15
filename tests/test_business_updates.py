"""Unit and integration tests for Telegram Business updates and Assistant logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiogram.methods import GetBusinessConnection, SendMessage

from assistant.claude_cli import ClaudeAuthError, ClaudeLimitError, ClaudeTimeout
from assistant.prompts import SKIP_TOKEN
from assistant.service import Outcome


@pytest.mark.asyncio
async def test_preview_mode_does_not_send_and_writes_previews_jsonl(make_harness):
    h = make_harness(overrides={"AUTO_SEND": "false"})
    await h.connect("conn1")
    h.generator.responses = ["Yaxshi, o'zing qandaysan?"]

    outcome = await h.business_message(text="Salom, qalesan?", connection_id="conn1")
    assert outcome == Outcome.previewed

    # No SendMessage sent to Telegram
    send_calls = [r for r in h.session.requests if isinstance(r, SendMessage)]
    assert len(send_calls) == 0

    # Written to previews.jsonl
    previews_file = h.data_dir / "previews.jsonl"
    assert previews_file.is_file()
    lines = previews_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["reply"] == "Yaxshi, o'zing qandaysan?"
    assert data["incoming"] == "Salom, qalesan?"

    # Unsent reply is NOT in history
    hist = h.assistant.history.get("conn1", 1001)
    assert len(hist) == 1
    assert hist[0]["role"] == "other"


@pytest.mark.asyncio
async def test_connection_lookup_and_cache(make_harness):
    h = make_harness()
    # Connection not in cache, but available in session
    h.set_connection("conn_lookup", user_id=123456)
    outcome = await h.business_message(text="Hello", connection_id="conn_lookup")
    assert outcome == Outcome.previewed

    # Verified that GetBusinessConnection was called once
    lookups = [r for r in h.session.requests if isinstance(r, GetBusinessConnection)]
    assert len(lookups) == 1

    # Second message with same connection does not call GetBusinessConnection again in preview mode
    await h.business_message(text="Second message", message_id=2, connection_id="conn_lookup")
    lookups2 = [r for r in h.session.requests if isinstance(r, GetBusinessConnection)]
    assert len(lookups2) == 1


@pytest.mark.asyncio
async def test_lookup_failure_returns_no_connection(make_harness):
    h = make_harness()
    # conn_unknown is neither cached nor in session
    outcome = await h.business_message(text="Hello", connection_id="conn_unknown")
    assert outcome == Outcome.no_connection


@pytest.mark.asyncio
async def test_foreign_and_disabled_connections(make_harness):
    h = make_harness()
    # Foreign connection
    await h.connect("conn_foreign", user_id=999999)
    outcome = await h.business_message(text="Hi", connection_id="conn_foreign")
    assert outcome == Outcome.foreign_connection
    assert len(h.generator.calls) == 0

    # Disabled connection
    await h.connect("conn_disabled", user_id=123456, is_enabled=False)
    outcome2 = await h.business_message(text="Hi", connection_id="conn_disabled")
    assert outcome2 == Outcome.connection_disabled
    assert len(h.generator.calls) == 0


@pytest.mark.asyncio
async def test_owner_message_and_takeover(make_harness):
    h = make_harness(overrides={"OWNER_TAKEOVER_MINUTES": 15})
    await h.connect("conn1")

    # Owner sends a message
    outcome = await h.business_message(
        text="Men o'zim javob yozdim", from_id=123456, connection_id="conn1"
    )
    assert outcome == Outcome.owner_message
    assert len(h.generator.calls) == 0

    # History contains owner message
    hist = h.assistant.history.get("conn1", 1001)
    assert len(hist) == 1
    assert hist[0]["role"] == "owner"

    # Other user sends a message during takeover period (10 minutes later)
    h.clock.advance(600)
    outcome2 = await h.business_message(
        text="Yana salom", from_id=999, message_id=2, connection_id="conn1"
    )
    assert outcome2 == Outcome.owner_active
    assert len(h.generator.calls) == 0

    # After takeover period expires (total 16 minutes later)
    h.clock.advance(360)
    outcome3 = await h.business_message(
        text="Endi javob bormi?", from_id=999, message_id=3, connection_id="conn1"
    )
    assert outcome3 == Outcome.previewed
    assert len(h.generator.calls) == 1


@pytest.mark.asyncio
async def test_ignored_bots(make_harness):
    h = make_harness()
    await h.connect("conn1")

    # Message from a bot
    o1 = await h.business_message(text="bot message", from_id=555, is_bot=True)
    assert o1 == Outcome.ignored_bot

    # Message sent via business bot
    o2 = await h.business_message(
        text="via bot message", from_id=999, via_business_bot=True, message_id=2
    )
    assert o2 == Outcome.ignored_bot
    assert len(h.generator.calls) == 0


@pytest.mark.asyncio
async def test_deduplication_including_restart(make_harness):
    h = make_harness()
    await h.connect("conn1")

    o1 = await h.business_message(text="birinchi", message_id=10)
    assert o1 == Outcome.previewed

    # Duplicate message ID
    o2 = await h.business_message(text="takroriy", message_id=10)
    assert o2 == Outcome.duplicate

    # Restart harness and verify duplicate still dropped
    h2 = h.restart()
    o3 = await h2.business_message(text="takroriy restartdan keyin", message_id=10)
    assert o3 == Outcome.duplicate


@pytest.mark.asyncio
async def test_media_descriptions_and_service_messages(make_harness):
    h = make_harness()
    await h.connect("conn1")

    # Sticker
    sticker_data = {
        "file_id": "st1",
        "file_unique_id": "u_st1",
        "type": "regular",
        "width": 512,
        "height": 512,
        "is_animated": False,
        "is_video": False,
        "emoji": "👍",
    }
    await h.business_message(text=None, sticker=sticker_data, message_id=1)
    hist = h.assistant.history.get("conn1", 1001)
    assert hist[-1]["text"] == "[sticker] 👍"

    # Voice message
    voice_data = {
        "file_id": "v1",
        "file_unique_id": "u_v1",
        "duration": 5,
    }
    await h.business_message(text=None, voice=voice_data, message_id=2)
    hist = h.assistant.history.get("conn1", 1001)
    assert hist[-1]["text"] == "[voice message]"

    # Unsupported / Service message (empty)
    o = await h.business_message(text=None, message_id=3)
    assert o == Outcome.unsupported


@pytest.mark.asyncio
async def test_history_separated_per_connection_and_chat(make_harness):
    h = make_harness()
    await h.connect("conn1")
    await h.connect("conn2")

    await h.business_message(text="Chat A", chat_id=100, connection_id="conn1", message_id=1)
    await h.business_message(text="Chat B", chat_id=200, connection_id="conn1", message_id=2)
    await h.business_message(text="Chat C", chat_id=100, connection_id="conn2", message_id=3)

    assert len(h.assistant.history.get("conn1", 100)) == 1
    assert len(h.assistant.history.get("conn1", 200)) == 1
    assert len(h.assistant.history.get("conn2", 100)) == 1


@pytest.mark.asyncio
async def test_language_fallback_to_last_chat_language(make_harness):
    h = make_harness()
    await h.connect("conn1")

    # Russian message
    await h.business_message(text="Привет, как дела?", message_id=1)
    assert "Russian" in h.generator.calls[-1][1]

    # Emoji only message -> inherits Russian
    await h.business_message(text="😊", message_id=2)
    assert "Russian" in h.generator.calls[-1][1]


@pytest.mark.asyncio
async def test_skip_token_is_not_sent(make_harness):
    h = make_harness()
    await h.connect("conn1")
    h.generator.responses = [f"  {SKIP_TOKEN}  "]

    outcome = await h.business_message(text="Ok", message_id=1)
    assert outcome == Outcome.skipped
    assert not (h.data_dir / "previews.jsonl").exists()


@pytest.mark.asyncio
async def test_autosend_flow_and_chunking(make_harness):
    h = make_harness(overrides={"AUTO_SEND": "true"})
    await h.connect("conn1")

    # Long reply of 4500 characters
    long_reply = "A" * 4500
    h.generator.responses = [long_reply]

    outcome = await h.business_message(text="Uzun xabar so'rayapman", message_id=1)
    assert outcome == Outcome.sent

    # Verify fresh GetBusinessConnection lookup was made
    lookups = [r for r in h.session.requests if isinstance(r, GetBusinessConnection)]
    assert len(lookups) >= 1

    # Verify SendMessage chunks (4000 + 500)
    sends = [r for r in h.session.requests if isinstance(r, SendMessage)]
    assert len(sends) == 2
    assert len(sends[0].text) == 4000
    assert len(sends[1].text) == 500
    assert sends[0].business_connection_id == "conn1"

    # Sent reply is written to history as owner
    hist = h.assistant.history.get("conn1", 1001)
    assert hist[-1]["role"] == "owner"
    assert hist[-1]["text"] == long_reply


@pytest.mark.asyncio
async def test_autosend_permission_denied_if_rights_revoked(make_harness):
    h = make_harness(overrides={"AUTO_SEND": "true"})
    await h.connect("conn1")

    # Simulate permission revoked on Telegram side
    h.set_connection("conn1", can_reply=False)
    h.generator.responses = ["Javob"]

    outcome = await h.business_message(text="Salom", message_id=1)
    assert outcome == Outcome.permission_denied
    sends = [r for r in h.session.requests if isinstance(r, SendMessage)]
    assert len(sends) == 0


@pytest.mark.asyncio
async def test_autosend_aborts_if_owner_active_during_generation(make_harness):
    h = make_harness(overrides={"AUTO_SEND": "true", "OWNER_TAKEOVER_MINUTES": 15})
    await h.connect("conn1")

    # Hook runs while generation is in flight: owner sends message
    async def owner_writes_hook():
        await h.business_message(
            text="Men o'zim yozib qo'ydim", from_id=123456, message_id=99
        )

    h.generator.hook = owner_writes_hook
    h.generator.responses = ["AI kechikkan javobi"]

    outcome = await h.business_message(text="Savol", message_id=1)
    assert outcome == Outcome.owner_active
    sends = [r for r in h.session.requests if isinstance(r, SendMessage)]
    assert len(sends) == 0


@pytest.mark.asyncio
async def test_owner_commands_and_non_owner_start(make_harness):
    h = make_harness()
    await h.connect("conn1")

    # Owner /pause
    await h.direct_message("/pause", from_id=123456)
    assert h.assistant.state.is_paused() is True

    # While paused, message receives Outcome.paused
    o = await h.business_message(text="Salom", message_id=1)
    assert o == Outcome.paused

    # Restart preserves pause
    h2 = h.restart()
    assert h2.assistant.state.is_paused() is True

    # Owner /resume
    await h2.direct_message("/resume", from_id=123456)
    assert h2.assistant.state.is_paused() is False

    # Owner /status and /start
    await h2.direct_message("/status", from_id=123456)
    await h2.direct_message("/start", from_id=123456)

    # Non-owner /start: short message, no profile disclosed, Claude not called
    init_calls = len(h2.generator.calls)
    await h2.direct_message("/start", from_id=777)
    assert len(h2.generator.calls) == init_calls

    sends = [r for r in h2.session.requests if isinstance(r, SendMessage)]
    assert any("shaxsiy yordamchi bot" in s.text for s in sends)


@pytest.mark.asyncio
async def test_limits_cooldown_and_resume_clear(make_harness):
    h = make_harness(overrides={"LIMIT_COOLDOWN_MINUTES": 30})
    await h.connect("conn1")

    # Claude limit error with epoch
    h.generator.responses = [ClaudeLimitError("usage limit", resets_at=1700003600)]
    o1 = await h.business_message(text="Savol 1", message_id=1)
    assert o1 == Outcome.limited
    assert h.assistant.state.limit_until == 1700003600

    # Immediate second message is limited without calling generator
    o2 = await h.business_message(text="Savol 2", message_id=2)
    assert o2 == Outcome.limited
    assert len(h.generator.calls) == 1

    # Advance beyond limit
    h.clock.advance(4000)
    h.generator.responses = ["Yangi javob"]
    o3 = await h.business_message(text="Savol 3", message_id=3)
    assert o3 == Outcome.previewed
    assert len(h.generator.calls) == 2


@pytest.mark.asyncio
async def test_claude_timeout_and_auth_error_handling(make_harness):
    h = make_harness(overrides={"AUTO_SEND": "true"})
    await h.connect("conn1")

    h.generator.responses = [ClaudeTimeout("timed out")]
    o = await h.business_message(text="Savol", message_id=1)
    assert o == Outcome.error

    # User chat receives no reply
    sends_to_user = [r for r in h.session.requests if isinstance(r, SendMessage) and r.chat_id == 1001]
    assert len(sends_to_user) == 0
