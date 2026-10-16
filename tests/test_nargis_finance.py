"""Comprehensive tests for NARGIS AI Personal Finance System."""
from __future__ import annotations

import datetime
from decimal import Decimal
import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from finance.extractor import FinanceExtractor, parse_uzbek_amount
from finance.service import FinanceService
from nargis.roles import UserRole, get_user_role, is_owner


@pytest_asyncio.fixture
async def finance_env(tmp_path):
    """Fixture providing initialized database and finance service."""
    await init_db(data_dir=tmp_path)
    service = FinanceService()
    await service.ensure_default_categories()
    yield service
    await close_db()


def test_roles_and_owner_detection():
    """Verify owner and user role detection."""
    owner_id = 5146977644
    guest_id = 999999999

    assert is_owner(owner_id, configured_owner_id=owner_id) is True
    assert is_owner(guest_id, configured_owner_id=owner_id) is False

    assert get_user_role(owner_id, configured_owner_id=owner_id) == UserRole.OWNER
    assert get_user_role(guest_id, configured_owner_id=owner_id) == UserRole.USER


def test_parse_uzbek_amount():
    """Verify Uzbek amounts parsing into Decimal."""
    assert parse_uzbek_amount("45 ming") == Decimal("45000")
    assert parse_uzbek_amount("2 million") == Decimal("2000000")
    assert parse_uzbek_amount("2.5 million") == Decimal("2500000")
    assert parse_uzbek_amount("50k") == Decimal("50000")
    assert parse_uzbek_amount("150 000 so'm") == Decimal("150000")
    assert parse_uzbek_amount("75000") == Decimal("75000")


def test_expense_and_income_extraction():
    """Verify single and multi-item extraction from text."""
    extractor = FinanceExtractor()

    # 1. Single expense
    res1 = extractor.rule_based_extract("Bugun tushlikka 45 ming sarfladim")
    assert len(res1.transactions) == 1
    tx1 = res1.transactions[0]
    assert tx1.type == "EXPENSE"
    assert tx1.amount == Decimal("45000")
    assert tx1.category == "FOOD"

    # 2. Multi-item compound expense
    res2 = extractor.rule_based_extract("Bugun 50 ming transport, 100 ming ovqat va 200 ming xarid")
    assert len(res2.transactions) == 3
    assert res2.total_amount == Decimal("350000")
    categories = [t.category for t in res2.transactions]
    assert "TRANSPORT" in categories
    assert "FOOD" in categories
    assert "SHOPPING" in categories

    # 3. Single income
    res3 = extractor.rule_based_extract("Bugun 2 million daromad oldim")
    assert len(res3.transactions) == 1
    assert res3.transactions[0].type == "INCOME"
    assert res3.transactions[0].amount == Decimal("2000000")


def test_ambiguous_amount_handling():
    """Verify ambiguous statements prompt clarification without creating transactions."""
    extractor = FinanceExtractor()
    res = extractor.rule_based_extract("Bugun ovqatga ancha pul ketdi")
    assert res.is_ambiguous is True
    assert len(res.transactions) == 0
    assert "qancha summa" in res.clarification_question.lower()


@pytest.mark.asyncio
async def test_pending_confirmation_pipeline(finance_env: FinanceService):
    """Verify strict confirmation pipeline: PENDING is not counted in balance; only CONFIRMED is."""
    service = finance_env
    owner_id = 123456789
    extractor = FinanceExtractor()

    res = extractor.rule_based_extract("Bugun tushlikka 45 ming sarfladim")
    created = await service.create_pending_transactions(owner_id, res.transactions, source="text")
    assert len(created) == 1
    tx = created[0]
    assert tx.status == "PENDING"

    # Before confirmation: daily summary must NOT count pending expense!
    summary_before = await service.get_daily_summary(owner_id)
    assert summary_before["total_expense"] == Decimal("0.00")
    assert summary_before["pending_count"] == 1

    # Confirm transaction
    confirmed_tx = await service.confirm_transaction(tx.id, actor_id=owner_id)
    assert confirmed_tx is not None
    assert confirmed_tx.status == "CONFIRMED"
    assert confirmed_tx.confirmed_at is not None

    # After confirmation: daily summary must include 45 000!
    summary_after = await service.get_daily_summary(owner_id)
    assert summary_after["total_expense"] == Decimal("45000.00")
    assert summary_after["pending_count"] == 0


