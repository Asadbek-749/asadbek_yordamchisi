"""Owner-only finance Mini App API: Telegram initData auth, ownership checks, static hardening."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from discipline.db import close_db, init_db
from finance.extractor import ExtractedTransactionItem as Item
from finance.service import FinanceService
from finance.timeutil import today_local
from webapp.fin_api import verify_init_data
from webapp.server import create_webapp

TOKEN = "123456:TEST"
OWNER = 5146977644


def make_init_data(token: str, user_id: int, auth_date: int | None = None, tamper: bool = False) -> str:
    """Build initData exactly like Telegram does (HMAC-SHA256 with key HMAC('WebAppData', bot_token))."""
    pairs = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":")),
        "query_id": "AAH123",
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        pairs["user"] = json.dumps({"id": OWNER + 1}, separators=(",", ":"))
    return urlencode(pairs)


def auth(user_id: int = OWNER, **kw) -> dict:
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id, **kw)}


def test_verify_init_data():
    assert verify_init_data(make_init_data(TOKEN, 5), TOKEN)["id"] == 5
    assert verify_init_data(make_init_data("999:OTHER", 5), TOKEN) is None  # wrong bot
    assert verify_init_data(make_init_data(TOKEN, 5, tamper=True), TOKEN) is None  # edited user id
    assert verify_init_data(make_init_data(TOKEN, 5, auth_date=int(time.time()) - 90000), TOKEN) is None  # stale
    assert verify_init_data("", TOKEN) is None and verify_init_data("garbage", TOKEN) is None
    assert verify_init_data("user=%7B%7D&auth_date=1", TOKEN) is None  # no hash


@pytest_asyncio.fixture
async def client(tmp_path):
    await init_db(data_dir=tmp_path)
    svc = FinanceService()
    today = today_local()
    created = await svc.create_pending_transactions(OWNER, [
        Item("EXPENSE", Decimal("45000"), "FOOD", "Tushlik", "UZS", today),
        Item("EXPENSE", Decimal("15000"), "TRANSPORT", "Taksi", "UZS", today),
        Item("INCOME", Decimal("3000000"), "MAIN_JOB", "Oylik", "UZS", today),
    ])
    await svc.confirm_transaction(created[0].id, OWNER)
    await svc.confirm_transaction(created[2].id, OWNER)
    other = await svc.create_pending_transactions(
        OWNER + 1, [Item("EXPENSE", Decimal("777"), "FOOD", "Boshqa odam", "UZS", today)]
    )
    app = create_webapp(owner_id=OWNER, data_dir=tmp_path, bot_token=TOKEN)
    c = TestClient(TestServer(app))
    await c.start_server()
    c.ids = {"pending": created[1].id, "confirmed": created[0].id, "foreign": other[0].id}
    yield c
    await c.close()
    await close_db()


@pytest.mark.asyncio
async def test_every_finance_route_requires_the_owner(client):
    pid = client.ids["pending"]
    routes = [
        ("get", "/api/fin/dashboard"),
        ("get", "/api/fin/transactions"),
        ("post", f"/api/fin/transactions/{pid}/confirm"),
        ("post", f"/api/fin/transactions/{pid}/cancel"),
        ("post", f"/api/fin/transactions/{pid}/edit"),
    ]
    for method, path in routes:
        call = getattr(client, method)
        assert (await call(path)).status == 401, path  # no auth
        assert (await call(path, headers=auth(OWNER + 99))).status == 403, path  # signed, not owner
        forged = {"Authorization": "tma " + make_init_data(TOKEN, OWNER, tamper=True)}
        assert (await call(path, headers=forged)).status == 401, path  # forged


@pytest.mark.asyncio
async def test_dashboard_and_transactions_json(client):
    resp = await client.get("/api/fin/dashboard?kind=month", headers=auth())
    d = (await resp.json())["dashboard"]
    assert d["income"] == 3000000 and d["expense"] == 45000 and d["net"] == 2955000
    assert d["expenseCategories"][0]["amount"] == 45000 and len(d["daily"]) >= 28

    assert (await client.get("/api/fin/dashboard?kind=bogus", headers=auth())).status == 400

    data = await (await client.get("/api/fin/transactions", headers=auth())).json()
    descs = {t["description"] for t in data["transactions"]}
    assert descs == {"Tushlik", "Taksi", "Oylik"}  # never another owner's record
    assert data["pendingCount"] == 1


@pytest.mark.asyncio
async def test_confirm_edit_cancel_and_ownership(client):
    pid, foreign = client.ids["pending"], client.ids["foreign"]

    assert (await client.post(f"/api/fin/transactions/{foreign}/confirm", headers=auth())).status == 404
    assert (await client.post("/api/fin/transactions/abc/confirm", headers=auth())).status == 404

    resp = await client.post(f"/api/fin/transactions/{pid}/edit", headers=auth(), json={"amount": "20 ming"})
    assert (await resp.json())["transaction"]["amount"] == 20000
    bad = await client.post(f"/api/fin/transactions/{pid}/edit", headers=auth(), json={"amount": "nothing"})
    assert bad.status == 400

    resp = await client.post(f"/api/fin/transactions/{pid}/confirm", headers=auth())
    assert (await resp.json())["transaction"]["status"] == "CONFIRMED"

    resp = await client.post(f"/api/fin/transactions/{pid}/cancel", headers=auth())
    assert (await resp.json())["transaction"]["status"] == "CANCELLED"
    assert (await client.post(f"/api/fin/transactions/{pid}/confirm", headers=auth())).status == 409  # no revival


@pytest.mark.asyncio
async def test_static_files_are_limited(client):
    assert (await client.get("/fin/")).status == 200
    assert "Nargis Moliya" in await (await client.get("/fin/")).text()
    assert (await client.get("/fin/fin.js")).status == 200
    assert (await client.get("/fin/fin.css")).status == 200
    assert (await client.get("/")).status == 200 and (await client.get("/styles.css")).status == 200
    assert (await client.get("/app.js")).status == 200 and (await client.get("/assets/emblem.svg")).status == 200
    # source code and directory listings are no longer served
    for path in ("/server.py", "/fin_api.py", "/fin/../server.py", "/assets/", "/__pycache__/"):
        assert (await client.get(path)).status in (403, 404), path


def test_finance_app_url_and_keyboard():
    from finance.keyboards import dashboard_keyboard, finance_app_url

    assert finance_app_url("https://x.up.railway.app/") == "https://x.up.railway.app/fin/"
    assert finance_app_url(None) is None
    rows = dashboard_keyboard("https://x.up.railway.app").inline_keyboard
    assert rows[0][0].web_app.url == "https://x.up.railway.app/fin/"
    assert dashboard_keyboard().inline_keyboard[0][0].callback_data == "fin:dash:day"
