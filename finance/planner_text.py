"""Uzbek text rendering and input parsing for the debt / budget planner (no Telegram or DB imports)."""
from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from finance.extractor import find_amount
from finance.planner import DEBT_KINDS, ZERO, Plan, Scenario, StrategyResult

def esc(text: str) -> str:
    """Escape &, < and > for Telegram HTML (apostrophes stay readable)."""
    return html.escape(text, quote=False)


DISCLAIMER = (
    "<i>Hisob siz kiritgan raqamlarga tayanadi, taqsimot esa umumiy qoidalar asosida. "
    "Bu shaxsiy moliyaviy maslahat emas, qarorni o'zingiz qabul qilasiz.</i>"
)


def money(v: Decimal, currency: str = "UZS") -> str:
    suffix = "USD" if currency.upper() == "USD" else "so'm"
    return f"{int(v):,}".replace(",", " ") + f" {suffix}"


def months_text(months: Optional[int]) -> str:
    if months is None:
        return "hech qachon (to'lov foizni qoplamaydi)"
    years, rest = divmod(months, 12)
    if years and rest:
        return f"{months} oy ({years} yil {rest} oy)"
    if years:
        return f"{months} oy ({years} yil)"
    return f"{months} oy"


# ─────────────────────────────────────────────────────────────────────────────
# Input parsing
# ─────────────────────────────────────────────────────────────────────────────

class ParseError(ValueError):
    """User-facing parse error (message is shown as is)."""


def parse_money(text: str, default_currency: str = "UZS") -> Tuple[Decimal, str]:
    """'24 million', '1 200 000', '300 dollar', '300' -> (amount, currency)."""
    text = text.strip()
    found = find_amount(text)
    if found is not None and found[0] > ZERO:
        amount, currency, has_unit = found
        return amount, currency if has_unit else default_currency
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return Decimal(text.replace(",", ".")), default_currency
    raise ParseError(f"Summani tushunmadim: «{text}». Masalan: 24 million, 1 200 000 yoki 300 dollar.")


def parse_percent(text: str) -> Decimal:
    clean = text.strip().replace("%", "").replace(",", ".")
    try:
        value = Decimal(clean)
    except InvalidOperation:
        raise ParseError(f"Foizni tushunmadim: «{text}». Masalan: 28 (yillik foiz).") from None
    if not ZERO <= value <= Decimal("300"):
        raise ParseError("Yillik foiz 0 dan 300 gacha bo'lishi kerak.")
    return value


def parse_day(text: str) -> int:
    clean = text.strip().rstrip("-kuni").strip("- ")
    if not clean.isdigit() or not 1 <= int(clean) <= 31:
        raise ParseError(f"Oy kuni 1 dan 31 gacha bo'lishi kerak: «{text}».")
    return int(clean)


ADDINCOME_USAGE = (
    "ℹ️ Format: <code>/addincome &lt;oy kuni&gt; &lt;nom&gt; &lt;summa&gt;</code>\n"
    "Masalan: <code>/addincome 5 Oylik 6 million</code>"
)
ADDDEBT_USAGE = (
    "ℹ️ Format (qismlar | bilan ajratiladi):\n"
    "<code>/adddebt nom | qoldiq | oylik to'lov | yillik foiz | to'lov kuni</code>\n\n"
    "Masalan:\n<code>/adddebt Kapitalbank kredit | 24 million | 1,2 million | 28 | 15</code>\n"
    "<code>/adddebt Do'stimdan qarz | 3 million | 500 ming | 0 | 1</code>\n\n"
    "Foiz va kun ixtiyoriy (foiz 0, kun 1 deb olinadi). Oxiriga tur qo'shsa bo'ladi: kredit / nasiya / shaxsiy."
)

KIND_WORDS = {
    "kredit": "BANK_CREDIT", "bank": "BANK_CREDIT", "ipoteka": "BANK_CREDIT", "avtokredit": "BANK_CREDIT",
    "nasiya": "INSTALLMENT", "rassrochka": "INSTALLMENT", "bo'lib": "INSTALLMENT",
    "shaxsiy": "PERSONAL", "qarz": "PERSONAL", "do'st": "PERSONAL",
}


def guess_kind(title: str, explicit: Optional[str], annual_rate: Decimal) -> str:
    if explicit:
        key = explicit.strip().lower()
        if key in KIND_WORDS:
            return KIND_WORDS[key]
        raise ParseError("Tur: kredit, nasiya yoki shaxsiy bo'lishi kerak.")
    lower = title.lower()
    for word, kind in KIND_WORDS.items():
        if word in lower:
            return kind
    return "BANK_CREDIT" if annual_rate > ZERO else "PERSONAL"


