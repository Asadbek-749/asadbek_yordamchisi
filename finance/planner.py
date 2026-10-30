"""Debt and monthly-budget planning engine (pure functions, Decimal only, no database).

Everything here is arithmetic on the numbers the owner entered. The percentages used for the
"sample split" are common rules of thumb, shown as reference points, not personal advice.
All amounts are in so'm (UZS); callers convert other currencies before building the inputs.
"""
from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MAX_MONTHS = 600  # 50 years: anything longer is treated as "never"

DEBT_KINDS = {"BANK_CREDIT": "Bank krediti", "INSTALLMENT": "Nasiya / bo'lib to'lash", "PERSONAL": "Shaxsiy qarz"}

# Reference thresholds for the debt-to-income ratio (monthly debt payments / monthly income)
DTI_CAUTION = Decimal("35")
DTI_DANGER = Decimal("40")


def q(v: Decimal) -> Decimal:
    """Round to whole so'm (half up)."""
    return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def monthly_interest(balance: Decimal, annual_rate_pct: Decimal) -> Decimal:
    return q(balance * annual_rate_pct / Decimal("1200"))


@dataclass
class Debt:
    id: int
    title: str
    kind: str
    balance: Decimal
    annual_rate_pct: Decimal
    monthly_payment: Decimal
    pay_day: int


@dataclass
class IncomeItem:
    title: str
    amount: Decimal
    pay_day: int


@dataclass
class FixedItem:
    title: str
    amount: Decimal
    day: int


@dataclass
class Payoff:
    """Result of paying one debt with its own fixed monthly payment."""
    months: Optional[int]  # None = never (payment does not cover the interest)
    total_interest: Decimal
    total_paid: Decimal
    payoff_date: Optional[datetime.date]


def payoff_single(balance: Decimal, annual_rate_pct: Decimal, payment: Decimal, today: datetime.date) -> Payoff:
    """Months, interest and payoff date for a single debt paid with a fixed monthly payment."""
    if balance <= ZERO:
        return Payoff(0, ZERO, ZERO, today)
    bal, interest_total, paid, months = balance, ZERO, ZERO, 0
    while bal > ZERO:
        interest = monthly_interest(bal, annual_rate_pct)
        if months >= MAX_MONTHS or payment <= ZERO or (interest > ZERO and payment <= interest):
            return Payoff(None, interest_total, paid, None)  # the balance would never reach zero
        bal += interest
        interest_total += interest
        pay = min(payment, bal)
        bal -= pay
        paid += pay
        months += 1
    return Payoff(months, interest_total, paid, add_months(today, months))


def add_months(d: datetime.date, months: int) -> datetime.date:
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    return datetime.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


@dataclass
class StrategyResult:
    name: str
    months: Optional[int]
    total_interest: Decimal
    payoff_order: List[str] = field(default_factory=list)


def simulate(debts: List[Debt], extra: Decimal, strategy: str) -> StrategyResult:
    """Pay all debts together.

    "none": every debt only gets its own minimum payment (no rollover), the baseline.
    "avalanche" / "snowball": the whole budget (sum of minimums + extra) is spent every month:
    minimums first, the rest on the target debt (highest rate / smallest balance), and the payments
    freed by closed debts roll over to the next target.
    """
    by_id = {d.id: d for d in debts}
    state = {d.id: d.balance for d in debts if d.balance > ZERO}
    if not state:
        return StrategyResult(strategy, 0, ZERO, [])
    budget = sum((d.monthly_payment for d in debts if d.balance > ZERO), ZERO) + extra
    total_interest, months, order = ZERO, 0, []

    def target_key(did: int):
        d = by_id[did]
        if strategy == "avalanche":
            return (-d.annual_rate_pct, state[did])
        return (state[did], -d.annual_rate_pct)

    while state and months < MAX_MONTHS:
        months += 1
        for did in list(state):
            interest = monthly_interest(state[did], by_id[did].annual_rate_pct)
            state[did] += interest
            total_interest += interest

        remaining = budget
        for did in list(state):  # minimums on every open debt
            pay = min(by_id[did].monthly_payment, state[did])
            state[did] -= pay
            remaining -= pay
        if strategy != "none":  # everything else goes to the target debts, in priority order
            for did in sorted(state, key=target_key):
                if remaining <= ZERO:
                    break
                pay = min(remaining, state[did])
                state[did] -= pay
                remaining -= pay

        for did in [k for k, v in state.items() if v <= ZERO]:
            order.append(by_id[did].title)
            del state[did]

    return StrategyResult(strategy, None if state else months, total_interest, order)


