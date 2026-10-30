"""Planner: input parsing, database layer, plan inputs and the Telegram commands end to end."""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Update

from discipline.db import close_db, init_db
from finance.extractor import ExtractedTransactionItem as Item
from finance.handlers import build_finance_router
from finance.planner_handlers import split_message
from finance.planner_service import PlannerService
from finance.planner_text import (
    ParseError,
    format_plan,
    parse_adddebt,
    parse_addincome,
    parse_editdebt,
)
from finance.service import FinanceService
from tests.conftest import RecordingSession

D = Decimal
OWNER = 5146977644
TODAY = datetime.date(2026, 10, 15)


# ── parsing ────────────────────────────────────────────────────────────────

def test_parse_addincome():
    assert parse_addincome("5 Oylik 6 million") == {
        "title": "Oylik", "amount": D(6_000_000), "pay_day": 5, "currency": "UZS"}
    assert parse_addincome("31 Freelance 500 dollar")["currency"] == "USD"
    for bad in ("", "5", "40 Oylik 6 million", "5 Oylik"):
        with pytest.raises(ParseError):
            parse_addincome(bad)


def test_parse_adddebt_variants():
    d = parse_adddebt("Kapitalbank kredit | 24 million | 1,2 million | 28 | 15")
    assert (d["balance"], d["monthly_payment"], d["annual_rate_pct"], d["pay_day"], d["kind"]) == (
        D(24_000_000), D(1_200_000), D(28), 15, "BANK_CREDIT")

    d = parse_adddebt("Do'stimdan qarz | 3 million | 500 ming | 0 | 1")
    assert d["kind"] == "PERSONAL" and d["monthly_payment"] == D(500_000)

    d = parse_adddebt("Telefon nasiya | 6 million | 500 ming")  # rate and day are optional
    assert d["kind"] == "INSTALLMENT" and d["annual_rate_pct"] == 0 and d["pay_day"] == 1

    d = parse_adddebt("Avto | 5000 dollar | 300 | 12 | 5 | kredit")  # bare payment inherits USD
    assert d["currency"] == "USD" and d["monthly_payment"] == D(300) and d["kind"] == "BANK_CREDIT"

    for bad in ("", "Kredit | 24 million", "Kredit | abc | 1 million", "K | 1 million | 100 ming | 400",
                "K | 1 million | 100 ming | 10 | 40", "K | 1 million | 100 ming | 10 | 5 | xyz"):
        with pytest.raises(ParseError):
            parse_adddebt(bad)


def test_parse_editdebt():
    assert parse_editdebt("3 qoldiq 18 million") == (3, "balance", D(18_000_000))
    assert parse_editdebt("#3 tolov 900 ming") == (3, "monthly_payment", D(900_000))
    assert parse_editdebt("3 foiz 25") == (3, "annual_rate_pct", D(25))
    assert parse_editdebt("3 kun 20") == (3, "pay_day", 20)
    assert parse_editdebt("3 nom Yangi nom") == (3, "title", "Yangi nom")
    with pytest.raises(ParseError):
        parse_editdebt("3 rang qizil")


def test_split_message_respects_limit():
    text = "\n".join(f"qator {i} " + "x" * 80 for i in range(200))
    parts = split_message(text, 3800)
    assert len(parts) > 1 and all(len(p) <= 3800 for p in parts) and "\n".join(parts) == text


# ── database layer ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def planner(tmp_path):
    await init_db(data_dir=tmp_path)
    yield PlannerService()
    await close_db()


@pytest.mark.asyncio
async def test_repayment_splits_interest_and_principal_and_books_expense(planner):
    debt = await planner.add_debt(OWNER, "Kredit", D(12_000_000), D(1_000_000), D(24), 15)
    res = await planner.pay_debt(OWNER, debt.id, D(1_000_000), TODAY)

    assert res["interest"] == D(240_000) and res["principal"] == D(760_000)  # 2% a month of 12 mln
    assert res["new_balance"] == D(11_240_000) and res["closed"] is False
    tx = res["transaction"]
    assert (tx.type, tx.status, tx.source, tx.amount) == ("EXPENSE", "CONFIRMED", "debt", D(1_000_000))
    assert "Qarz" in tx.category_name

    res = await planner.pay_debt(OWNER, debt.id, D(50_000_000), TODAY)  # more than is owed
    assert res["closed"] is True and res["new_balance"] == 0
    assert await planner.list_debts(OWNER) == []
    assert len(await planner.list_debts(OWNER, include_closed=True)) == 1


