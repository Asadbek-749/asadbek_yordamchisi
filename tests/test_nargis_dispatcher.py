"""Tests for Nargis Message Dispatcher system."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from discipline.db import close_db, init_db
from discipline.models import NargisContact
from nargis.dispatcher import (
    NargisMessageDispatcher,
    clean_uzbek_recipient,
    get_message_dispatcher,
)
from nargis.service import NargisContactService


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path):
    await init_db(data_dir=tmp_path)
    yield
    await close_db()


def test_clean_uzbek_recipient():
    assert clean_uzbek_recipient("Oyattulaga") == "Oyattula"
    assert clean_uzbek_recipient("Oyattulloga") == "Oyattullo"
    assert clean_uzbek_recipient("Aliga") == "Ali"
    assert clean_uzbek_recipient("Azizbekka") == "Azizbek"
    assert clean_uzbek_recipient("Ulug'bekka") == "Ulug'bek"
    assert clean_uzbek_recipient("Oyattullo akaga") == "Oyattullo"
    assert clean_uzbek_recipient("@oyatullo_dev") == "@oyatullo_dev"
    assert clean_uzbek_recipient("Shahzodga,") == "Shahzod"


@pytest.mark.asyncio
async def test_parse_command_send():
    dispatcher = NargisMessageDispatcher()
    owner_id = 5146977644

    # Pattern A: "Oyattulaga ayting, kechki soat 6 dagi gruppamga menga dars o'tib bersin."
    cmd = await dispatcher.parse_command(
        "Oyattulaga ayting, kechki soat 6 dagi gruppamga menga dars o'tib bersin.",
        owner_id=owner_id,
    )
    assert cmd.action == "SEND"
    assert cmd.recipient == "Oyattula"
    assert "dars o'tib bersin" in cmd.message

    # Pattern B: "Aliga yoz ertaga uchrashuv soat 10 da"
    cmd2 = await dispatcher.parse_command(
        "Aliga yoz ertaga uchrashuv soat 10 da",
        owner_id=owner_id,
    )
    assert cmd2.action == "SEND"
    assert cmd2.recipient == "Ali"
    assert "uchrashuv soat 10 da" in cmd2.message

    # Pattern C: "Oyattulaga dars qoldirildi deb ayting"
    cmd3 = await dispatcher.parse_command(
        "Oyattulaga dars qoldirildi deb ayting",
        owner_id=owner_id,
    )
    assert cmd3.action == "SEND"
    assert cmd3.recipient == "Oyattula"
    assert cmd3.message == "dars qoldirildi"

    # Pattern D: /msg
    cmd4 = await dispatcher.parse_command(
        "/msg Oyattullo darsga kechikmang",
        owner_id=owner_id,
    )
    assert cmd4.action == "SEND"
    assert cmd4.recipient == "Oyattullo"
    assert cmd4.message == "darsga kechikmang"


@pytest.mark.asyncio
async def test_parse_command_confirm_and_status():
    dispatcher = NargisMessageDispatcher()
    owner_id = 5146977644

    # Status without pending
    cmd_stat = await dispatcher.parse_command("Aloo jo'natdingmi ?", owner_id=owner_id)
    assert cmd_stat.action == "STATUS"

    cmd_stat2 = await dispatcher.parse_command("Jo'natdingmi?", owner_id=owner_id)
    assert cmd_stat2.action == "STATUS"

    # Cancel
    cmd_cancel = await dispatcher.parse_command("Bekor qil", owner_id=owner_id)
    assert cmd_cancel.action == "CANCEL"


@pytest.mark.asyncio
async def test_dispatcher_flow_contact_found():
    contact_svc = NargisContactService()
    # 1. Create contact
    contact = await contact_svc.add_or_update_contact(
        first_name="Oyattullo",
        telegram_id=987654321,
        username="oyatullo_dev",
    )
    assert contact.id is not None

    dispatcher = NargisMessageDispatcher(contact_service=contact_svc)
    owner_id = 5146977644

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))

    mock_msg = MagicMock()
    mock_msg.answer = AsyncMock()

    # Step 1: Owner gives voice instruction
    text = "Oyattulaga ayting, kechki soat 6 dagi gruppamga menga dars o'tib bersin."
    handled = await dispatcher.handle_owner_message(
        message=mock_msg,
        text=text,
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled is True
    assert mock_msg.answer.called
    ans_text = mock_msg.answer.call_args[0][0]
    assert "XABAR YUBORISHNI TASDIQLASH" in ans_text
    assert "Oyattullo" in ans_text

    # Step 2: Owner says "Jo'nat"
    mock_msg.answer.reset_mock()
    handled2 = await dispatcher.handle_owner_message(
        message=mock_msg,
        text="Jo'nat",
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled2 is True
    assert mock_bot.send_message.called
    # Check that message was physically sent to recipient
    call_kwargs = mock_bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 987654321
    assert "Asadbek Ustozdan xabar" in call_kwargs["text"]
    assert "dars o'tib bersin" in call_kwargs["text"]

    report_text = mock_msg.answer.call_args[0][0]
    assert "XABAR MUVAFFAQIYATLI YUBORILDI" in report_text

    # Step 3: Owner asks "Aloo jo'natdingmi ?"
    mock_msg.answer.reset_mock()
    handled3 = await dispatcher.handle_owner_message(
        message=mock_msg,
        text="Aloo jo'natdingmi ?",
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled3 is True
    status_text = mock_msg.answer.call_args[0][0]
    assert "Ha, Asadbek ustoz!" in status_text
    assert "muvaffaqiyatli yetkazilgan" in status_text


@pytest.mark.asyncio
async def test_dispatcher_flow_contact_missing_then_provided():
    contact_svc = NargisContactService()
    dispatcher = NargisMessageDispatcher(contact_service=contact_svc)
    owner_id = 5146977644

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=666))
    mock_msg = MagicMock()
    mock_msg.answer = AsyncMock()

    # Step 1: Owner says message to missing contact "Oyattula"
    text = "Oyattulaga ayting, kechki soat 6 dagi gruppamga menga dars o'tib bersin."
    handled = await dispatcher.handle_owner_message(
        message=mock_msg,
        text=text,
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled is True
    ans_text = mock_msg.answer.call_args[0][0]
    assert "topilmadi" in ans_text
    assert "Saqlangan qoralama" in ans_text

    # Step 2: Owner provides username "@oyatullo_dev"
    mock_msg.answer.reset_mock()
    handled_user = await dispatcher.handle_owner_message(
        message=mock_msg,
        text="@oyatullo_dev",
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled_user is True
    ans_user = mock_msg.answer.call_args[0][0]
    assert "Kontakt bog'landi" in ans_user

    # Step 3: Owner corrects text: "Yo'q unga shu xabarimni yetkaz: dars soat 19:00 ga ko'chirildi"
    mock_msg.answer.reset_mock()
    handled_corr = await dispatcher.handle_owner_message(
        message=mock_msg,
        text="Yo'q unga shu xabarimni yetkaz: dars soat 19:00 ga ko'chirildi",
        owner_id=owner_id,
        bot=mock_bot,
    )
    assert handled_corr is True
    draft = dispatcher.get_pending_draft(owner_id)
    assert draft.message_text == "dars soat 19:00 ga ko'chirildi"