@dataclass
class Scenario:
    baseline: StrategyResult
    avalanche: StrategyResult
    snowball: StrategyResult
    extra: Decimal

    @property
    def best(self) -> StrategyResult:
        ranked = sorted(
            (self.avalanche, self.snowball),
            key=lambda r: (r.months if r.months is not None else 10**6, r.total_interest),
        )
        return ranked[0]

    def interest_saved(self, result: StrategyResult) -> Decimal:
        return self.baseline.total_interest - result.total_interest

    def months_saved(self, result: StrategyResult) -> Optional[int]:
        if self.baseline.months is None or result.months is None:
            return None
        return self.baseline.months - result.months


def compare_strategies(debts: List[Debt], extra: Decimal) -> Scenario:
    """Baseline (minimums only) versus avalanche and snowball with an extra monthly amount."""
    return Scenario(
        baseline=simulate(debts, ZERO, "none"),
        avalanche=simulate(debts, extra, "avalanche"),
        snowball=simulate(debts, extra, "snowball"),
        extra=extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The monthly plan
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlanInputs:
    incomes: List[IncomeItem]
    debts: List[Debt]
    fixed: List[FixedItem]
    variable_avg: Decimal  # average monthly variable spending (debt payments excluded)
    variable_months: int  # how many months of data the average is based on
    today: datetime.date
    notes: List[str] = field(default_factory=list)  # e.g. skipped USD items


@dataclass
class CalendarEvent:
    date: datetime.date
    title: str
    amount: Decimal
    kind: str  # "income" | "debt" | "fixed"


@dataclass
class DebtLine:
    debt: Debt
    payoff: Payoff
    interest_per_month: Decimal


@dataclass
class Plan:
    inputs: PlanInputs
    income: Decimal
    debt_payments: Decimal
    fixed_total: Decimal
    variable_avg: Decimal
    free: Decimal  # income - debt - fixed - variable
    dti: Optional[Decimal]
    total_debt: Decimal
    debt_lines: List[DebtLine]
    emergency_1m: Decimal
    emergency_3m: Decimal
    sample_split: Dict[str, Decimal]  # reference split of positive free money
    split_percent: Dict[str, int]
    spend_budget: Decimal  # money left for wants after obligations and the sample split
    daily_limit: Decimal
    calendar: List[CalendarEvent]
    due_30d: Decimal
    income_30d: Decimal
    warnings: List[str]
    scenario: Optional[Scenario]


def effective_day(day: int, year: int, month: int) -> int:
    return min(max(day, 1), calendar.monthrange(year, month)[1])


def build_calendar(inputs: PlanInputs, days: int = 60) -> List[CalendarEvent]:
    """Dated incomes and obligations for the next `days` days."""
    start, end = inputs.today, inputs.today + datetime.timedelta(days=days)
    events: List[CalendarEvent] = []

    def add(title: str, amount: Decimal, day: int, kind: str) -> None:
        y, m = start.year, start.month
        for _ in range(4):
            d = datetime.date(y, m, effective_day(day, y, m))
            if start <= d <= end:
                events.append(CalendarEvent(d, title, amount, kind))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    for inc in inputs.incomes:
        add(inc.title, inc.amount, inc.pay_day, "income")
    for debt in inputs.debts:
        if debt.balance > ZERO and debt.monthly_payment > ZERO:
            add(debt.title, debt.monthly_payment, debt.pay_day, "debt")
    for item in inputs.fixed:
        add(item.title, item.amount, item.day, "fixed")
    return sorted(events, key=lambda e: (e.date, e.kind != "income"))


def sample_split_percent(dti: Optional[Decimal], has_debt: bool) -> Dict[str, int]:
    """Reference split of the free money (%): extra debt payment / emergency fund / flexible.

    A rule of thumb that leans toward debts when the debt load is heavy. It is a starting point only.
    """
    if not has_debt:
        return {"qarz": 0, "zaxira": 60, "erkin": 40}
    if dti is not None and dti >= DTI_CAUTION:
        return {"qarz": 60, "zaxira": 30, "erkin": 10}
    return {"qarz": 40, "zaxira": 40, "erkin": 20}


def build_plan(inputs: PlanInputs, extra_override: Optional[Decimal] = None) -> Plan:
    today = inputs.today
    income = sum((i.amount for i in inputs.incomes), ZERO)
    active_debts = [d for d in inputs.debts if d.balance > ZERO]
    debt_payments = sum((d.monthly_payment for d in active_debts), ZERO)
    fixed_total = sum((f.amount for f in inputs.fixed), ZERO)
    variable = inputs.variable_avg
    free = income - debt_payments - fixed_total - variable
    dti = (debt_payments / income * HUNDRED).quantize(Decimal("0.1")) if income > ZERO else None
    total_debt = sum((d.balance for d in active_debts), ZERO)

    lines = [
        DebtLine(d, payoff_single(d.balance, d.annual_rate_pct, d.monthly_payment, today), monthly_interest(d.balance, d.annual_rate_pct))
        for d in active_debts
    ]

    essentials = fixed_total + variable + debt_payments
    percent = sample_split_percent(dti, bool(active_debts))
    positive_free = max(free, ZERO)
    split = {k: q(positive_free * Decimal(v) / HUNDRED) for k, v in percent.items()}
    spend_budget = max(income - debt_payments - fixed_total - split["qarz"] - split["zaxira"], ZERO)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    daily_limit = q(spend_budget / days_in_month)

    events = build_calendar(inputs, 60)
    horizon = today + datetime.timedelta(days=30)
    due_30d = sum((e.amount for e in events if e.kind != "income" and e.date <= horizon), ZERO)
    income_30d = sum((e.amount for e in events if e.kind == "income" and e.date <= horizon), ZERO)

    warnings: List[str] = []
    if income <= ZERO:
        warnings.append("Daromad kiritilmagan: hisob-kitob to'liq emas. /addincome bilan oylikni qo'shing.")
    if income > ZERO and free < ZERO:
        warnings.append(f"Oylik yetmayapti: majburiy to'lovlar va o'rtacha sarf daromaddan {abs(free):,.0f} so'm ko'p.".replace(",", " "))
    if dti is not None and dti >= DTI_DANGER:
        warnings.append(f"Qarz yuki juda yuqori: daromadning {dti}% i qarz to'lovlariga ketadi (odatda 40% dan yuqori xavfli hisoblanadi).")
    elif dti is not None and dti >= DTI_CAUTION:
        warnings.append(f"Qarz yuki yuqori: daromadning {dti}% i qarz to'lovlariga ketadi (35% dan oshmagani ma'qul).")
    for line in lines:
        if line.payoff.months is None and line.debt.monthly_payment > ZERO:
            warnings.append(f"«{line.debt.title}»: oylik to'lov foizni ham qoplamayapti, qarz kamaymaydi.")
        if line.debt.monthly_payment <= ZERO:
            warnings.append(f"«{line.debt.title}»: oylik to'lov kiritilmagan, yopilish muddatini hisoblab bo'lmaydi.")
    if income_30d > ZERO and due_30d > income_30d:
        warnings.append("Keyingi 30 kunda majburiy to'lovlar kutilayotgan daromaddan ko'p.")
    if inputs.variable_months == 0:
        warnings.append("O'zgaruvchan xarajatlar bo'yicha tasdiqlangan yozuv yo'q: sarf 0 deb olindi, natija optimistik bo'lishi mumkin.")
    warnings.extend(inputs.notes)

    scenario = None
    if active_debts:
        extra = extra_override if extra_override is not None else split["qarz"]
        scenario = compare_strategies(active_debts, extra)

    return Plan(
        inputs=inputs, income=income, debt_payments=debt_payments, fixed_total=fixed_total,
        variable_avg=variable, free=free, dti=dti, total_debt=total_debt, debt_lines=lines,
        emergency_1m=essentials, emergency_3m=essentials * 3, sample_split=split, split_percent=percent,
        spend_budget=spend_budget, daily_limit=daily_limit, calendar=events, due_30d=due_30d,
        income_30d=income_30d, warnings=warnings, scenario=scenario,
    )