@pytest.mark.asyncio
async def test_zero_interest_payment_is_all_principal(planner):
    debt = await planner.add_debt(OWNER, "Do'st", D(3_000_000), D(500_000), D(0), 1, kind="PERSONAL")
    res = await planner.pay_debt(OWNER, debt.id, D(500_000), TODAY)
    assert res["interest"] == 0 and res["new_balance"] == D(2_500_000)


@pytest.mark.asyncio
async def test_other_owners_cannot_touch_my_data_and_validation(planner):
    debt = await planner.add_debt(OWNER, "Kredit", D(1_000_000), D(100_000), D(10), 5)
    inc = await planner.add_income(OWNER, "Oylik", D(5_000_000), 5)

    assert await planner.pay_debt(OWNER + 1, debt.id, D(1)) is None
    assert await planner.update_debt(OWNER + 1, debt.id, balance=D(1)) is None
    assert await planner.delete_debt(OWNER + 1, debt.id) is False
    assert await planner.delete_income(OWNER + 1, inc.id) is False
    assert await planner.list_debts(OWNER + 1) == [] and await planner.list_incomes(OWNER + 1) == []

    with pytest.raises(ValueError):
        await planner.add_debt(OWNER, "X", D(0), D(1), D(0), 1)
    with pytest.raises(ValueError):
        await planner.add_income(OWNER, "X", D(1), 32)
    with pytest.raises(ValueError):
        await planner.update_debt(OWNER, debt.id, hacked="yes")

    row = await planner.update_debt(OWNER, debt.id, balance=D(800_000), pay_day=20)
    assert row.balance == D(800_000) and row.pay_day == 20
    assert await planner.delete_debt(OWNER, debt.id) is True
    assert await planner.delete_debt(OWNER, debt.id) is False


@pytest.mark.asyncio
async def test_plan_inputs_use_history_but_not_double_counted_items(planner):
    fin = planner.finance
    await planner.add_income(OWNER, "Oylik", D(6_000_000), 5)
    await planner.add_debt(OWNER, "Kredit", D(5_000_000), D(300_000), D(24), 20)
    await fin.add_recurring(OWNER, "Ijara", D(1_500_000), 1, category_name="HOME")

    sept, aug = datetime.date(2026, 9, 10), datetime.date(2026, 8, 10)
    items = [
        Item("EXPENSE", D(2_000_000), "FOOD", "Sentyabr sarf", "UZS", sept),
        Item("EXPENSE", D(1_000_000), "FOOD", "Avgust sarf", "UZS", aug),
        Item("EXPENSE", D(900_000), "DEBT", "Kredit to'lovi", "UZS", sept),  # counted as a debt, not as spending
        Item("INCOME", D(6_000_000), "MAIN_JOB", "Oylik", "UZS", sept),  # income is never spending
    ]
    for tx in await fin.create_pending_transactions(OWNER, items):
        await fin.confirm_transaction(tx.id, OWNER)
    await fin.create_pending_transactions(OWNER, [Item("EXPENSE", D(1_500_000), "HOME", "Ijara", "UZS", sept)], source="recurring")
    await fin.confirm_all_pending(OWNER, OWNER)  # the recurring-sourced expense must not count either

    inputs = await planner.load_inputs(OWNER, TODAY)
    assert inputs.variable_avg == D(1_500_000) and inputs.variable_months == 2  # (2.0 + 1.0) / 2
    assert [i.amount for i in inputs.incomes] == [D(6_000_000)]
    assert [f.amount for f in inputs.fixed] == [D(1_500_000)]
    assert [(d.balance, d.monthly_payment) for d in inputs.debts] == [(D(5_000_000), D(300_000))]

    plan = await planner.build_plan(OWNER, TODAY)
    assert plan.free == D(6_000_000) - D(300_000) - D(1_500_000) - D(1_500_000)  # 2 700 000
    assert plan.warnings == []


