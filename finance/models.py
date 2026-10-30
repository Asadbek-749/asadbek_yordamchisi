"""SQLAlchemy 2.x async models for Personal Finance System.

Strict Rules:
- Decimal / NUMERIC(18, 2) is used for all monetary amounts (NEVER float).
- Transaction statuses: PENDING, CONFIRMED, EDITED, CANCELLED.
- Only CONFIRMED transactions are computed in balances and reports.
- Audit log is maintained for all state changes.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from discipline.models import Base, utcnow


class FinancialAccount(Base):
    """User financial account / wallet (Cash, Card, Bank, etc.)."""
    __tablename__ = "financial_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Hamyon", "Uzcard"
    currency: Mapped[str] = mapped_column(String(10), default="UZS", nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FinancialCategory(Base):
    """Categories for income and expenses."""
    __tablename__ = "financial_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)  # null = system default
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "FOOD", "TRANSPORT"
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "🍔 Ovqat"
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # "INCOME" or "EXPENSE"
    icon: Mapped[str] = mapped_column(String(20), default="📦")
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transactions: Mapped[list["FinancialTransaction"]] = relationship(
        "FinancialTransaction", back_populates="category"
    )


class FinancialTransaction(Base):
    """Individual financial transaction record."""
    __tablename__ = "financial_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # INCOME, EXPENSE, TRANSFER, REFUND
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("financial_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Cached string for convenience
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="UZS", nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="text")  # "text", "voice", "manual"
    transaction_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="PENDING", index=True, nullable=False
    )  # PENDING, CONFIRMED, EDITED, CANCELLED
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    confirmed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    category: Mapped[Optional["FinancialCategory"]] = relationship(
        "FinancialCategory", back_populates="transactions"
    )
    items: Mapped[list["FinancialTransactionItem"]] = relationship(
        "FinancialTransactionItem", back_populates="transaction", cascade="all, delete-orphan"
    )


class FinancialTransactionItem(Base):
    """Individual line item within a compound transaction (e.g. multi-item expense)."""
    __tablename__ = "financial_transaction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("financial_transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("financial_categories.id", ondelete="SET NULL"), nullable=True
    )
    category_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transaction: Mapped["FinancialTransaction"] = relationship(
        "FinancialTransaction", back_populates="items"
    )


class FinancialBudget(Base):
    """Budget limits and threshold alerts for months or specific categories."""
    __tablename__ = "financial_budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("financial_categories.id", ondelete="CASCADE"), nullable=True
    )
    period_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # e.g. "2026-10"
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    # Alert notification tracking flags (sent once per threshold)
    alert_50_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_75_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_90_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_100_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("owner_id", "period_key", "category_id", name="uq_budget_owner_period_cat"),
    )


class FinancialGoal(Base):
    """Financial saving goal (e.g. New Laptop, Travel, Emergency Fund)."""
    __tablename__ = "financial_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="UZS", nullable=False)
    deadline: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")  # ACTIVE, ACHIEVED, CANCELLED
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FinancialReport(Base):
    """Archived daily, weekly, or monthly financial report summary."""
    __tablename__ = "financial_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # DAILY, WEEKLY, MONTHLY
    period_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # e.g. "2026-10-16", "2026-W42", "2026-10"

    total_income: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), nullable=False)
    total_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), nullable=False)
    net_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), nullable=False)
    expense_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)  # percentage

    top_category_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    top_category_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("owner_id", "report_type", "period_key", name="uq_report_owner_type_period"),
    )


class FinancialAuditLog(Base):
    """Audit trail for all financial operations (CREATE, CONFIRM, EDIT, CANCEL)."""
    __tablename__ = "financial_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # CREATE, CONFIRM, EDIT, CANCEL, DELETE
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "transaction", "budget"
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or text description
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or text description
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FinancialSetting(Base):
    """Per-owner finance settings (e.g. the manually set USD -> UZS rate)."""
    __tablename__ = "financial_settings"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
