"""Unit tests for Communication Manager and Deleted Message Monitoring."""
from __future__ import annotations

import datetime
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db, get_session
from discipline.models import NargisMessage
from nargis.communication import CommunicationManager


@pytest_asyncio.fixture
async def comm_env(tmp_path):
    await init_db(data_dir=tmp_path)
    yield
    await close_db()


@pytest.mark.asyncio
async def test_first_and_repeated_external_message_protocol(comm_env):
    owner_id = 5146977644
    mgr = CommunicationManager(owner_id=owner_id)
    guest_id = 987654321

    # 1. First interaction: "Asadbek bormi?"
    r1 = await mgr.handle_external_message(
        sender_id=guest_id,
        sender_name="Dilshod Karimov",
        sender_username="dilshod_k",
        message_text="Asadbek bormi?",
        chat_id=guest_id,
        telegram_message_id=101,
    )
    assert "Men Asadbek ustozning AI agentiman — Nargis" in r1["reply_text"]
    assert "ish bilan bandlar" in r1["reply_text"]
    assert "Men Asadbekman" not in r1["reply_text"]

    # 2. Repeated message: vague continuation
    r2 = await mgr.handle_external_message(
        sender_id=guest_id,
        sender_name="Dilshod Karimov",
        sender_username="dilshod_k",
        message_text="Yaxshi, kutaman",
        chat_id=guest_id,
        telegram_message_id=102,
    )
    assert "qisqacha nima masalada bog‘lanayotganingizni yozib qoldiring" in r2["reply_text"]

    # 3. Repeated message with specific intent: Course lead
    r3 = await mgr.handle_external_message(
        sender_id=guest_id,
        sender_name="Dilshod Karimov",
        sender_username="dilshod_k",
        message_text="AI Pro kursi bo'yicha gaplashmoqchi edim",
        chat_id=guest_id,
        telegram_message_id=103,
    )
    assert r3["intent"] == "COURSE"
    assert r3["notify_owner"] is True
    assert "Ma'lumotni Asadbek ustozga yetkazdim" in r3["reply_text"]


@pytest.mark.asyncio
async def test_deleted_message_interception_and_owner_alert(comm_env):
    owner_id = 5146977644
    mgr = CommunicationManager(owner_id=owner_id)
    guest_id = 888777666

    # 1. Message received and preserved
    await mgr.handle_external_message(
        sender_id=guest_id,
        sender_name="Jasur Aliyev",
        sender_username="jasur_ai",
        message_text="Salom Asadbek, ertaga uchrashuv bekor qilindi",
        chat_id=guest_id,
        telegram_message_id=555,
        business_connection_id="conn_abc123",
    )

    # 2. User subsequently deletes message 555
    alerts = await mgr.record_deleted_message(
        chat_id=guest_id,
        message_ids=[555],
        business_connection_id="conn_abc123",
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["message_id"] == 555
    assert "@jasur_ai" in alert["sender"]
    assert "ertaga uchrashuv bekor qilindi" in alert["content"]
    assert "O'CHIRILGAN XABAR ANIQLANDI" in alert["preview_alert"]

    # Verify DB flag
    async with get_session() as session:
        from sqlalchemy import select
        res = await session.execute(select(NargisMessage).where(NargisMessage.telegram_message_id == 555))
        saved = res.scalar_one()
        assert saved.is_deleted is True
        assert saved.deleted_at is not None
        assert saved.status == "DELETED"
