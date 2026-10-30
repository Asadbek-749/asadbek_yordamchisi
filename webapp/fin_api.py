"""Owner-only finance API for the "Nargis Moliya" Telegram Mini App.

Every request must carry Telegram's signed `initData` (header `Authorization: tma <initData>`).
The signature is verified with the bot token, the data must be fresh, and the Telegram user id
must be the configured owner. Nothing here is reachable without that.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import select

from discipline.db import get_session
from finance.dashboard import DashboardData, collect_dashboard_data
from finance.extractor import find_amount
from finance.models import FinancialTransaction
from finance.service import FinanceService
from finance.timeutil import today_local

logger = logging.getLogger("webapp.fin_api")

INIT_DATA_MAX_AGE = 24 * 3600


def verify_init_data(
    init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE, now: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Validate Telegram Mini App initData (HMAC-SHA256). Returns the user dict, or None if invalid/stale."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    if (now if now is not None else time.time()) - auth_date > max_age:
        return None
    return user if isinstance(user, dict) and "id" in user else None


def _num(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def dashboard_to_json(d: DashboardData) -> Dict[str, Any]:
    return {
        "kind": d.kind,
        "title": d.title,
        "start": d.start.isoformat(),
        "end": d.end.isoformat(),
        "income": _num(d.income),
        "expense": _num(d.expense),
        "net": _num(d.net),
        "txCount": d.tx_count,
        "avgDailyExpense": _num(d.avg_daily_expense),
        "projectedExpense": _num(d.projected_expense) if d.projected_expense is not None else None,
        "budgetLimit": _num(d.budget_limit) if d.budget_limit is not None else None,
        "budgetPct": round(float(d.budget_pct), 1) if d.budget_pct is not None else None,
        "daily": [{"date": day.isoformat(), "income": _num(i), "expense": _num(e)} for day, i, e in d.daily],
        "expenseCategories": [{"name": n, "amount": _num(a)} for n, a in d.expense_categories],
        "incomeSources": [{"name": n, "amount": _num(a)} for n, a in d.income_sources],
        "topDay": {"date": d.top_day[0].isoformat(), "amount": _num(d.top_day[1])} if d.top_day else None,
        "otherCurrencies": {c: {k: _num(v) for k, v in vals.items()} for c, vals in d.other_currencies.items()},
        "today": today_local().isoformat(),
    }


def tx_to_json(tx: FinancialTransaction) -> Dict[str, Any]:
    return {
        "id": tx.id,
        "date": tx.transaction_date.isoformat(),
        "type": tx.type,
        "category": tx.category_name or "Boshqa",
        "amount": _num(tx.amount),
        "currency": tx.currency,
        "description": tx.description or "",
        "status": tx.status,
        "source": tx.source,
    }


def register_finance_routes(app: web.Application, owner_id: int, bot_token: str) -> None:
    """Attach /api/fin/* routes to the aiohttp app."""
    service = FinanceService()

    def authorize(request: web.Request) -> Optional[web.Response]:
        header = request.headers.get("Authorization", "")
        init_data = header[4:] if header.startswith("tma ") else ""
        user = verify_init_data(init_data, bot_token)
        if user is None:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        if int(user["id"]) != int(owner_id):
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)
        return None

    def parse_month(value: Optional[str]) -> Optional[tuple[int, int]]:
        if not value:
            return None
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m")
        except ValueError:
            return None
        return parsed.year, parsed.month

    async def handle_dashboard(request: web.Request) -> web.Response:
        if (denied := authorize(request)) is not None:
            return denied
        kind = request.query.get("kind", "month")
        if kind not in ("day", "week", "month"):
            return web.json_response({"ok": False, "error": "bad kind"}, status=400)
        ym = parse_month(request.query.get("month"))
        data = await collect_dashboard_data(
            owner_id, kind, year=ym[0] if ym else None, month=ym[1] if ym else None
        )
        return web.json_response({"ok": True, "dashboard": dashboard_to_json(data)})

    async def handle_transactions(request: web.Request) -> web.Response:
        if (denied := authorize(request)) is not None:
            return denied
        ym = parse_month(request.query.get("month")) or (today_local().year, today_local().month)
        start = datetime.date(ym[0], ym[1], 1)
        end = datetime.date(ym[0] + 1, 1, 1) if ym[1] == 12 else datetime.date(ym[0], ym[1] + 1, 1)
        async with get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(FinancialTransaction)
                        .where(
                            FinancialTransaction.owner_id == owner_id,
                            FinancialTransaction.transaction_date >= start,
                            FinancialTransaction.transaction_date < end,
                            FinancialTransaction.status != "CANCELLED",
                        )
                        .order_by(FinancialTransaction.transaction_date.desc(), FinancialTransaction.id.desc())
                        .limit(300)
                    )
                ).scalars().all()
            )
        pending_total = len(await service.get_pending_transactions(owner_id))
        return web.json_response(
            {"ok": True, "transactions": [tx_to_json(t) for t in rows], "pendingCount": pending_total}
        )

    async def _owned_tx(request: web.Request) -> Optional[FinancialTransaction]:
        try:
            tx_id = int(request.match_info["tx_id"])
        except ValueError:
            return None
        return await service.get_transaction(tx_id, owner_id)

    async def handle_confirm(request: web.Request) -> web.Response:
        if (denied := authorize(request)) is not None:
            return denied
        tx = await _owned_tx(request)
        if tx is None:
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        done = await service.confirm_transaction(tx.id, actor_id=owner_id)
        if done is None:
            return web.json_response({"ok": False, "error": "cancelled"}, status=409)
        return web.json_response({"ok": True, "transaction": tx_to_json(done)})

    async def handle_cancel(request: web.Request) -> web.Response:
        if (denied := authorize(request)) is not None:
            return denied
        tx = await _owned_tx(request)
        if tx is None:
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        done = await service.cancel_transaction(tx.id, actor_id=owner_id)
        return web.json_response({"ok": True, "transaction": tx_to_json(done)})

    async def handle_edit(request: web.Request) -> web.Response:
        if (denied := authorize(request)) is not None:
            return denied
        tx = await _owned_tx(request)
        if tx is None:
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        amount_text = str(body.get("amount", "")).strip()
        found = find_amount(amount_text) if amount_text else None
        if found is None or found[0] <= Decimal("0"):
            return web.json_response({"ok": False, "error": "bad amount"}, status=400)
        done = await service.edit_transaction(tx.id, actor_id=owner_id, new_amount=found[0])
        if done is None:
            return web.json_response({"ok": False, "error": "cancelled"}, status=409)
        return web.json_response({"ok": True, "transaction": tx_to_json(done)})

    app.router.add_get("/api/fin/dashboard", handle_dashboard)
    app.router.add_get("/api/fin/transactions", handle_transactions)
    app.router.add_post("/api/fin/transactions/{tx_id}/confirm", handle_confirm)
    app.router.add_post("/api/fin/transactions/{tx_id}/cancel", handle_cancel)
    app.router.add_post("/api/fin/transactions/{tx_id}/edit", handle_edit)
