"""Finance dashboard: aggregates confirmed transactions and renders a single PNG overview.

Only CONFIRMED / EDITED transactions count. Main figures are in so'm (UZS); other currencies
are listed separately and never added into the so'm totals.
"""
from __future__ import annotations

import calendar
import datetime
import io
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from discipline.db import get_session
from finance.models import FinancialBudget, FinancialTransaction
from finance.timeutil import UZ_MONTHS, month_label, today_local

ZERO = Decimal("0")

UZ_WEEKDAYS = ("Du", "Se", "Chor", "Pay", "Ju", "Sha", "Yak")

KIND_TITLES = {"day": "Bugungi", "week": "Haftalik", "month": "Oylik"}


@dataclass
class DashboardData:
    kind: str  # "day" | "week" | "month"
    start: datetime.date
    end: datetime.date
    title: str
    income: Decimal = ZERO
    expense: Decimal = ZERO
    tx_count: int = 0
    daily: List[Tuple[datetime.date, Decimal, Decimal]] = field(default_factory=list)  # chart range, UZS
    expense_categories: List[Tuple[str, Decimal]] = field(default_factory=list)
    income_sources: List[Tuple[str, Decimal]] = field(default_factory=list)
    avg_daily_expense: Decimal = ZERO
    top_day: Optional[Tuple[datetime.date, Decimal]] = None
    projected_expense: Optional[Decimal] = None  # month view only
    budget_limit: Optional[Decimal] = None
    other_currencies: Dict[str, Dict[str, Decimal]] = field(default_factory=dict)

    @property
    def net(self) -> Decimal:
        return self.income - self.expense

    @property
    def budget_pct(self) -> Optional[Decimal]:
        if self.budget_limit and self.budget_limit > ZERO:
            return self.expense / self.budget_limit * Decimal("100")
        return None


def resolve_period(kind: str, today: datetime.date) -> Tuple[datetime.date, datetime.date, datetime.date, datetime.date]:
    """Return (start, end, chart_start, chart_end) for day / week / month."""
    if kind == "day":
        return today, today, today - datetime.timedelta(days=6), today
    if kind == "week":
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        return monday, today, monday, sunday
    last = calendar.monthrange(today.year, today.month)[1]
    first = today.replace(day=1)
    return first, today, first, today.replace(day=last)


