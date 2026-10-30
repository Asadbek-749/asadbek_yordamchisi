"""Dashboard aggregation and rendering."""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from finance.dashboard import collect_dashboard_data, render_dashboard_png
from finance.extractor import ExtractedTransactionItem as Item
from finance.service import FinanceService

TODAY = datetime.date(2026, 10, 15)


@pytest_asyncio.fixture
async def svc(tmp_path):
    await init_db(data_dir=tmp_path)
    service = FinanceService()
    items = [
        Item("EXPENSE", Decimal("45000"), "FOOD", "Tushlik", "UZS", TODAY),
        Item("EXPENSE", Decimal("15000"), "TRANSPORT", "Taksi", "UZS", TODAY),
        Item("EXPENSE", Decimal("30000"), "FOOD", "Kechki", "UZS", TODAY - datetime.timedelta(days=1)),
        Item("INCOME", Decimal("3000000"), "MAIN_JOB", "Oylik", "UZS", datetime.date(2026, 10, 5)),
        Item("EXPENSE", Decimal("100"), "SHOPPING", "Kitob", "USD", TODAY),
        Item("EXPENSE", Decimal("999"), "FOOD", "O'tgan oy", "UZS", datetime.date(2026, 9, 30)),
    ]
    created = await service.create_pending_transactions(7, items)
    for tx in created:
        if tx.description != "Kechki":  # leave one PENDING: it must not be counted
            await service.confirm_transaction(tx.id, 7)
    await service.set_monthly_budget(7, "2026-10", Decimal("1000000"))
    yield service
    await close_db()


@pytest.mark.asyncio
async def test_day_numbers_exclude_pending_and_other_currency(svc):
    d = await collect_dashboard_data(7, "day", today=TODAY)
    assert d.expense == Decimal("60000") and d.income == Decimal("0")
    assert d.other_currencies["USD"]["expense"] == Decimal("100")
    assert [c for c, _ in d.expense_categories] != []
    assert len(d.daily) == 7 and d.daily[-1][0] == TODAY  # last 7 days context


@pytest.mark.asyncio
async def test_month_numbers_budget_and_projection(svc):
    d = await collect_dashboard_data(7, "month", today=TODAY)
    assert d.income == Decimal("3000000") and d.expense == Decimal("60000")
    assert d.net == Decimal("2940000")
    assert d.avg_daily_expense == Decimal("4000")  # 60 000 / 15 days
    assert d.projected_expense == Decimal("124000")  # 4 000 * 31
    assert d.budget_limit == Decimal("1000000") and round(d.budget_pct) == 6
    assert d.top_day == (TODAY, Decimal("60000"))
    assert len(d.daily) == 31


@pytest.mark.asyncio
async def test_past_month_and_empty_period(svc):
    sept = await collect_dashboard_data(7, "month", today=TODAY, year=2026, month=9)
    assert sept.expense == Decimal("999") and sept.projected_expense == Decimal("999")
    empty = await collect_dashboard_data(7, "week", today=datetime.date(2026, 3, 4))
    assert empty.expense == 0 and empty.income == 0 and empty.top_day is None
    assert render_dashboard_png(empty)[:8] == b"\x89PNG\r\n\x1a\n"  # empty data still renders


@pytest.mark.asyncio
async def test_png_renders_for_all_periods(svc):
    for kind in ("day", "week", "month"):
        png = render_dashboard_png(await collect_dashboard_data(7, kind, today=TODAY))
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
