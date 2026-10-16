"""Finance service implementing strict confirmation pipeline, Decimal math, and audit logs.

Strict Architectural Principles:
1. All calculations use Decimal and ONLY include CONFIRMED transactions.
2. Every voice or text intake creates PENDING transactions first.
3. Hard delete is prohibited for confirmed transactions — status becomes CANCELLED.
4. Comprehensive audit trail in financial_audit_logs.
"""
from __future__ import annotations

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from discipline.db import get_session
from discipline.models import utcnow
from finance.extractor import ExtractedTransactionItem
from finance.models import (
    FinancialAccount,
    FinancialAuditLog,
    FinancialBudget,
    FinancialCategory,
    FinancialGoal,
    FinancialReport,
    FinancialTransaction,
    FinancialTransactionItem,
)

logger = logging.getLogger(__name__)

DEFAULT_EXPENSE_CATEGORIES = [
    ("FOOD", "🍔 Ovqat", "🍔"),
    ("TRANSPORT", "🚕 Transport", "🚕"),
    ("HOME", "🏠 Uy", "🏠"),
    ("UTILITIES", "💡 Kommunal", "💡"),
    ("INTERNET", "📱 Aloqa/Internet", "📱"),
    ("SHOPPING", "🛒 Xarid", "🛒"),
    ("CLOTHING", "👕 Kiyim", "👕"),
    ("TECHNOLOGY", "💻 Texnika", "💻"),
    ("EDUCATION", "📚 Ta'lim", "📚"),
    ("WORK", "💼 Ish", "💼"),
    ("ENTERTAINMENT", "🎮 Ko‘ngilochar", "🎮"),
    ("HEALTH", "🏥 Sog‘liq", "🏥"),
    ("FAMILY", "👨‍👩‍👦 Oila", "👨‍👩‍👦"),
    ("DEBT", "💳 Qarz/Kredit", "💳"),
    ("SAVINGS", "💰 Jamg‘arma", "💰"),
    ("OTHER", "📦 Boshqa", "📦"),
]

DEFAULT_INCOME_CATEGORIES = [
    ("MAIN_JOB", "💼 Asosiy ish", "💼"),
    ("TEACHING", "🎓 Dars berish", "🎓"),
    ("AI_SERVICES", "🤖 AI xizmatlari", "🤖"),
    ("FREELANCE", "💻 Freelance", "💻"),
    ("BUSINESS", "📈 Biznes", "📈"),
    ("BONUS", "🎁 Bonus", "🎁"),
    ("OTHER", "💵 Boshqa", "💵"),
]


