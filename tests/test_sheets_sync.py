"""Google Sheets mirror: row building, dirty-month tracking, retries (no network)."""
from __future__ import annotations

import base64
import datetime
import json
from decimal import Decimal

import pytest
import pytest_asyncio

from discipline.db import close_db, init_db
from finance import service as service_module
from finance.extractor import ExtractedTransactionItem as Item
from finance.service import FinanceService
from finance.sheets import SheetsSync, build_sync_from_env, parse_service_account

D = datetime.date(2026, 10, 15)


class FakeBackend:
    def __init__(self) -> None:
        self.sheets: dict[str, list] = {}
        self.writes: list[str] = []
        self.fail = False

    def write_worksheet(self, title, rows):
        if self.fail:
            raise ConnectionError("boom")
        self.sheets[title] = rows
        self.writes.append(title)

    def spreadsheet_url(self):
        return "https://docs.google.com/spreadsheets/d/FAKE"


@pytest_asyncio.fixture
async def env(tmp_path):
    await init_db(data_dir=tmp_path)
    backend = FakeBackend()
    sync = SheetsSync(backend, owner_id=7)
    service_module.change_hook = sync.mark_dirty
    yield FinanceService(), sync, backend
    service_module.change_hook = None
    await close_db()


async def _confirmed(svc, *items):
    created = await svc.create_pending_transactions(7, list(items))
    return [await svc.confirm_transaction(t.id, 7) for t in created]


@pytest.mark.asyncio
async def test_confirm_marks_month_dirty_and_sync_writes_rows(env):
    svc, sync, backend = env
    created = await svc.create_pending_transactions(7, [Item("EXPENSE", Decimal("45000"), "FOOD", "Tushlik", "UZS", D)])
    assert sync.pending_months == []  # pending records are not mirrored
    await svc.confirm_transaction(created[0].id, 7)
    assert sync.pending_months == [(2026, 10)]

    assert await sync.sync_dirty() == 3
    assert sync.pending_months == []
    assert set(backend.writes) == {"2026-10", "Kunlik", "Oylik"}

    rows = backend.sheets["2026-10"]
    assert rows[0][:5] == ["ID", "Sana", "Turi", "Kategoriya", "Summa"]
    assert rows[1][1:3] == ["2026-10-15", "Xarajat"] and rows[1][4] == 45000
    assert await sync.sync_dirty() is None  # nothing left to do


@pytest.mark.asyncio
async def test_edit_and_cancel_are_reflected_without_duplicates(env):
    svc, sync, backend = env
    a, b = await _confirmed(
        svc,
        Item("EXPENSE", Decimal("10000"), "FOOD", "A", "UZS", D),
        Item("EXPENSE", Decimal("20000"), "TRANSPORT", "B", "UZS", D),
    )
    await sync.sync_dirty()
    assert len(backend.sheets["2026-10"]) == 3

    await svc.edit_transaction(a.id, 7, new_amount=Decimal("15000"))
    await svc.cancel_transaction(b.id, 7)
    await sync.sync_dirty()
    rows = backend.sheets["2026-10"]
    assert len(rows) == 2 and rows[1][4] == 15000 and rows[1][8] == "Tahrirlangan"


@pytest.mark.asyncio
async def test_failed_sync_keeps_month_dirty_and_retries(env):
    svc, sync, backend = env
    await _confirmed(svc, Item("INCOME", Decimal("3000000"), "MAIN_JOB", "Oylik", "UZS", D))
    backend.fail = True
    with pytest.raises(ConnectionError):
        await sync.sync_dirty()
    assert sync.pending_months == [(2026, 10)]

    backend.fail = False
    assert await sync.sync_dirty() == 3
    assert sync.pending_months == []