def parse_addincome(args: str) -> Dict[str, Any]:
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        raise ParseError(ADDINCOME_USAGE)
    day = parse_day(parts[0])
    rest = parts[1]
    found = find_amount(rest)
    if found is None or found[0] <= ZERO:
        raise ParseError(ADDINCOME_USAGE)
    amount, currency, _ = found
    title = re.sub(r"\d[\d\s.,]*\s*(?:ming|mln|million|k|so['`]?m|usd|dollar\w*|\$)?", "", rest, flags=re.IGNORECASE).strip(" -,.")
    return {"title": (title or "Daromad").capitalize(), "amount": amount, "pay_day": day, "currency": currency}


def parse_adddebt(args: str) -> Dict[str, Any]:
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3 or not parts[0]:
        raise ParseError(ADDDEBT_USAGE)
    title = parts[0]
    balance, currency = parse_money(parts[1])
    payment, _ = parse_money(parts[2], currency)
    rate = parse_percent(parts[3]) if len(parts) > 3 and parts[3] else ZERO
    day = parse_day(parts[4]) if len(parts) > 4 and parts[4] else 1
    kind = guess_kind(title, parts[5] if len(parts) > 5 and parts[5] else None, rate)
    return {"title": title, "balance": balance, "monthly_payment": payment, "annual_rate_pct": rate,
            "pay_day": day, "kind": kind, "currency": currency}


def parse_editdebt(args: str, currency: str = "UZS") -> Tuple[int, str, Any]:
    parts = args.strip().split(maxsplit=2)
    usage = "ℹ️ Format: <code>/editdebt &lt;raqam&gt; &lt;maydon&gt; &lt;qiymat&gt;</code>\nMaydonlar: nom, qoldiq, tolov, foiz, kun"
    if len(parts) < 3 or not parts[0].lstrip("#").isdigit():
        raise ParseError(usage)
    field = parts[1].lower().replace("to'lov", "tolov")
    value = parts[2].strip()
    debt_id = int(parts[0].lstrip("#"))
    if field == "nom":
        return debt_id, "title", value[:200]
    if field in ("qoldiq", "balance"):
        return debt_id, "balance", parse_money(value, currency)[0]
    if field == "tolov":
        return debt_id, "monthly_payment", parse_money(value, currency)[0]
    if field == "foiz":
        return debt_id, "annual_rate_pct", parse_percent(value)
    if field == "kun":
        return debt_id, "pay_day", parse_day(value)
    raise ParseError(usage)


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def format_debts(debts: List[Any], plan: Optional[Plan] = None) -> str:
    if not debts:
        return "Qarzlar yo'q. Qo'shish: <code>/adddebt Kapitalbank kredit | 24 million | 1,2 million | 28 | 15</code>"
    lines = ["🏦 <b>Qarzlar va kreditlar</b>\n"]
    by_id = {l.debt.id: l for l in plan.debt_lines} if plan else {}
    total = ZERO
    for d in debts:
        line = by_id.get(d.id)
        kind = DEBT_KINDS.get(d.kind, d.kind)
        rate = f", {d.annual_rate_pct}% yillik" if d.annual_rate_pct > ZERO else ", foizsiz"
        head = f"<b>#{d.id} {esc(d.title)}</b> ({kind})\n   qoldiq {money(d.balance, d.currency)}{rate}\n"
        pay = f"   oylik {money(d.monthly_payment, d.currency)}, har oyning {d.pay_day}-kuni"
        tail = ""
        if line is not None and d.monthly_payment > ZERO:
            if line.payoff.months is None:
                tail = "\n   ⚠️ to'lov foizni qoplamaydi, qarz kamaymaydi"
            else:
                tail = (f"\n   ✅ yopiladi: {line.payoff.payoff_date.strftime('%m.%Y')} ({months_text(line.payoff.months)}), "
                        f"foiz jami {money(line.payoff.total_interest)}")
        lines.append(head + pay + tail + "\n")
        if d.currency.upper() == "UZS":
            total += d.balance
    lines.append(f"<b>Jami qarz (so'mda):</b> {money(total)}")
    return "\n".join(lines)


def _strategy_line(res: StrategyResult, label: str) -> str:
    return f"• {label}: {months_text(res.months)}, foiz {money(res.total_interest)}"


