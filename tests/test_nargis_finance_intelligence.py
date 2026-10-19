"""Unit tests for Finance Intelligence Engine: variance analysis, spike detection, and backend math."""
from __future__ import annotations

import datetime
from decimal import Decimal
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db, get_session
from finance.intelligence import FinanceIntelligenceEngine
from finance.models import FinancialTransaction


@pytest_asyncio.fixture
async def fin_intel_env(tmp_path):
    await init_db(data_dir=tmp_path)
    yield
    await close_db()


@pytest.mark.asyncio
async def test_weekly_spending_variance_66_percent_spike(fin_intel_env):
    owner_id = 5146977644
    engine = FinanceIntelligenceEngine(owner_id=owner_id)

    # Reference date: Wednesday 2026-10-21
    ref_date = datetime.date(2026, 10, 21)

    # Previous week: Monday 2026-10-12 to Wednesday 2026-10-14 -> total 300,000 UZS
    # Current week: Monday 2026-10-19 to Wednesday 2026-10-21 -> total 500,000 UZS
    async with get_session() as session:
        # Prev week transactions
        t_prev = FinancialTransaction(
            owner_id=owner_id,
            type="EXPENSE",
            category_name="FOOD",
            amount=Decimal("300000.00"),
            currency="UZS",
            transaction_date=datetime.date(2026, 10, 13),
            status="CONFIRMED",
        )
        # Current week transactions (300k Food + 200k Shopping)
        t_curr1 = FinancialTransaction(
            owner_id=owner_id,
            type="EXPENSE",
            category_name="FOOD",
            amount=Decimal("300000.00"),
            currency="UZS",
            transaction_date=datetime.date(2026, 10, 19),
            status="CONFIRMED",
        )
        t_curr2 = FinancialTransaction(
            owner_id=owner_id,
            type="EXPENSE",
            category_name="SHOPPING",
            amount=Decimal("200000.00"),
            currency="UZS",
            transaction_date=datetime.date(2026, 10, 20),
            status="CONFIRMED",
        )
        session.add_all([t_prev, t_curr1, t_curr2])
        await session.commit()

    analysis = await engine.compare_weekly_spending(reference_date=ref_date)
    assert analysis["previous_total"] == Decimal("300000.00")
    assert analysis["current_total"] == Decimal("500000.00")
    assert analysis["pct_change"] == Decimal("66.7")
    assert analysis["has_spike"] is True
    assert analysis["biggest_spike_category"] == "SHOPPING"

    msg = engine.generate_variance_message(analysis)
    assert msg is not None
    assert "66.7%" in msg
    assert "500 000 so'm" in msg
    assert "300 000 so'm" in msg
    assert "SHOPPING" in msg
    assert "Xarajat oshishining asosiy sababi nima?" in msg