async def collect_dashboard_data(
    owner_id: int, kind: str = "month", today: Optional[datetime.date] = None,
    year: Optional[int] = None, month: Optional[int] = None,
) -> DashboardData:
    """Aggregate numbers for the dashboard. For kind='month' a past month can be requested via year/month."""
    today = today or today_local()
    if kind == "month" and year and month and (year, month) != (today.year, today.month):
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        start, end, c_start, c_end = first, last, first, last
        title = f"{month_label(year, month).capitalize()} — oylik dashboard"
        elapsed_end = last
    else:
        start, end, c_start, c_end = resolve_period(kind, today)
        elapsed_end = min(end, today)
        if kind == "month":
            title = f"{month_label(today.year, today.month).capitalize()} — oylik dashboard"
        elif kind == "week":
            title = f"Haftalik dashboard ({start.strftime('%d.%m')} – {c_end.strftime('%d.%m.%Y')})"
        else:
            title = f"Bugungi dashboard ({today.strftime('%d.%m.%Y')})"

    fetch_start, fetch_end = min(start, c_start), max(end, c_end)
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(FinancialTransaction).where(
                        FinancialTransaction.owner_id == owner_id,
                        FinancialTransaction.transaction_date >= fetch_start,
                        FinancialTransaction.transaction_date <= fetch_end,
                        FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                    )
                )
            ).scalars().all()
        )
        budget_limit = None
        if kind == "month":
            key = f"{start.year}-{start.month:02d}"
            budget = (
                await session.execute(
                    select(FinancialBudget).where(
                        FinancialBudget.owner_id == owner_id,
                        FinancialBudget.period_key == key,
                        FinancialBudget.category_id.is_(None),
                    )
                )
            ).scalars().first()
            budget_limit = budget.limit_amount if budget else None

    data = DashboardData(kind=kind, start=start, end=end, title=title, budget_limit=budget_limit)

    per_day: Dict[datetime.date, List[Decimal]] = {}
    d = c_start
    while d <= c_end:
        per_day[d] = [ZERO, ZERO]
        d += datetime.timedelta(days=1)

    exp_cat: Dict[str, Decimal] = {}
    inc_src: Dict[str, Decimal] = {}
    for tx in rows:
        cur = (tx.currency or "UZS").upper()
        in_range = start <= tx.transaction_date <= end
        if cur != "UZS":
            if in_range:
                bucket = data.other_currencies.setdefault(cur, {"income": ZERO, "expense": ZERO})
                bucket["income" if tx.type == "INCOME" else "expense"] += tx.amount
                data.tx_count += 1
            continue
        if tx.transaction_date in per_day:
            per_day[tx.transaction_date][0 if tx.type == "INCOME" else 1] += tx.amount
        if not in_range:
            continue
        data.tx_count += 1
        name = tx.category_name or "Boshqa"
        if tx.type == "INCOME":
            data.income += tx.amount
            inc_src[name] = inc_src.get(name, ZERO) + tx.amount
        elif tx.type == "EXPENSE":
            data.expense += tx.amount
            exp_cat[name] = exp_cat.get(name, ZERO) + tx.amount

    data.daily = [(day, v[0], v[1]) for day, v in sorted(per_day.items())]
    data.expense_categories = sorted(exp_cat.items(), key=lambda kv: kv[1], reverse=True)
    data.income_sources = sorted(inc_src.items(), key=lambda kv: kv[1], reverse=True)

    days_elapsed = max((elapsed_end - start).days + 1, 1)
    data.avg_daily_expense = (data.expense / days_elapsed).quantize(Decimal("1"))
    spend_days = [(day, e) for day, _, e in data.daily if start <= day <= end and e > ZERO]
    if spend_days:
        data.top_day = max(spend_days, key=lambda t: t[1])
    if kind == "month":
        month_days = calendar.monthrange(start.year, start.month)[1]
        data.projected_expense = (
            data.avg_daily_expense * month_days if end < start.replace(day=month_days) else data.expense
        )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

C_INCOME = "#2E9E6B"
C_EXPENSE = "#E4572E"
C_NET = "#3B6FD6"
C_INK = "#1F2933"
C_MUTED = "#6B7785"
C_BG = "#F4F6F9"
C_CARD = "#FFFFFF"
PALETTE = ["#3B6FD6", "#E4572E", "#2E9E6B", "#F2A541", "#8E5BD0", "#17A2B8", "#D6457A", "#7A8B99"]


def _money(v: Decimal) -> str:
    return f"{int(v):,}".replace(",", " ")


def _short(v: float, _pos=None) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f} mln".replace(".0 ", " ")
    if a >= 1_000:
        return f"{v / 1_000:.0f} ming"
    return f"{v:.0f}"


