"""Finance Intelligence & Investigation Engine.

Performs:
- Precise week-over-week variance calculation (+X.X% change)
- Expense anomaly detection and category breakdown spikes
- Automated investigation prompts for sudden spikes
- Budget threshold monitoring (50%, 75%, 90%, 100%, OVER)
- Strict backend/Decimal math (no hallucinated calculations)
"""
from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from discipline.db import get_session
from finance.timeutil import today_local
from finance.models import FinancialBudget, FinancialCategory, FinancialTransaction

logger = logging.getLogger(__name__)


class FinanceIntelligenceEngine:
    """Analytical engine for detecting spending anomalies and variance trends."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def compare_weekly_spending(
        self,
        reference_date: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        """
        Compare current week expenses with previous week expenses.
        Returns exact Decimal amounts, percentage delta, and category spikes.
        """
        ref = reference_date or today_local()

        # Current week start (Monday) and end
        current_monday = ref - datetime.timedelta(days=ref.weekday())
        prev_monday = current_monday - datetime.timedelta(days=7)
        prev_sunday = current_monday - datetime.timedelta(days=1)

        async with get_session() as session:
            # Current week total
            stmt_curr = select(func.coalesce(func.sum(FinancialTransaction.amount), Decimal("0.00"))).where(
                FinancialTransaction.owner_id == self.owner_id,
                FinancialTransaction.type == "EXPENSE",
                FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                FinancialTransaction.currency == "UZS",
                FinancialTransaction.transaction_date >= current_monday,
                FinancialTransaction.transaction_date <= ref,
            )
            curr_total = (await session.execute(stmt_curr)).scalar() or Decimal("0.00")

            # Previous week total (up to the same relative day of week for fair comparison)
            prev_ref = prev_monday + datetime.timedelta(days=ref.weekday())
            stmt_prev = select(func.coalesce(func.sum(FinancialTransaction.amount), Decimal("0.00"))).where(
                FinancialTransaction.owner_id == self.owner_id,
                FinancialTransaction.type == "EXPENSE",
                FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                FinancialTransaction.currency == "UZS",
                FinancialTransaction.transaction_date >= prev_monday,
                FinancialTransaction.transaction_date <= prev_ref,
            )
            prev_total = (await session.execute(stmt_prev)).scalar() or Decimal("0.00")

            # Category-wise breakdown for current week
            stmt_cats = (
                select(
                    FinancialTransaction.category_name,
                    func.sum(FinancialTransaction.amount).label("cat_sum"),
                )
                .where(
                    FinancialTransaction.owner_id == self.owner_id,
                    FinancialTransaction.type == "EXPENSE",
                    FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                FinancialTransaction.currency == "UZS",
                    FinancialTransaction.transaction_date >= current_monday,
                    FinancialTransaction.transaction_date <= ref,
                )
                .group_by(FinancialTransaction.category_name)
                .order_by(func.sum(FinancialTransaction.amount).desc())
            )
            curr_cats = dict((await session.execute(stmt_cats)).all())

            # Category-wise breakdown for previous week
            stmt_prev_cats = (
                select(
                    FinancialTransaction.category_name,
                    func.sum(FinancialTransaction.amount).label("cat_sum"),
                )
                .where(
                    FinancialTransaction.owner_id == self.owner_id,
                    FinancialTransaction.type == "EXPENSE",
                    FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                FinancialTransaction.currency == "UZS",
                    FinancialTransaction.transaction_date >= prev_monday,
                    FinancialTransaction.transaction_date <= prev_ref,
                )
                .group_by(FinancialTransaction.category_name)
            )
            prev_cats = dict((await session.execute(stmt_prev_cats)).all())

        # Sanitize category dicts (ensure no None keys)
        curr_cats = {
            (cat or "Boshqa"): amt for cat, amt in curr_cats.items()
        }
        prev_cats = {
            (cat or "Boshqa"): amt for cat, amt in prev_cats.items()
        }

        # Percentage change
        pct_change = Decimal("0.00")
        has_spike = False
        if prev_total > Decimal("0.00"):
            pct_change = ((curr_total - prev_total) / prev_total) * Decimal("100")
            pct_change = round(pct_change, 1)
            # A 25%+ increase constitutes an investigative spike
            if pct_change >= Decimal("25.0"):
                has_spike = True
        elif curr_total > Decimal("0.00"):
            pct_change = Decimal("100.0")
            has_spike = True

        # Find largest category jump
        biggest_spike_cat = None
        biggest_spike_delta = Decimal("0.00")
        for cat, amt in curr_cats.items():
            prev_amt = prev_cats.get(cat, Decimal("0.00"))
            delta = amt - prev_amt
            if delta > biggest_spike_delta:
                biggest_spike_delta = delta
                biggest_spike_cat = cat

        return {
            "current_total": curr_total,
            "previous_total": prev_total,
            "pct_change": pct_change,
            "has_spike": has_spike,
            "current_categories": curr_cats,
            "previous_categories": prev_cats,
            "biggest_spike_category": biggest_spike_cat,
            "biggest_spike_delta": biggest_spike_delta,
        }

    def generate_variance_message(self, analysis: Dict[str, Any]) -> Optional[str]:
        """Format investigation notification if spending increased substantially."""
        pct = analysis["pct_change"]
        if pct <= Decimal("0.00"):
            return None

        curr = f"{analysis['current_total']:,.0f}".replace(",", " ") + " so'm"
        prev = f"{analysis['previous_total']:,.0f}".replace(",", " ") + " so'm"
        spike_cat = analysis.get("biggest_spike_category")

        text = (
            f"⚠️ <b>MOLIYAVIY TAHLIL: Xarajatlaringiz o'tgan haftaga nisbatan {pct}% oshdi!</b>\n\n"
            f"• O'tgan hafta: <b>{prev}</b>\n"
            f"• Bu hafta: <b>{curr}</b>\n"
        )

        if spike_cat:
            text += f"\nEng katta o'sish <b>{spike_cat}</b> kategoriyasida kuzatildi."

        text += "\n\n<i>Xarajat oshishining asosiy sababi nima? Rejadan tashqari xarajat bo'ldimi?</i>"
        return text

    def generate_full_comparison_report(self, analysis: Dict[str, Any]) -> str:
        """Format a rich, detailed comparison report with category breakdowns."""
        pct = analysis["pct_change"]
        curr_total = analysis["current_total"]
        prev_total = analysis["previous_total"]
        curr = f"{curr_total:,.0f}".replace(",", " ") + " so'm"
        prev = f"{prev_total:,.0f}".replace(",", " ") + " so'm"
        curr_cats = analysis.get("current_categories", {})
        spike_cat = analysis.get("biggest_spike_category")

        if prev_total == Decimal("0.00") and curr_total == Decimal("0.00"):
            return "📊 <b>Haftalik taqqoslash:</b>\n\nBu va o'tgan haftada hali xarajatlar qayd etilmagan."

        if pct > Decimal("0.00"):
            header_icon = "⚠️" if analysis["has_spike"] else "📈"
            trend_str = f"<b>+{pct}%</b> ga ko'proq"
        elif pct < Decimal("0.00"):
            header_icon = "📉"
            trend_str = f"<b>{abs(pct)}%</b> ga tejaldi"
        else:
            header_icon = "📊"
            trend_str = "o'zgarishsiz (0.0%)"

        lines = [
            f"{header_icon} <b>HAFTALIK MOLIYAVIY TAQQOSLASH</b>\n",
            f"• O'tgan hafta: <b>{prev}</b>",
            f"• Bu hafta: <b>{curr}</b>",
            f"• Dinamika: {trend_str}\n",
        ]

        if curr_cats:
            lines.append("🏷 <b>Bu haftadagi asosiy xarajatlar:</b>")
            for cat, amt in list(curr_cats.items())[:5]:
                amt_str = f"{amt:,.0f}".replace(",", " ") + " so'm"
                lines.append(f"  • {cat}: <b>{amt_str}</b>")

        if spike_cat and analysis["has_spike"]:
            lines.append(f"\n⚠️ <i>Diqqat: Eng katta o'sish <b>{spike_cat}</b> bo'yicha kuzatildi.</i>")
        elif pct <= Decimal("0.00"):
            lines.append("\n✅ <i>Ajoyib intizom! Xarajatlar nazorat ostida saqlanmoqda.</i>")

        return "\n".join(lines)
