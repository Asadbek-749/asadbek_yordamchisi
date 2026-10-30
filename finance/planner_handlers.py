"""Telegram commands for incomes, debts and the monthly plan (owner only)."""
from __future__ import annotations

import html
import logging
from decimal import Decimal
from typing import List

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from finance.planner import ZERO, payoff_single
from finance.planner_service import PlannerService
from finance.planner_text import (
    ADDDEBT_USAGE,
    ADDINCOME_USAGE,
    ParseError,
    format_debts,
    format_plan,
    format_scenario,
    money,
    months_text,
    parse_adddebt,
    parse_addincome,
    parse_editdebt,
    parse_money,
)
from finance.timeutil import today_local

logger = logging.getLogger(__name__)


def esc(text: str) -> str:
    """Escape &, < and > for Telegram HTML (apostrophes stay readable)."""
    return html.escape(text, quote=False)

PLANNER_COMMANDS = (
    "addincome", "incomes", "delincome", "adddebt", "debts", "paydebt", "editdebt", "deldebt", "plan", "whatif",
)


def _args(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def split_message(text: str, limit: int = 3800) -> List[str]:
    """Split on blank lines / newlines so each piece fits Telegram's 4096-character limit."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n"):
        if len(current) + len(block) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + block
    if current:
        chunks.append(current)
    return chunks


async def _send_long(message: Message, text: str) -> None:
    for chunk in split_message(text):
        await message.answer(chunk, parse_mode="HTML")


def register_planner_handlers(router: Router, planner: PlannerService, owner_id: int) -> None:
    is_owner = F.from_user.id == owner_id

    # ── incomes ──
    @router.message(is_owner, Command("addincome"))
    async def cmd_add_income(message: Message) -> None:
        try:
            data = parse_addincome(_args(message))
        except ParseError as exc:
            await message.answer(str(exc), parse_mode="HTML")
            return
        row = await planner.add_income(owner_id, **data)
        await message.answer(
            f"✅ Daromad qo'shildi: <b>{esc(row.title)}</b> — {money(row.amount, row.currency)}, "
            f"har oyning <b>{row.pay_day}-kuni</b>.\nQarzlar bo'lsa: /adddebt, natija: /plan",
            parse_mode="HTML",
        )

    @router.message(is_owner, Command("incomes"))
    async def cmd_incomes(message: Message) -> None:
        rows = await planner.list_incomes(owner_id)
        if not rows:
            await message.answer(ADDINCOME_USAGE, parse_mode="HTML")
            return
        lines = ["💰 <b>Oylik daromadlar</b>\n"]
        total = ZERO
        for r in rows:
            lines.append(f"#{r.id} {esc(r.title)} — {money(r.amount, r.currency)} (har oy {r.pay_day}-kuni)")
            if r.currency.upper() == "UZS":
                total += r.amount
        lines.append(f"\n<b>Jami (so'mda):</b> {money(total)}\nO'chirish: <code>/delincome &lt;raqam&gt;</code>")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.message(is_owner, Command("delincome"))
    async def cmd_del_income(message: Message) -> None:
        arg = _args(message).lstrip("#")
        if not arg.isdigit():
            await message.answer("ℹ️ Format: <code>/delincome 2</code>", parse_mode="HTML")
            return
        ok = await planner.delete_income(owner_id, int(arg))
        await message.answer("🗑 O'chirildi." if ok else "❌ Bunday daromad topilmadi.")

    # ── debts ──
    @router.message(is_owner, Command("adddebt"))
    async def cmd_add_debt(message: Message) -> None:
        try:
            data = parse_adddebt(_args(message))
            row = await planner.add_debt(owner_id, **data)
        except (ParseError, ValueError) as exc:
            text = str(exc) if isinstance(exc, ParseError) else "❌ Raqamlar noto'g'ri (qoldiq > 0, to'lov ≥ 0, foiz 0–300)."
            await message.answer(text, parse_mode="HTML")
            return
        est = payoff_single(row.balance, row.annual_rate_pct, row.monthly_payment, today_local())
        if row.monthly_payment <= ZERO:
            when = "oylik to'lov kiritilmagan, muddatni hisoblab bo'lmaydi"
        elif est.months is None:
            when = "⚠️ to'lov foizni qoplamaydi, qarz kamaymaydi"
        else:
            when = f"taxminan {months_text(est.months)} da yopiladi ({est.payoff_date.strftime('%m.%Y')}), foiz jami {money(est.total_interest, row.currency)}"
        await message.answer(
            f"✅ Qarz qo'shildi: <b>#{row.id} {esc(row.title)}</b>\n"
            f"qoldiq {money(row.balance, row.currency)}, oylik {money(row.monthly_payment, row.currency)}, "
            f"{row.annual_rate_pct}% yillik, har oyning {row.pay_day}-kuni\n{when}\n\nNatija: /plan",
            parse_mode="HTML",
        )

    @router.message(is_owner, Command("debts"))
    async def cmd_debts(message: Message) -> None:
        debts = await planner.list_debts(owner_id)
        plan = await planner.build_plan(owner_id) if debts else None
        await _send_long(message, format_debts(debts, plan))

    @router.message(is_owner, Command("paydebt"))
    async def cmd_pay_debt(message: Message) -> None:
        parts = _args(message).split(maxsplit=1)
        usage = "ℹ️ Format: <code>/paydebt &lt;raqam&gt; &lt;summa&gt;</code>, masalan <code>/paydebt 1 1,2 million</code>"
        if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
            await message.answer(usage, parse_mode="HTML")
            return
        debt = await planner.get_debt(owner_id, int(parts[0].lstrip("#")))
        if debt is None or not debt.is_active:
            await message.answer("❌ Bunday qarz topilmadi.")
            return
        try:
            amount, _cur = parse_money(parts[1], debt.currency)
            result = await planner.pay_debt(owner_id, debt.id, amount)
        except (ParseError, ValueError) as exc:
            await message.answer(str(exc) if isinstance(exc, ParseError) else usage, parse_mode="HTML")
            return
        text = (
            f"✅ To'lov qayd etildi: <b>{money(amount, debt.currency)}</b> → {esc(debt.title)}\n"
            f"Shundan taxminan foiz {money(result['interest'], debt.currency)}, asosiy qarz {money(result['principal'], debt.currency)}.\n"
            f"Qoldiq: <b>{money(result['new_balance'], debt.currency)}</b>\n"
            "Xarajat «Qarz/Kredit» kategoriyasida hisobga qo'shildi."
        )
        if result["closed"]:
            text += "\n\n🎉 Qarz to'liq yopildi!"
        else:
            text += "\n<i>Bank ko'chirmasi bilan solishtiring, kerak bo'lsa: /editdebt raqam qoldiq summa</i>"
        await message.answer(text, parse_mode="HTML")

    @router.message(is_owner, Command("editdebt"))
    async def cmd_edit_debt(message: Message) -> None:
        arg = _args(message)
        first = arg.split(maxsplit=1)[0].lstrip("#") if arg else ""
        debt = await planner.get_debt(owner_id, int(first)) if first.isdigit() else None
        try:
            debt_id, field, value = parse_editdebt(arg, debt.currency if debt else "UZS")
            row = await planner.update_debt(owner_id, debt_id, **{field: value})
        except (ParseError, ValueError) as exc:
            await message.answer(str(exc) if isinstance(exc, ParseError) else "❌ Qiymat noto'g'ri.", parse_mode="HTML")
            return
        if row is None:
            await message.answer("❌ Bunday qarz topilmadi.")
            return
        await message.answer(
            f"✅ Yangilandi: <b>#{row.id} {esc(row.title)}</b>\nqoldiq {money(row.balance, row.currency)}, "
            f"oylik {money(row.monthly_payment, row.currency)}, {row.annual_rate_pct}% yillik, har oyning {row.pay_day}-kuni",
            parse_mode="HTML",
        )

    @router.message(is_owner, Command("deldebt"))
    async def cmd_del_debt(message: Message) -> None:
        arg = _args(message).lstrip("#")
        if not arg.isdigit():
            await message.answer("ℹ️ Format: <code>/deldebt 2</code>", parse_mode="HTML")
            return
        ok = await planner.delete_debt(owner_id, int(arg))
        await message.answer("🗑 Qarz ro'yxatdan olib tashlandi." if ok else "❌ Bunday qarz topilmadi.")

    # ── plan ──
    @router.message(is_owner, Command("plan"))
    async def cmd_plan(message: Message) -> None:
        plan = await planner.build_plan(owner_id)
        if plan.income <= ZERO and not plan.debt_lines:
            await message.answer(
                "📊 Reja uchun ma'lumot kerak:\n1) Oylikni kiriting: <code>/addincome 5 Oylik 6 million</code>\n"
                "2) Qarz/kreditlarni kiriting: <code>/adddebt Kapitalbank kredit | 24 million | 1,2 million | 28 | 15</code>\n"
                "3) Ijara, internet kabi doimiy to'lovlar: <code>/addrecurring 1 Ijara 1,5 million</code>\n"
                "Keyin yana /plan yuboring.",
                parse_mode="HTML",
            )
            return
        await _send_long(message, format_plan(plan))

    @router.message(is_owner, Command("whatif"))
    async def cmd_whatif(message: Message) -> None:
        arg = _args(message)
        extra = None
        if arg:
            try:
                extra, _ = parse_money(arg)
            except ParseError as exc:
                await message.answer(str(exc), parse_mode="HTML")
                return
        plan = await planner.build_plan(owner_id, extra=extra)
        if plan.scenario is None:
            await message.answer("Qarz yo'q, taqqoslash uchun avval /adddebt bilan qarz kiriting.")
            return
        await _send_long(message, format_scenario(plan.scenario))