def render_dashboard_png(data: DashboardData) -> bytes:
    """Draw KPI cards + daily bars + category donut + cumulative spending. Returns PNG bytes."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.gridspec import GridSpec
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.ticker import FuncFormatter

    fig = Figure(figsize=(13, 9), dpi=130, facecolor=C_BG)
    FigureCanvasAgg(fig)
    gs = GridSpec(3, 12, figure=fig, height_ratios=[0.9, 2.6, 2.2], hspace=0.62, wspace=1.6,
                  left=0.04, right=0.97, top=0.88, bottom=0.06)

    fig.text(0.04, 0.945, data.title, fontsize=21, fontweight="bold", color=C_INK, va="center")
    fig.text(0.04, 0.905, f"{data.start.strftime('%d.%m.%Y')} – {data.end.strftime('%d.%m.%Y')}  ·  "
             f"{data.tx_count} ta tasdiqlangan yozuv  ·  so'mda", fontsize=10.5, color=C_MUTED, va="center")

    # ── KPI cards ──
    net_color = C_NET if data.net >= 0 else C_EXPENSE
    kpis = [
        ("Daromad", _money(data.income), C_INCOME),
        ("Xarajat", _money(data.expense), C_EXPENSE),
        ("Sof balans", ("+" if data.net >= 0 else "−") + _money(abs(data.net)), net_color),
        ("O'rtacha kunlik xarajat", _money(data.avg_daily_expense), C_INK),
    ]
    for i, (label, value, color) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, i * 3:(i + 1) * 3])
        ax.set_axis_off()
        ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.08",
                                    transform=ax.transAxes, facecolor=C_CARD, edgecolor="#E2E6EB", linewidth=1))
        ax.add_patch(FancyBboxPatch((0, 0), 0.025, 1, boxstyle="square,pad=0", transform=ax.transAxes,
                                    facecolor=color, edgecolor="none"))
        ax.text(0.09, 0.70, label, transform=ax.transAxes, fontsize=10.5, color=C_MUTED, va="center")
        ax.text(0.09, 0.32, value, transform=ax.transAxes, fontsize=19, fontweight="bold", color=color, va="center")

    # ── daily income vs expense bars ──
    ax = fig.add_subplot(gs[1, :8])
    _card(ax)
    days = [d for d, _, _ in data.daily]
    inc = [float(i) for _, i, _ in data.daily]
    exp = [float(e) for _, _, e in data.daily]
    xs = list(range(len(days)))
    w = 0.4
    ax.bar([x - w / 2 for x in xs], inc, w, color=C_INCOME, label="Daromad")
    ax.bar([x + w / 2 for x in xs], exp, w, color=C_EXPENSE, label="Xarajat")
    if len(days) <= 8:
        ax.set_xticks(xs, [f"{UZ_WEEKDAYS[d.weekday()]}\n{d.day}" for d in days])
    else:
        step = 2 if len(days) <= 16 else 3
        ax.set_xticks(xs[::step], [str(d.day) for d in days[::step]])
    ax.yaxis.set_major_formatter(FuncFormatter(_short))
    ax.set_title("Kunlik daromad va xarajat", loc="left", fontsize=12.5, fontweight="bold", color=C_INK, pad=10)
    if any(inc) or any(exp):
        ax.legend(frameon=False, fontsize=9.5, loc="upper right", ncol=2)
    else:
        ax.text(0.5, 0.5, "Bu davrda tasdiqlangan yozuv yo'q", transform=ax.transAxes, ha="center", color=C_MUTED)

    # ── expense categories donut ──
    ax = fig.add_subplot(gs[1, 8:])
    _card(ax, axes=False)
    ax.set_title("Xarajat kategoriyalari", loc="left", fontsize=12.5, fontweight="bold", color=C_INK, pad=10)
    cats = data.expense_categories
    if cats:
        top = cats[:6]
        rest = sum((a for _, a in cats[6:]), ZERO)
        if rest > ZERO:
            top = top + [("Boshqalar", rest)]
        labels = [_plain(n) for n, _ in top]
        vals = [float(a) for _, a in top]
        colors = PALETTE[: len(vals)]
        ax.pie(vals, colors=colors, startangle=90, counterclock=False,
               wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 2}, radius=1.0, center=(0, 0.05))
        ax.text(0, 0.05, _short(float(data.expense)), ha="center", va="center", fontsize=14, fontweight="bold", color=C_INK)
        total = sum(vals)
        ax.legend(handles=[_dot(c) for c in colors],
                  labels=[f"{n}  {v / total * 100:.0f}%" for n, v in zip(labels, vals)], loc="upper center",
                  bbox_to_anchor=(0.5, 0.0), frameon=False, fontsize=8.5, ncol=2, columnspacing=1.2)
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-1.1, 1.15)
    else:
        ax.text(0.5, 0.5, "Xarajat yo'q", transform=ax.transAxes, ha="center", color=C_MUTED)

    # ── cumulative spending (+ budget / projection) ──
    ax = fig.add_subplot(gs[2, :8])
    _card(ax)
    run, cum_x, cum_y = ZERO, [], []
    for i, (day, _, e) in enumerate(data.daily):
        if day > data.end:
            break  # no line for days that have not happened yet
        run += e
        cum_x.append(i)
        cum_y.append(float(run))
    ax.plot(cum_x, cum_y, color=C_EXPENSE, linewidth=2.6, marker="o", markersize=3.5 if len(xs) > 12 else 6)
    ax.fill_between(cum_x, cum_y, color=C_EXPENSE, alpha=0.10)
    if data.budget_limit:
        ax.axhline(float(data.budget_limit), color=C_NET, linestyle="--", linewidth=1.6)
        ax.text(0, float(data.budget_limit), f" Byudjet {_short(float(data.budget_limit))}", color=C_NET,
                va="bottom", fontsize=9.5)
    if data.projected_expense is not None and data.projected_expense > data.expense:
        ax.axhline(float(data.projected_expense), color=C_MUTED, linestyle=":", linewidth=1.4)
        ax.text(len(xs) - 1, float(data.projected_expense), f"Oy oxirigacha taxmin {_short(float(data.projected_expense))} ",
                color=C_MUTED, va="bottom", ha="right", fontsize=9.5)
    ax.set_xticks(xs[:: max(len(xs) // 8, 1)], [str(days[i].day) for i in xs[:: max(len(xs) // 8, 1)]])
    ax.yaxis.set_major_formatter(FuncFormatter(_short))
    ax.set_title("Jami xarajat o'sishi (yig'ma)", loc="left", fontsize=12.5, fontweight="bold", color=C_INK, pad=10)

    # ── insights panel ──
    ax = fig.add_subplot(gs[2, 8:])
    _card(ax, axes=False)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Muhim ko'rsatkichlar", loc="left", fontsize=12.5, fontweight="bold", color=C_INK, pad=10)
    lines: List[Tuple[str, str]] = []
    if data.top_day:
        lines.append(("Eng qimmat kun", f"{data.top_day[0].strftime('%d.%m')} — {_money(data.top_day[1])}"))
    if data.expense_categories:
        n, a = data.expense_categories[0]
        lines.append(("Eng katta kategoriya", f"{_plain(n)} — {_money(a)}"))
    if data.income_sources:
        n, a = data.income_sources[0]
        lines.append(("Asosiy daromad", f"{_plain(n)} — {_money(a)}"))
    if data.budget_pct is not None:
        lines.append(("Byudjet ishlatildi", f"{data.budget_pct:.0f}%"))
    if data.projected_expense is not None and data.kind == "month":
        lines.append(("Oy oxirigacha taxmin", _money(data.projected_expense)))
    for cur, v in data.other_currencies.items():
        lines.append((f"{cur} (alohida)", f"+{_money(v['income'])} / −{_money(v['expense'])}"))
    if not lines:
        lines.append(("", "Hozircha ma'lumot yo'q"))
    y = 0.90
    for label, value in lines[:6]:
        if label:
            ax.text(0.05, y, label, fontsize=9, color=C_MUTED, va="center", transform=ax.transAxes)
            ax.text(0.05, y - 0.065, value, fontsize=11, fontweight="bold", color=C_INK, va="center",
                    transform=ax.transAxes)
        else:
            ax.text(0.05, y, value, fontsize=11, color=C_MUTED, va="center", transform=ax.transAxes)
        y -= 0.152

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    return buf.getvalue()


def _plain(name: str) -> str:
    """Drop the leading emoji from a category display name (the chart font has no emoji glyphs)."""
    parts = name.split(" ", 1)
    return parts[1] if len(parts) == 2 and not parts[0].isalnum() else name


def _dot(color: str):
    from matplotlib.lines import Line2D

    return Line2D([0], [0], marker="o", linestyle="", markersize=8, markerfacecolor=color, markeredgecolor=color)


def _card(ax, axes: bool = True) -> None:
    ax.set_facecolor(C_CARD)
    for side, spine in ax.spines.items():
        spine.set_visible(False)
    if axes:
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#D5DAE0")
        ax.tick_params(colors=C_MUTED, labelsize=9, length=0)
        ax.grid(axis="y", color="#EEF0F3", linewidth=1)
        ax.set_axisbelow(True)
    else:
        ax.set_xticks([])
        ax.set_yticks([])