def format_scenario(sc: Scenario) -> str:
    lines = [f"🔁 <b>Qo'shimcha {money(sc.extra)} / oy to'lasangiz</b>\n", _strategy_line(sc.baseline, "Faqat minimal to'lov"),
             _strategy_line(sc.avalanche, "Avalanche (foizi yuqorisidan)"), _strategy_line(sc.snowball, "Snowball (qoldig'i kichigidan)")]
    best = sc.best
    saved = sc.interest_saved(best)
    months = sc.months_saved(best)
    if sc.extra > ZERO and (months or saved > ZERO):
        label = "Avalanche" if best.name == "avalanche" else "Snowball"
        part = f"{months} oy erta" if months else "bir xil muddatda"
        lines.append(f"\n💡 {label} bilan {part} yopiladi va taxminan <b>{money(saved)}</b> foiz tejaladi.")
        if sc.avalanche.months == sc.snowball.months and sc.avalanche.total_interest != sc.snowball.total_interest:
            lines.append("<i>Avalanche matematik jihatdan arzonroq, Snowball esa kichik qarzlarni tezroq yopib motivatsiya beradi.</i>")
    elif sc.extra <= ZERO:
        lines.append("\n<i>Qo'shimcha to'lov yo'q: taqqoslash uchun /whatif 500 ming kabi summa bering.</i>")
    if sc.avalanche.payoff_order:
        lines.append("Yopilish tartibi (avalanche): " + " → ".join(esc(t) for t in sc.avalanche.payoff_order))
    return "\n".join(lines)


def format_plan(plan: Plan) -> str:
    today = plan.inputs.today
    out = [f"📊 <b>OYLIK MOLIYAVIY REJA</b> · {today.strftime('%d.%m.%Y')}\n", "<b>Oylik oqim</b>"]
    out.append(f"💰 Daromad: <b>{money(plan.income)}</b>")
    dti = f" ({plan.dti}%)" if plan.dti is not None else ""
    out.append(f"🏦 Qarz to'lovlari: {money(plan.debt_payments)}{dti}")
    out.append(f"🏠 Doimiy to'lovlar: {money(plan.fixed_total)}")
    out.append(f"🛒 O'rtacha sarf: {money(plan.variable_avg)}" + (f" (so'nggi {plan.inputs.variable_months} oy)" if plan.inputs.variable_months else ""))
    icon = "✅" if plan.free >= 0 else "🚨"
    sign = "+" if plan.free >= 0 else "−"
    out.append(f"━━━━━━━━━━\n{icon} Erkin pul: <b>{sign}{money(abs(plan.free))}</b>")

    if plan.debt_lines:
        out.append(f"\n<b>Qarzlar</b> · jami {money(plan.total_debt)}")
        for line in plan.debt_lines:
            d = line.debt
            when = (f"{line.payoff.payoff_date.strftime('%m.%Y')}, {months_text(line.payoff.months)}" if line.payoff.months is not None
                    else "yopilmaydi")
            out.append(f"• {esc(d.title)}: {money(d.balance)} → {when}")

    if plan.free > ZERO:
        p, s = plan.split_percent, plan.sample_split
        out.append("\n<b>Namuna taqsimot</b> (umumiy qoida; erkin pulni qanday bo'lish mumkin)")
        if s["qarz"] > ZERO:
            out.append(f"• Qo'shimcha qarz to'lovi: {money(s['qarz'])} ({p['qarz']}%)")
        out.append(f"• Zaxira jamg'arma: {money(s['zaxira'])} ({p['zaxira']}%)")
        out.append(f"• Erkin sarf: {money(s['erkin'])} ({p['erkin']}%)")
    out.append(f"\n🛟 Zaxira maqsadi: 1 oylik zarur xarajat {money(plan.emergency_1m)}, 3 oylik {money(plan.emergency_3m)}")
    if plan.spend_budget > ZERO:
        out.append(f"📅 Kunlik xavfsiz sarf limiti: <b>{money(plan.daily_limit)}</b> (zaxira va qo'shimcha to'lovdan keyin)")

    if plan.scenario is not None:
        out.append("\n" + format_scenario(plan.scenario))

    out.append(f"\n<b>Keyingi 30 kun:</b> majburiy to'lovlar {money(plan.due_30d)}, kutilayotgan daromad {money(plan.income_30d)}")
    upcoming = [e for e in plan.calendar if e.kind != "income"][:5]
    for e in upcoming:
        out.append(f"  {e.date.strftime('%d.%m')} · {esc(e.title)} · {money(e.amount)}")

    if plan.warnings:
        out.append("\n<b>Diqqat</b>")
        out.extend(f"⚠️ {esc(w)}" for w in plan.warnings)
    out.append("\n" + DISCLAIMER)
    return "\n".join(out)