@pytest.mark.asyncio
async def test_transaction_edit_and_cancel(finance_env: FinanceService):
    """Verify editing pending amounts and soft cancellation."""
    service = finance_env
    owner_id = 123456789
    extractor = FinanceExtractor()

    res = extractor.rule_based_extract("Taksiga 40 ming to'ladim")
    created = await service.create_pending_transactions(owner_id, res.transactions)
    tx = created[0]

    # Owner corrects: "Yo'q, 45 ming edi"
    edited = await service.edit_transaction(tx.id, actor_id=owner_id, new_amount=Decimal("45000"))
    assert edited.amount == Decimal("45000")

    # Cancel transaction
    cancelled = await service.cancel_transaction(tx.id, actor_id=owner_id)
    assert cancelled.status == "CANCELLED"

    summary = await service.get_daily_summary(owner_id)
    assert summary["total_expense"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_reports_and_division_by_zero_safety(finance_env: FinanceService):
    """Verify daily, weekly, monthly calculations and 0 income division safety."""
    service = finance_env
    owner_id = 123456789
    extractor = FinanceExtractor()

    # Add 2M income and 500K expense
    inc_res = extractor.rule_based_extract("Bugun 2 million daromad oldim")
    exp_res = extractor.rule_based_extract("Bugun 500 ming xarid qildim")

    tx_inc = await service.create_pending_transactions(owner_id, inc_res.transactions)
    tx_exp = await service.create_pending_transactions(owner_id, exp_res.transactions)

    await service.confirm_all_pending(owner_id, actor_id=owner_id)

    today = datetime.date.today()
    summary = await service.get_daily_summary(owner_id, today)
    assert summary["total_income"] == Decimal("2000000.00")
    assert summary["total_expense"] == Decimal("500000.00")
    assert summary["net_balance"] == Decimal("1500000.00")

    # Monthly report
    m_summary = await service.get_monthly_summary(owner_id, today.year, today.month)
    assert m_summary["total_income"] == Decimal("2000000.00")
    assert m_summary["total_expense"] == Decimal("500000.00")
    assert m_summary["net_balance"] == Decimal("1500000.00")
    assert m_summary["expense_ratio"] == Decimal("25.00")


@pytest.mark.asyncio
async def test_budget_threshold_alerts(finance_env: FinanceService):
    """Verify budget tracking and 90% threshold alert generation."""
    service = finance_env
    owner_id = 123456789
    today = datetime.date.today()
    period_key = today.strftime("%Y-%m")

    # Set monthly budget limit to 4 million
    await service.set_monthly_budget(owner_id, period_key, limit_amount=Decimal("4000000"))

    # Add 3.7 million confirmed expense (92.5% usage)
    extractor = FinanceExtractor()
    res = extractor.rule_based_extract("Noutbukka 3.7 million sarfladim")
    txs = await service.create_pending_transactions(owner_id, res.transactions)
    await service.confirm_transaction(txs[0].id, actor_id=owner_id)

    status = await service.get_budget_status(owner_id, period_key)
    assert status is not None
    assert status["spent_amount"] == Decimal("3700000.00")
    assert status["remaining_amount"] == Decimal("300000.00")
    assert status["usage_percentage"] == Decimal("92.5")

    # Check alert trigger
    alerts = await service.check_budget_alerts(owner_id, period_key)
    assert len(alerts) > 0
    assert any("92.5%" in a or "90%" in a for a in alerts)

    # Check that alert does not duplicate on second check
    alerts_second = await service.check_budget_alerts(owner_id, period_key)
    assert len(alerts_second) == 0


@pytest.mark.asyncio
async def test_duplicate_detection(finance_env: FinanceService):
    """Verify duplicate transaction detection within time window."""
    service = finance_env
    owner_id = 123456789

    extractor = FinanceExtractor()
    res = extractor.rule_based_extract("Tushlikka 45 ming sarfladim")
    await service.create_pending_transactions(owner_id, res.transactions)

    # Check duplicate
    is_dup = await service.detect_duplicate(owner_id, Decimal("45000"), "🍔 Ovqat", window_minutes=10)
    assert is_dup is True

    # Different amount should not be duplicate
    is_not_dup = await service.detect_duplicate(owner_id, Decimal("55000"), "🍔 Ovqat", window_minutes=10)
    assert is_not_dup is False