@pytest.mark.asyncio
async def test_dollar_items_need_a_rate(planner):
    await planner.add_debt(OWNER, "Avto", D(5000), D(300), D(12), 5, currency="USD")
    inputs = await planner.load_inputs(OWNER, TODAY)
    assert inputs.debts == [] and any("kurs belgilanmagan" in n for n in inputs.notes)

    await planner.finance.set_usd_rate(OWNER, D(12_600))
    inputs = await planner.load_inputs(OWNER, TODAY)
    assert inputs.debts[0].balance == D(63_000_000) and inputs.debts[0].monthly_payment == D(3_780_000)
    assert inputs.notes == []


@pytest.mark.asyncio
async def test_plan_text_escapes_user_input(planner):
    await planner.add_income(OWNER, "<b>Oylik</b>", D(6_000_000), 5)
    await planner.add_debt(OWNER, "<i>Kredit</i>", D(5_000_000), D(300_000), D(24), 20)
    text = format_plan(await planner.build_plan(OWNER, TODAY))
    assert "<i>Kredit</i>" not in text and "&lt;i&gt;Kredit&lt;/i&gt;" in text
    assert "OYLIK MOLIYAVIY REJA" in text and "shaxsiy moliyaviy maslahat emas" in text
    assert len(text) < 8000


# ── Telegram commands, end to end ──────────────────────────────────────────

@pytest_asyncio.fixture
async def bot_env(tmp_path):
    await init_db(data_dir=tmp_path)
    session = RecordingSession()
    bot = Bot(token="123456:ABC-DEF", session=session)
    dp = Dispatcher()
    dp.include_router(build_finance_router(FinanceService(), OWNER))
    counter = {"n": 0}

    async def say(text: str, user_id: int = OWNER) -> list[str]:
        counter["n"] += 1
        command = text.split()[0]
        update = Update.model_validate({
            "update_id": counter["n"],
            "message": {
                "message_id": counter["n"], "date": 1700000000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "U"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}],
            },
        }, context={"bot": bot})
        before = len(session.requests)
        await dp.feed_update(bot, update)
        return [r.text for r in session.requests[before:] if isinstance(r, SendMessage)]

    yield say
    await close_db()


@pytest.mark.asyncio
async def test_commands_end_to_end(bot_env):
    say = bot_env

    out = await say("/plan")
    assert "/addincome" in out[0] and "/adddebt" in out[0]  # onboarding hint when empty

    assert "Daromad qo'shildi" in (await say("/addincome 5 Oylik 6 million"))[0]
    out = await say("/adddebt Kapitalbank kredit | 24 million | 1,2 million | 28 | 15")
    assert "Qarz qo'shildi" in out[0] and "yopiladi" in out[0]
    assert "Qarz qo'shildi" in (await say("/adddebt Do'stimdan qarz | 3 million | 500 ming | 0 | 1"))[0]
    assert "/adddebt nom" in (await say("/adddebt xato"))[0]  # usage on bad input

    out = await say("/debts")
    assert "Kapitalbank kredit" in out[0] and "27 000 000" in out[0]  # total owed

    out = await say("/plan")
    assert "OYLIK MOLIYAVIY REJA" in out[0] and "Erkin pul" in out[0] and "Avalanche" in " ".join(out)

    assert "foiz" in (await say("/whatif 800 ming"))[0]
    assert "To'lov qayd etildi" in (await say("/paydebt 2 500 ming"))[0]
    assert "Yangilandi" in (await say("/editdebt 1 qoldiq 20 million"))[0]
    assert "olib tashlandi" in (await say("/deldebt 2"))[0]
    assert "o'chirildi" in (await say("/delincome 1"))[0].lower()


@pytest.mark.asyncio
async def test_commands_are_owner_only(bot_env):
    say = bot_env
    for cmd in ("/plan", "/debts", "/adddebt X | 1 million | 100 ming", "/addincome 5 Oylik 6 million", "/paydebt 1 100 ming"):
        out = await say(cmd, user_id=OWNER + 7)
        assert out and "Kirish taqiqlangan" in out[0], cmd
