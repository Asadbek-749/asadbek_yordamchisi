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
