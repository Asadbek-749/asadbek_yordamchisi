"""Database layer for the debt / budget planner: incomes, debts, repayments and plan inputs."""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from discipline.db import get_session
from finance.models import (
    FinancialCategory,
    FinancialDebt,
    FinancialIncomeSource,
    FinancialRecurring,
    FinancialTransaction,
)
from finance.planner import (
    ZERO,
    Debt,
    FixedItem,
    IncomeItem,
    Plan,
    PlanInputs,
    build_plan,
    monthly_interest,
    q,
)
from finance.service import FinanceService
from finance.timeutil import today_local

DEBT_FIELDS = {"nom": "title", "qoldiq": "balance", "tolov": "monthly_payment", "foiz": "annual_rate_pct", "kun": "pay_day"}


class PlannerService:
    """CRUD for incomes and debts plus assembling the inputs of the monthly plan."""

    def __init__(self, finance: Optional[FinanceService] = None) -> None:
        self.finance = finance or FinanceService()

    # ── incomes ──
    async def add_income(self, owner_id: int, title: str, amount: Decimal, pay_day: int, currency: str = "UZS") -> FinancialIncomeSource:
        if not 1 <= pay_day <= 31:
            raise ValueError("pay_day must be 1..31")
        async with get_session() as session:
            row = FinancialIncomeSource(
                owner_id=owner_id, title=title.strip()[:200], amount=amount, pay_day=pay_day, currency=currency
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_incomes(self, owner_id: int) -> List[FinancialIncomeSource]:
        async with get_session() as session:
            q_ = select(FinancialIncomeSource).where(
                FinancialIncomeSource.owner_id == owner_id, FinancialIncomeSource.is_active.is_(True)
            ).order_by(FinancialIncomeSource.pay_day, FinancialIncomeSource.id)
            return list((await session.execute(q_)).scalars().all())

    async def delete_income(self, owner_id: int, income_id: int) -> bool:
        async with get_session() as session:
            row = await session.get(FinancialIncomeSource, income_id)
            if not row or row.owner_id != owner_id or not row.is_active:
                return False
            row.is_active = False
            await session.commit()
            return True

    # ── debts ──
    async def add_debt(
        self, owner_id: int, title: str, balance: Decimal, monthly_payment: Decimal, annual_rate_pct: Decimal,
        pay_day: int, kind: str = "BANK_CREDIT", currency: str = "UZS",
    ) -> FinancialDebt:
        if not 1 <= pay_day <= 31:
            raise ValueError("pay_day must be 1..31")
        if balance <= ZERO or monthly_payment < ZERO or not ZERO <= annual_rate_pct <= Decimal("300"):
            raise ValueError("invalid debt numbers")
        async with get_session() as session:
            row = FinancialDebt(
                owner_id=owner_id, title=title.strip()[:200], kind=kind, balance=balance, monthly_payment=monthly_payment,
                annual_rate_pct=annual_rate_pct, pay_day=pay_day, currency=currency,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_debts(self, owner_id: int, include_closed: bool = False) -> List[FinancialDebt]:
        async with get_session() as session:
            q_ = select(FinancialDebt).where(FinancialDebt.owner_id == owner_id)
            if not include_closed:
                q_ = q_.where(FinancialDebt.is_active.is_(True))
            return list((await session.execute(q_.order_by(FinancialDebt.id))).scalars().all())

    async def get_debt(self, owner_id: int, debt_id: int) -> Optional[FinancialDebt]:
        async with get_session() as session:
            row = await session.get(FinancialDebt, debt_id)
            return row if row and row.owner_id == owner_id else None

    async def update_debt(self, owner_id: int, debt_id: int, **fields: Any) -> Optional[FinancialDebt]:
        allowed = set(DEBT_FIELDS.values())
        async with get_session() as session:
            row = await session.get(FinancialDebt, debt_id)
            if not row or row.owner_id != owner_id or not row.is_active:
                return None
            for key, value in fields.items():
                if key not in allowed:
                    raise ValueError(f"unknown field {key}")
                if key == "pay_day" and not 1 <= int(value) <= 31:
                    raise ValueError("pay_day must be 1..31")
                if key in ("balance", "monthly_payment", "annual_rate_pct") and Decimal(value) < ZERO:
                    raise ValueError("negative number")
                setattr(row, key, value)
            if row.balance <= ZERO:
                row.is_active = False
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_debt(self, owner_id: int, debt_id: int) -> bool:
        async with get_session() as session:
            row = await session.get(FinancialDebt, debt_id)
            if not row or row.owner_id != owner_id or not row.is_active:
                return False
            row.is_active = False
            await session.commit()
            return True

    async def pay_debt(self, owner_id: int, debt_id: int, amount: Decimal, when: Optional[datetime.date] = None) -> Optional[Dict[str, Any]]:
        """Record a repayment: lowers the balance by the principal part and books a confirmed expense.

        Of every payment, the interest accrued for one month is treated as interest and the rest as
        principal. Compare with your bank statement and correct the balance with /editdebt if needed.
        """
        from finance.extractor import ExtractedTransactionItem

        if amount <= ZERO:
            raise ValueError("amount must be positive")
        debt = await self.get_debt(owner_id, debt_id)
        if debt is None or not debt.is_active:
            return None
        interest = min(monthly_interest(debt.balance, debt.annual_rate_pct), amount)
        principal = min(amount - interest, debt.balance)
        new_balance = debt.balance - principal

        async with get_session() as session:
            row = await session.get(FinancialDebt, debt_id)
            row.balance = new_balance
            if new_balance <= ZERO:
                row.is_active = False
            await session.commit()

        item = ExtractedTransactionItem(
            type="EXPENSE", amount=amount, category="DEBT", description=f"{debt.title} to'lovi",
            currency=debt.currency, transaction_date=when or today_local(),
        )
        tx = (await self.finance.create_pending_transactions(owner_id, [item], source="debt"))[0]
        tx = await self.finance.confirm_transaction(tx.id, owner_id)
        return {"debt": debt, "interest": interest, "principal": principal, "new_balance": new_balance,
                "closed": new_balance <= ZERO, "transaction": tx}

    # ── plan inputs ──
    async def _variable_spending(self, owner_id: int, today: datetime.date, rate: Optional[Decimal]) -> tuple[Decimal, int]:
        """Average monthly variable spending in so'm.

        Debt repayments and expenses created from recurring payments are excluded (the planner counts
        those separately). Uses up to the last 3 full months; with no history falls back to the
        current month scaled to a full month once at least a week of data exists.
        """
        first_this = today.replace(day=1)
        async with get_session() as session:
            debt_cat_ids = [
                c for (c,) in (
                    await session.execute(select(FinancialCategory.id).where(FinancialCategory.name == "DEBT"))
                ).all()
            ]
            rows = list(
                (
                    await session.execute(
                        select(FinancialTransaction).where(
                            FinancialTransaction.owner_id == owner_id,
                            FinancialTransaction.type == "EXPENSE",
                            FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                            FinancialTransaction.transaction_date >= (first_this - datetime.timedelta(days=95)),
                        )
                    )
                ).scalars().all()
            )

        per_month: Dict[tuple[int, int], Decimal] = {}
        for tx in rows:
            if tx.source in ("debt", "recurring") or tx.category_id in debt_cat_ids:
                continue
            amount = tx.amount
            if (tx.currency or "UZS").upper() == "USD":
                if rate is None:
                    continue
                amount = amount * rate
            key = (tx.transaction_date.year, tx.transaction_date.month)
            per_month[key] = per_month.get(key, ZERO) + amount

        full_months = []
        y, m = first_this.year, first_this.month
        for _ in range(3):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            if per_month.get((y, m), ZERO) > ZERO:
                full_months.append(per_month[(y, m)])
        if full_months:
            return q(sum(full_months, ZERO) / len(full_months)), len(full_months)

        current = per_month.get((today.year, today.month), ZERO)
        if current > ZERO and today.day >= 7:
            return q(current / today.day * calendar.monthrange(today.year, today.month)[1]), 1
        return ZERO, 0

    async def load_inputs(self, owner_id: int, today: Optional[datetime.date] = None) -> PlanInputs:
        today = today or today_local()
        rate = await self.finance.get_usd_rate(owner_id)
        notes: List[str] = []

        def to_uzs(amount: Decimal, currency: str, what: str) -> Optional[Decimal]:
            if (currency or "UZS").upper() == "UZS":
                return amount
            if rate is None:
                notes.append(f"«{what}» dollarda, lekin kurs belgilanmagan: hisobga olinmadi (/setrate 12650).")
                return None
            return q(amount * rate)

        incomes = []
        for row in await self.list_incomes(owner_id):
            amount = to_uzs(row.amount, row.currency, row.title)
            if amount is not None:
                incomes.append(IncomeItem(row.title, amount, row.pay_day))

        debts = []
        for row in await self.list_debts(owner_id):
            balance = to_uzs(row.balance, row.currency, row.title)
            payment = to_uzs(row.monthly_payment, row.currency, row.title)
            if balance is not None and payment is not None:
                debts.append(Debt(row.id, row.title, row.kind, balance, row.annual_rate_pct, payment, row.pay_day))

        fixed = []
        async with get_session() as session:
            recurring = list(
                (
                    await session.execute(
                        select(FinancialRecurring).where(
                            FinancialRecurring.owner_id == owner_id, FinancialRecurring.is_active.is_(True)
                        )
                    )
                ).scalars().all()
            )
        for row in recurring:
            amount = to_uzs(row.amount, row.currency, row.title)
            if amount is not None:
                fixed.append(FixedItem(row.title, amount, row.day_of_month))

        variable, months = await self._variable_spending(owner_id, today, rate)
        return PlanInputs(incomes=incomes, debts=debts, fixed=fixed, variable_avg=variable, variable_months=months,
                          today=today, notes=list(dict.fromkeys(notes)))

    async def build_plan(self, owner_id: int, today: Optional[datetime.date] = None, extra: Optional[Decimal] = None) -> Plan:
        return build_plan(await self.load_inputs(owner_id, today), extra_override=extra)
