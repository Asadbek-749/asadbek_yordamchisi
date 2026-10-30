"""Debt / budget planning engine: hand-checked numbers."""
from __future__ import annotations

import datetime
import math
from decimal import Decimal

from finance.planner import (
    Debt,
    FixedItem,
    IncomeItem,
    PlanInputs,
    add_months,
    build_calendar,
    build_plan,
    compare_strategies,
    payoff_single,
    sample_split_percent,
    simulate,
)

D = Decimal
TODAY = datetime.date(2026, 10, 15)


def debt(i, title, balance, rate, pay, day=20):
    return Debt(i, title, "BANK_CREDIT", D(balance), D(rate), D(pay), day)


def test_single_credit_matches_the_annuity_formula():
    # 12 mln at 24% a year (2% a month), 1 mln a month: n = -ln(1 - rB/P) / ln(1 + r) = 13.86 -> 14 months
    expected = math.ceil(-math.log(1 - 0.02 * 12_000_000 / 1_000_000) / math.log(1.02))
    res = payoff_single(D(12_000_000), D(24), D(1_000_000), TODAY)
    assert expected == 14 and res.months == 14
    assert res.payoff_date == add_months(TODAY, 14)
    assert res.total_paid - D(12_000_000) == res.total_interest  # every so'm accounted for
    assert D(1_000_000) < res.total_interest < D(2_000_000)


def test_zero_interest_personal_debt():
    res = payoff_single(D(3_000_000), D(0), D(500_000), TODAY)
    assert res.months == 6 and res.total_interest == 0 and res.total_paid == D(3_000_000)


def test_payment_that_does_not_cover_interest_never_pays_off():
    assert payoff_single(D(10_000_000), D(36), D(200_000), TODAY).months is None  # interest is 300 000
    assert payoff_single(D(10_000_000), D(36), D(300_000), TODAY).months is None  # exactly the interest
    assert payoff_single(D(1_000_000), D(0), D(0), TODAY).months is None  # no payment at all
    assert payoff_single(D(0), D(36), D(0), TODAY).months == 0


def test_add_months_clamps_day():
    assert add_months(datetime.date(2026, 1, 31), 1) == datetime.date(2026, 2, 28)
    assert add_months(datetime.date(2026, 11, 15), 3) == datetime.date(2027, 2, 15)


def test_avalanche_saves_interest_and_snowball_closes_small_debt_first():
    a = debt(1, "A", 5_000_000, 36, 300_000)
    b = debt(2, "B", 1_000_000, 0, 100_000)
    sc = compare_strategies([a, b], D(500_000))

    assert sc.avalanche.payoff_order[0] == "A"
    assert sc.snowball.payoff_order[0] == "B"
    assert sc.avalanche.total_interest <= sc.snowball.total_interest < sc.baseline.total_interest
    assert sc.avalanche.months < sc.baseline.months and sc.snowball.months < sc.baseline.months
    assert sc.interest_saved(sc.avalanche) > 0 and sc.months_saved(sc.avalanche) > 0
    assert sc.best.name == "avalanche"


def test_simulation_conserves_money():
    a = debt(1, "A", 2_000_000, 24, 400_000)
    res = simulate([a], D(0), "avalanche")
    single = payoff_single(a.balance, a.annual_rate_pct, a.monthly_payment, TODAY)
    assert (res.months, res.total_interest) == (single.months, single.total_interest)


def inputs(**kw):
    base = dict(
        incomes=[IncomeItem("Oylik", D(6_000_000), 5)],
        debts=[debt(1, "A", 5_000_000, 36, 300_000), debt(2, "B", 1_000_000, 0, 100_000)],
        fixed=[FixedItem("Ijara", D(1_500_000), 1)],
        variable_avg=D(1_200_000),
        variable_months=3,
        today=TODAY,
    )
    base.update(kw)
    return PlanInputs(**base)


def test_monthly_plan_numbers():
    plan = build_plan(inputs())
    assert plan.income == D(6_000_000) and plan.debt_payments == D(400_000)
    assert plan.free == D(2_900_000)  # 6.0 - 0.4 - 1.5 - 1.2 mln
    assert plan.dti == D("6.7")
    assert plan.split_percent == {"qarz": 40, "zaxira": 40, "erkin": 20}
    assert plan.sample_split == {"qarz": D(1_160_000), "zaxira": D(1_160_000), "erkin": D(580_000)}
    assert plan.spend_budget == D(1_780_000) and plan.daily_limit == D(57_419)  # / 31 days of October
    assert plan.emergency_1m == D(3_100_000) and plan.emergency_3m == D(9_300_000)
    assert plan.total_debt == D(6_000_000)
    assert plan.due_30d == D(1_900_000) and plan.income_30d == D(6_000_000)
    assert plan.warnings == []
    assert plan.scenario.extra == D(1_160_000)


def test_deficit_and_heavy_debt_warnings():
    plan = build_plan(inputs(
        incomes=[IncomeItem("Oylik", D(2_000_000), 5)],
        debts=[debt(1, "Kredit", 30_000_000, 30, 1_500_000)],
        fixed=[FixedItem("Ijara", D(1_000_000), 1)],
        variable_avg=D(0), variable_months=0,
    ))
    text = " ".join(plan.warnings)
    assert plan.free == D(-500_000)
    assert "Oylik yetmayapti" in text and "75" in text and "Qarz yuki juda yuqori" in text
    assert "yozuv yo'q" in text  # no spending history
    assert plan.sample_split == {"qarz": 0, "zaxira": 0, "erkin": 0} and plan.spend_budget == 0


def test_negative_amortisation_and_missing_payment_are_flagged():
    plan = build_plan(inputs(debts=[
        debt(1, "Foizli", 10_000_000, 36, 200_000),
        debt(2, "Do'stga", 2_000_000, 0, 0),
    ]))
    text = " ".join(plan.warnings)
    assert "foizni ham qoplamayapti" in text and "oylik to'lov kiritilmagan" in text


def test_calendar_clamps_to_month_length_and_sorts():
    inp = inputs(
        today=datetime.date(2026, 2, 1),
        incomes=[IncomeItem("Oylik", D(1), 31)],
        debts=[debt(1, "A", 1_000, 0, 10, day=31)],
        fixed=[],
    )
    days = [(e.date, e.kind) for e in build_calendar(inp, 30)]
    assert (datetime.date(2026, 2, 28), "income") in days and (datetime.date(2026, 2, 28), "debt") in days
    assert days == sorted(days, key=lambda t: (t[0], t[1] != "income"))


def test_sample_split_reference_values():
    assert sample_split_percent(None, False) == {"qarz": 0, "zaxira": 60, "erkin": 40}
    assert sample_split_percent(D("36"), True) == {"qarz": 60, "zaxira": 30, "erkin": 10}
    assert sample_split_percent(D("10"), True)["qarz"] == 40