@pytest.mark.asyncio
async def test_daily_and_monthly_totals_keep_currencies_apart(env):
    svc, sync, backend = env
    await _confirmed(
        svc,
        Item("INCOME", Decimal("3000000"), "MAIN_JOB", "Oylik", "UZS", D),
        Item("EXPENSE", Decimal("45000"), "FOOD", "Tushlik", "UZS", D),
        Item("EXPENSE", Decimal("100"), "SHOPPING", "Kitob", "USD", D),
        Item("EXPENSE", Decimal("9000"), "FOOD", "Sentyabr", "UZS", datetime.date(2026, 9, 30)),
    )
    await sync.sync_everything()
    assert {"2026-09", "2026-10", "Kunlik", "Oylik"} <= set(backend.sheets)

    daily = {r[0]: r for r in backend.sheets["Kunlik"][1:]}
    assert daily["2026-10-15"][1:] == [3000000, 45000, 2955000, 0, 100]
    monthly = {r[0]: r for r in backend.sheets["Oylik"][1:]}
    assert monthly["2026-10"][1:] == [3000000, 45000, 2955000, 0, 100, 3]
    assert monthly["2026-09"][2] == 9000


def test_service_account_parsing_and_env_building():
    info = {"client_email": "bot@proj.iam.gserviceaccount.com", "private_key": "-----BEGIN-----"}
    raw = json.dumps(info)
    assert parse_service_account(raw) == info
    assert parse_service_account(base64.b64encode(raw.encode()).decode()) == info
    with pytest.raises(ValueError):
        parse_service_account('{"client_email": "x"}')

    assert build_sync_from_env(None, "sheet", 1) is None
    assert build_sync_from_env(raw, None, 1) is None
    assert build_sync_from_env("not json", "sheet", 1) is None
    assert build_sync_from_env(raw, " sheet-id ", 1).backend.spreadsheet_url().endswith("/sheet-id")


# ── Apps Script backend (no Google Cloud key) ──────────────────────────────

class _FakeAppsScript:
    """Local server imitating Apps Script: POST -> 302 -> GET result."""

    def __init__(self, secret: str = "s3cret") -> None:
        import http.server
        import threading

        outer = self
        self.secret = secret
        self.received: list = []
        self._result = b""

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("secret") != outer.secret:
                    outer._result = json.dumps({"ok": False, "error": "forbidden"}).encode()
                else:
                    outer.received.append(body["sheets"])
                    outer._result = json.dumps({"ok": True, "url": "https://docs.google.com/spreadsheets/d/XYZ"}).encode()
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/result")
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer._result)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/exec"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def test_apps_script_backend_posts_rows_and_follows_redirect():
    from finance.sheets import AppsScriptBackend

    fake = _FakeAppsScript()
    try:
        backend = AppsScriptBackend(fake.url, "s3cret", timeout=10)
        assert "paydo bo'ladi" in backend.spreadsheet_url()
        backend.write_worksheet("2026-10", [["ID", "Sana"], [1, "2026-10-15"]])
        assert fake.received == [[{"title": "2026-10", "rows": [["ID", "Sana"], [1, "2026-10-15"]]}]]
        assert backend.spreadsheet_url() == "https://docs.google.com/spreadsheets/d/XYZ"
    finally:
        fake.close()


def test_apps_script_backend_rejects_wrong_secret_and_non_json():
    from finance.sheets import AppsScriptBackend, AppsScriptError

    fake = _FakeAppsScript()
    try:
        with pytest.raises(AppsScriptError, match="forbidden"):
            AppsScriptBackend(fake.url, "wrong", timeout=10).write_worksheet("x", [["a"]])
    finally:
        fake.close()


@pytest.mark.asyncio
async def test_sheets_sync_works_through_apps_script(env):
    from finance.sheets import AppsScriptBackend

    svc, _, _ = env
    fake = _FakeAppsScript()
    try:
        sync = SheetsSync(AppsScriptBackend(fake.url, "s3cret", timeout=10), owner_id=7)
        await _confirmed(svc, Item("EXPENSE", Decimal("45000"), "FOOD", "Tushlik", "UZS", D))
        assert await sync.sync_everything() == 3
        titles = [batch[0]["title"] for batch in fake.received]
        assert titles == ["2026-10", "Kunlik", "Oylik"]
    finally:
        fake.close()


def test_apps_script_env_settings():
    url = "https://script.google.com/macros/s/AKfy123/exec"
    sync = build_sync_from_env(None, None, 1, apps_script_url=url, apps_script_secret=" abc ")
    assert sync is not None and sync.backend._secret == "abc"
    assert build_sync_from_env(None, None, 1, apps_script_url="http://evil.example/exec", apps_script_secret="x") is None
    assert build_sync_from_env(None, None, 1, apps_script_url=url, apps_script_secret=None) is None