class FinanceService:
    """Core business logic for personal finances."""

    @staticmethod
    def format_money(amount: Decimal, currency: str = "so'm") -> str:
        """Format Decimal amount into readable string (e.g. 1 500 000 so'm)."""
        val = int(amount) if amount % 1 == 0 else float(amount)
        formatted = f"{val:,}".replace(",", " ")
        return f"{formatted} {currency}"

    async def ensure_default_categories(self) -> None:
        """Seed default categories if they don't already exist."""
        async with get_session() as session:
            count_q = select(func.count(FinancialCategory.id))
            res = await session.execute(count_q)
            if (res.scalar() or 0) > 0:
                return

            for name, display, icon in DEFAULT_EXPENSE_CATEGORIES:
                session.add(
                    FinancialCategory(
                        owner_id=None,
                        name=name,
                        display_name=display,
                        type="EXPENSE",
                        icon=icon,
                        is_system=True,
                    )
                )
            for name, display, icon in DEFAULT_INCOME_CATEGORIES:
                session.add(
                    FinancialCategory(
                        owner_id=None,
                        name=name,
                        display_name=display,
                        type="INCOME",
                        icon=icon,
                        is_system=True,
                    )
                )
            await session.commit()
            logger.info("Default financial categories seeded successfully.")

    async def _audit(
        self,
        session: AsyncSession,
        actor_id: int,
        action: str,
        entity_id: int,
        old_val: Optional[Any] = None,
        new_val: Optional[Any] = None,
    ) -> None:
        """Write an entry to the audit log."""
        session.add(
            FinancialAuditLog(
                actor_id=actor_id,
                action=action,
                entity_type="transaction",
                entity_id=entity_id,
                old_value=json.dumps(old_val, default=str) if old_val else None,
                new_value=json.dumps(new_val, default=str) if new_val else None,
            )
        )

    async def detect_duplicate(
        self,
        owner_id: int,
        amount: Decimal,
        category_name: str,
        window_minutes: int = 10,
    ) -> bool:
        """Check if a similar transaction was entered within the last N minutes."""
        async with get_session() as session:
            cutoff = utcnow() - datetime.timedelta(minutes=window_minutes)
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.amount == amount,
                FinancialTransaction.category_name == category_name,
                FinancialTransaction.created_at >= cutoff,
                FinancialTransaction.status.in_(["PENDING", "CONFIRMED"]),
            )
            res = await session.execute(q)
            return res.scalars().first() is not None

    async def create_pending_transactions(
        self,
        owner_id: int,
        items: List[ExtractedTransactionItem],
        source: str = "text",
        telegram_message_id: Optional[int] = None,
    ) -> List[FinancialTransaction]:
        """Create transactions in PENDING status. Does NOT count towards balances until confirmed."""
        await self.ensure_default_categories()
        created_records: List[FinancialTransaction] = []

        async with get_session() as session:
            for item in items:
                # Look up category ID
                cat_q = select(FinancialCategory).where(
                    FinancialCategory.name == item.category,
                    FinancialCategory.type == item.type,
                )
                cat_res = await session.execute(cat_q)
                category = cat_res.scalars().first()

                tx = FinancialTransaction(
                    owner_id=owner_id,
                    type=item.type,
                    category_id=category.id if category else None,
                    category_name=category.display_name if category else item.category,
                    amount=item.amount,
                    currency=item.currency,
                    description=item.description,
                    source=source,
                    transaction_date=item.transaction_date,
                    status="PENDING",
                    telegram_message_id=telegram_message_id,
                )
                session.add(tx)
                await session.flush()

                await self._audit(
                    session=session,
                    actor_id=owner_id,
                    action="CREATE",
                    entity_id=tx.id,
                    new_val={"amount": str(tx.amount), "type": tx.type, "category": tx.category_name, "status": "PENDING"},
                )
                created_records.append(tx)

            await session.commit()
            for tx in created_records:
                await session.refresh(tx)

        return created_records

    async def confirm_transaction(self, transaction_id: int, actor_id: int) -> Optional[FinancialTransaction]:
        """Move transaction from PENDING to CONFIRMED. Now included in balances and accounting."""
        async with get_session() as session:
            tx = await session.get(FinancialTransaction, transaction_id)
            if not tx:
                return None

            old_status = tx.status
            tx.status = "CONFIRMED"
            tx.confirmed_at = utcnow()
            tx.updated_at = utcnow()

            await self._audit(
                session=session,
                actor_id=actor_id,
                action="CONFIRM",
                entity_id=tx.id,
                old_val={"status": old_status},
                new_val={"status": "CONFIRMED"},
            )
            await session.commit()
            await session.refresh(tx)
            return tx

    async def confirm_all_pending(self, owner_id: int, actor_id: int) -> int:
        """Confirm all pending transactions for the owner."""
        async with get_session() as session:
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.status == "PENDING",
            )
            res = await session.execute(q)
            pending_list = list(res.scalars().all())
            now = utcnow()
            for tx in pending_list:
                tx.status = "CONFIRMED"
                tx.confirmed_at = now
                tx.updated_at = now
                await self._audit(
                    session=session,
                    actor_id=actor_id,
                    action="CONFIRM",
                    entity_id=tx.id,
                    old_val={"status": "PENDING"},
                    new_val={"status": "CONFIRMED"},
                )
            await session.commit()
            return len(pending_list)

    async def edit_transaction(
        self,
        transaction_id: int,
        actor_id: int,
        new_amount: Optional[Decimal] = None,
        new_category_name: Optional[str] = None,
        new_description: Optional[str] = None,
    ) -> Optional[FinancialTransaction]:
        """Edit a transaction with full audit trail."""
        async with get_session() as session:
            tx = await session.get(FinancialTransaction, transaction_id)
            if not tx:
                return None

            old_data = {"amount": str(tx.amount), "category": tx.category_name, "desc": tx.description}

            if new_amount is not None:
                tx.amount = new_amount
            if new_category_name is not None:
                tx.category_name = new_category_name
            if new_description is not None:
                tx.description = new_description

            tx.status = "EDITED" if tx.status == "CONFIRMED" else "PENDING"
            tx.updated_at = utcnow()

            new_data = {"amount": str(tx.amount), "category": tx.category_name, "desc": tx.description}
            await self._audit(
                session=session,
                actor_id=actor_id,
                action="EDIT",
                entity_id=tx.id,
                old_val=old_data,
                new_val=new_data,
            )
            await session.commit()
            await session.refresh(tx)
            return tx

    async def cancel_transaction(self, transaction_id: int, actor_id: int) -> Optional[FinancialTransaction]:
        """Soft-cancel a transaction. Hard delete is prohibited to preserve audit trail."""
        async with get_session() as session:
            tx = await session.get(FinancialTransaction, transaction_id)
            if not tx:
                return None

            old_status = tx.status
            tx.status = "CANCELLED"
            tx.updated_at = utcnow()

            await self._audit(
                session=session,
                actor_id=actor_id,
                action="CANCEL",
                entity_id=tx.id,
                old_val={"status": old_status},
                new_val={"status": "CANCELLED"},
            )
            await session.commit()
            await session.refresh(tx)
            return tx

    async def get_pending_transactions(self, owner_id: int) -> List[FinancialTransaction]:
        """Get all transactions currently awaiting confirmation."""
        async with get_session() as session:
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.status == "PENDING",
            ).order_by(FinancialTransaction.created_at.desc())
            res = await session.execute(q)
            return list(res.scalars().all())

    # ─────────────────────────────────────────────────────────────────────────
    # REPORTS & AGGREGATIONS (CONFIRMED ONLY!)
    # ─────────────────────────────────────────────────────────────────────────

    async def get_daily_summary(
        self, owner_id: int, target_date: Optional[datetime.date] = None
    ) -> Dict[str, Any]:
        """Calculate daily total income, expense, and net balance using Decimal."""
        t_date = target_date or datetime.date.today()
        async with get_session() as session:
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.transaction_date == t_date,
                FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
            )
            res = await session.execute(q)
            txs = list(res.scalars().all())

            total_income = Decimal("0.00")
            total_expense = Decimal("0.00")
            categories: Dict[str, Decimal] = {}

            for tx in txs:
                if tx.type == "INCOME":
                    total_income += tx.amount
                elif tx.type == "EXPENSE":
                    total_expense += tx.amount
                    cat_name = tx.category_name or "Boshqa"
                    categories[cat_name] = categories.get(cat_name, Decimal("0.00")) + tx.amount

            net_balance = total_income - total_expense

            # Also check pending count
            pending_q = select(func.count(FinancialTransaction.id)).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.status == "PENDING",
            )
            pending_count = (await session.execute(pending_q)).scalar() or 0

            return {
                "date": t_date,
                "total_income": total_income,
                "total_expense": total_expense,
                "net_balance": net_balance,
                "categories": categories,
                "pending_count": pending_count,
                "transaction_count": len(txs),
            }

    async def get_weekly_summary(
        self, owner_id: int, start_date: datetime.date, end_date: datetime.date
    ) -> Dict[str, Any]:
        """Calculate weekly financial summary across date range."""
        async with get_session() as session:
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.transaction_date >= start_date,
                FinancialTransaction.transaction_date <= end_date,
                FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
            ).order_by(FinancialTransaction.amount.desc())
            res = await session.execute(q)
            txs = list(res.scalars().all())

            total_income = Decimal("0.00")
            total_expense = Decimal("0.00")
            categories: Dict[str, Decimal] = {}
            top_expense_tx: Optional[FinancialTransaction] = None

            for tx in txs:
                if tx.type == "INCOME":
                    total_income += tx.amount
                elif tx.type == "EXPENSE":
                    total_expense += tx.amount
                    cat_name = tx.category_name or "Boshqa"
                    categories[cat_name] = categories.get(cat_name, Decimal("0.00")) + tx.amount
                    if top_expense_tx is None or tx.amount > top_expense_tx.amount:
                        top_expense_tx = tx

            net_balance = total_income - total_expense

            # Sort categories by highest spend
            sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)
            top_category = sorted_cats[0] if sorted_cats else None

            return {
                "start_date": start_date,
                "end_date": end_date,
                "total_income": total_income,
                "total_expense": total_expense,
                "net_balance": net_balance,
                "categories": sorted_cats,
                "top_category": top_category,
                "top_expense_tx": top_expense_tx,
                "transaction_count": len(txs),
            }

    async def get_monthly_summary(self, owner_id: int, year: int, month: int) -> Dict[str, Any]:
        """Calculate monthly financial summary with percentage ratios."""
        start_date = datetime.date(year, month, 1)
        # Calculate next month start
        if month == 12:
            next_start = datetime.date(year + 1, 1, 1)
        else:
            next_start = datetime.date(year, month + 1, 1)

        end_date = next_start - datetime.timedelta(days=1)

        async with get_session() as session:
            q = select(FinancialTransaction).where(
                FinancialTransaction.owner_id == owner_id,
                FinancialTransaction.transaction_date >= start_date,
                FinancialTransaction.transaction_date <= end_date,
                FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
            )
            res = await session.execute(q)
            txs = list(res.scalars().all())

            total_income = Decimal("0.00")
            total_expense = Decimal("0.00")
            expense_categories: Dict[str, Decimal] = {}
            income_sources: Dict[str, Decimal] = {}

            for tx in txs:
                if tx.type == "INCOME":
                    total_income += tx.amount
                    src = tx.category_name or "Boshqa"
                    income_sources[src] = income_sources.get(src, Decimal("0.00")) + tx.amount
                elif tx.type == "EXPENSE":
                    total_expense += tx.amount
                    cat = tx.category_name or "Boshqa"
                    expense_categories[cat] = expense_categories.get(cat, Decimal("0.00")) + tx.amount

            net_balance = total_income - total_expense

            # Safe percentage calculation (Division by zero prevention)
            if total_income > Decimal("0.00"):
                expense_ratio = (total_expense / total_income) * Decimal("100.00")
            else:
                expense_ratio = None

            sorted_expenses = sorted(expense_categories.items(), key=lambda x: x[1], reverse=True)
            sorted_incomes = sorted(income_sources.items(), key=lambda x: x[1], reverse=True)

            return {
                "year": year,
                "month": month,
                "start_date": start_date,
                "end_date": end_date,
                "total_income": total_income,
                "total_expense": total_expense,
                "net_balance": net_balance,
                "expense_ratio": expense_ratio,
                "expense_categories": sorted_expenses,
                "income_sources": sorted_incomes,
                "transaction_count": len(txs),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # BUDGET MANAGEMENT & ALERTS
    # ─────────────────────────────────────────────────────────────────────────

    async def set_monthly_budget(
        self, owner_id: int, period_key: str, limit_amount: Decimal, category_id: Optional[int] = None
    ) -> FinancialBudget:
        """Set or update monthly budget limit."""
        async with get_session() as session:
            q = select(FinancialBudget).where(
                FinancialBudget.owner_id == owner_id,
                FinancialBudget.period_key == period_key,
                FinancialBudget.category_id == category_id,
            )
            budget = (await session.execute(q)).scalars().first()

            if budget:
                budget.limit_amount = limit_amount
                budget.updated_at = utcnow()
            else:
                budget = FinancialBudget(
                    owner_id=owner_id,
                    period_key=period_key,
                    category_id=category_id,
                    limit_amount=limit_amount,
                )
                session.add(budget)

            await session.commit()
            await session.refresh(budget)
            return budget

    async def get_budget_status(
        self, owner_id: int, period_key: str, category_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Get current budget usage against CONFIRMED expenses."""
        async with get_session() as session:
            q = select(FinancialBudget).where(
                FinancialBudget.owner_id == owner_id,
                FinancialBudget.period_key == period_key,
                FinancialBudget.category_id == category_id,
            )
            budget = (await session.execute(q)).scalars().first()
            if not budget:
                return None

            # Calculate total confirmed expenses in this period
            # period_key is "YYYY-MM"
            year, month = map(int, period_key.split("-"))
            monthly_data = await self.get_monthly_summary(owner_id, year, month)
            spent = monthly_data["total_expense"]
            limit = budget.limit_amount
            remaining = limit - spent

            ratio = (spent / limit * Decimal("100.00")) if limit > Decimal("0") else Decimal("0")

            return {
                "period_key": period_key,
                "limit_amount": limit,
                "spent_amount": spent,
                "remaining_amount": remaining,
                "usage_percentage": ratio,
                "is_exceeded": spent > limit,
                "budget_record": budget,
            }

    async def check_budget_alerts(self, owner_id: int, period_key: str) -> List[str]:
        """Check budget thresholds (50%, 75%, 90%, 100%, 100%+) and return new alert texts."""
        status = await self.get_budget_status(owner_id, period_key)
        if not status:
            return []

        budget: FinancialBudget = status["budget_record"]
        spent = status["spent_amount"]
        limit = status["limit_amount"]
        ratio = status["usage_percentage"]
        alerts: List[str] = []

        async with get_session() as session:
            db_budget = await session.get(FinancialBudget, budget.id)
            if not db_budget:
                return []

            if ratio >= Decimal("100.00"):
                if not db_budget.alert_100_sent:
                    db_budget.alert_100_sent = True
                    diff = spent - limit
                    alerts.append(
                        f"🚨 <b>BYUDJET OSHDI</b>\n\n"
                        f"Oylik xarajat limitingiz tugadi!\n"
                        f"🎯 Limit: {self.format_money(limit)}\n"
                        f"💸 Sarflandi: {self.format_money(spent)}\n"
                        f"⚠️ Farq: +{self.format_money(diff)}"
                    )
                db_budget.alert_90_sent = True
                db_budget.alert_75_sent = True
                db_budget.alert_50_sent = True
            elif ratio >= Decimal("90.00"):
                if not db_budget.alert_90_sent:
                    db_budget.alert_90_sent = True
                    alerts.append(
                        f"⚠️ <b>BYUDJET OGOHLANTIRISHI</b>\n\n"
                        f"Oylik byudjetingizning <b>{ratio:.1f}%</b> qismi ishlatildi.\n"
                        f"Qoldiq: {self.format_money(limit - spent)}"
                    )
                db_budget.alert_75_sent = True
                db_budget.alert_50_sent = True
            elif ratio >= Decimal("75.00"):
                if not db_budget.alert_75_sent:
                    db_budget.alert_75_sent = True
                    alerts.append(
                        f"ℹ️ <b>Byudjet xabari:</b> Oylik byudjetingizning <b>75%</b> qismiga yetdingiz.\n"
                        f"Qoldiq: {self.format_money(limit - spent)}"
                    )
                db_budget.alert_50_sent = True
            elif ratio >= Decimal("50.00"):
                if not db_budget.alert_50_sent:
                    db_budget.alert_50_sent = True
                    alerts.append(
                        f"ℹ️ <b>Byudjet xabari:</b> Oylik byudjetingizning <b>50%</b> qismi sarflandi."
                    )

            await session.commit()

        return alerts
