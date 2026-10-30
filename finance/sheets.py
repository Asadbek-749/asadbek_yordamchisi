"""Keeps a Google Sheet in step with the confirmed transactions in the database.

The database stays the source of truth. After every confirm / edit / cancel the affected month
is marked dirty; a background loop then rewrites that month's worksheet completely, so edits and
cancellations are always reflected and a failed sync is simply retried (no duplicated rows).

Worksheets:
  "YYYY-MM"  one row per confirmed transaction of that month
  "Kunlik"   daily totals (all time)
  "Oylik"    monthly totals (all time)
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import urllib.request
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple

from sqlalchemy import select

from discipline.db import get_session
from finance.models import FinancialTransaction

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

TX_HEADER = ["ID", "Sana", "Turi", "Kategoriya", "Summa", "Valyuta", "Izoh", "Manba", "Holat"]
DAILY_HEADER = ["Sana", "Daromad (so'm)", "Xarajat (so'm)", "Sof balans (so'm)", "Daromad (USD)", "Xarajat (USD)"]
MONTHLY_HEADER = [
    "Oy", "Daromad (so'm)", "Xarajat (so'm)", "Sof balans (so'm)", "Daromad (USD)", "Xarajat (USD)", "Yozuvlar soni",
]
TYPE_LABEL = {"INCOME": "Daromad", "EXPENSE": "Xarajat"}
STATUS_LABEL = {"CONFIRMED": "Tasdiqlangan", "EDITED": "Tahrirlangan"}


class SheetsBackend(Protocol):
    def write_worksheet(self, title: str, rows: List[List[Any]]) -> None: ...

    def spreadsheet_url(self) -> str: ...


def parse_service_account(raw: str) -> Dict[str, Any]:
    """Accept the service-account key as raw JSON or base64 of that JSON (easier to paste into Railway)."""
    raw = raw.strip()
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    info = json.loads(raw)
    if "client_email" not in info or "private_key" not in info:
        raise ValueError("Service account JSON must contain client_email and private_key")
    return info


class GspreadBackend:
    """Blocking gspread client; call it through asyncio.to_thread."""

    def __init__(self, credentials_info: Dict[str, Any], spreadsheet_id: str) -> None:
        self._info = credentials_info
        self._id = spreadsheet_id
        self._spreadsheet = None

    def _open(self):
        if self._spreadsheet is None:
            import gspread

            client = gspread.service_account_from_dict(self._info, scopes=SCOPES)
            self._spreadsheet = client.open_by_key(self._id)
        return self._spreadsheet

    def write_worksheet(self, title: str, rows: List[List[Any]]) -> None:
        import gspread

        sp = self._open()
        try:
            ws = sp.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sp.add_worksheet(title=title, rows=max(len(rows) + 50, 100), cols=max(len(rows[0]), 10))
        ws.clear()
        if ws.row_count < len(rows) + 1:
            ws.resize(rows=len(rows) + 50)
        ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
        ws.freeze(rows=1)
        ws.format("A1:Z1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.87, "green": 0.92, "blue": 0.97}})

    def spreadsheet_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self._id}"


class AppsScriptError(RuntimeError):
    """The Apps Script web app rejected the request (wrong secret, script error, bad response)."""


class AppsScriptBackend:
    """Writes worksheets through a Google Apps Script web app bound to the spreadsheet.

    No Google Cloud project or service-account key is needed: the script runs as the sheet's
    owner and only accepts requests that carry the shared secret.
    """

    def __init__(self, url: str, secret: str, timeout: float = 60.0) -> None:
        self._url = url.strip()
        self._secret = secret
        self._timeout = timeout
        self._sheet_url = ""

    def write_worksheet(self, title: str, rows: List[List[Any]]) -> None:
        body = json.dumps({"secret": self._secret, "sheets": [{"title": title, "rows": rows}]}).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        # Apps Script answers a POST with a 302 to the result; urllib follows it with a GET, as required.
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AppsScriptError("Apps Script javobi JSON emas (skript e'lon qilinganini tekshiring)") from None
        if not payload.get("ok"):
            raise AppsScriptError(str(payload.get("error", "unknown error"))[:100])
        self._sheet_url = payload.get("url") or self._sheet_url

    def spreadsheet_url(self) -> str:
        return self._sheet_url or "(birinchi sinxronlashdan keyin havola paydo bo'ladi)"


def _num(v: Decimal) -> float | int:
    return int(v) if v == v.to_integral_value() else float(v)


def month_key(d: datetime.date) -> Tuple[int, int]:
    return d.year, d.month


class SheetsSync:
    def __init__(self, backend: SheetsBackend, owner_id: int) -> None:
        self.backend = backend
        self.owner_id = owner_id
        self._dirty: Set[Tuple[int, int]] = set()
        self._lock = asyncio.Lock()

    # ── change tracking ──
    def mark_dirty(self, *dates: datetime.date) -> None:
        for d in dates:
            self._dirty.add(month_key(d))

    @property
    def pending_months(self) -> List[Tuple[int, int]]:
        return sorted(self._dirty)

    # ── data ──
    async def _load_all(self) -> List[FinancialTransaction]:
        async with get_session() as session:
            q = (
                select(FinancialTransaction)
                .where(
                    FinancialTransaction.owner_id == self.owner_id,
                    FinancialTransaction.status.in_(["CONFIRMED", "EDITED"]),
                )
                .order_by(FinancialTransaction.transaction_date, FinancialTransaction.id)
            )
            return list((await session.execute(q)).scalars().all())

    @staticmethod
    def month_rows(txs: List[FinancialTransaction], year: int, month: int) -> List[List[Any]]:
        rows: List[List[Any]] = [TX_HEADER]
        for tx in txs:
            if month_key(tx.transaction_date) != (year, month):
                continue
            rows.append([
                tx.id, tx.transaction_date.isoformat(), TYPE_LABEL.get(tx.type, tx.type), tx.category_name or "Boshqa",
                _num(tx.amount), tx.currency, tx.description or "", tx.source, STATUS_LABEL.get(tx.status, tx.status),
            ])
        return rows

    @staticmethod
    def totals_rows(txs: List[FinancialTransaction]) -> Tuple[List[List[Any]], List[List[Any]]]:
        """(daily rows, monthly rows) with UZS and USD kept in separate columns."""
        zero = Decimal("0")
        daily: Dict[datetime.date, Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: zero))
        monthly: Dict[Tuple[int, int], Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: zero))
        for tx in txs:
            cur = (tx.currency or "UZS").upper()
            if cur not in ("UZS", "USD") or tx.type not in ("INCOME", "EXPENSE"):
                continue
            col = f"{'inc' if tx.type == 'INCOME' else 'exp'}_{cur}"
            daily[tx.transaction_date][col] += tx.amount
            m = monthly[month_key(tx.transaction_date)]
            m[col] += tx.amount
            m["count"] += 1

        d_rows: List[List[Any]] = [DAILY_HEADER]
        for day in sorted(daily):
            v = daily[day]
            d_rows.append([day.isoformat(), _num(v["inc_UZS"]), _num(v["exp_UZS"]), _num(v["inc_UZS"] - v["exp_UZS"]),
                           _num(v["inc_USD"]), _num(v["exp_USD"])])
        m_rows: List[List[Any]] = [MONTHLY_HEADER]
        for (y, mo) in sorted(monthly):
            v = monthly[(y, mo)]
            m_rows.append([f"{y}-{mo:02d}", _num(v["inc_UZS"]), _num(v["exp_UZS"]), _num(v["inc_UZS"] - v["exp_UZS"]),
                           _num(v["inc_USD"]), _num(v["exp_USD"]), int(v["count"])])
        return d_rows, m_rows

    # ── syncing ──
    async def sync_months(self, months: List[Tuple[int, int]]) -> int:
        """Rewrite the given months plus the summary sheets. Returns number of worksheets written."""
        async with self._lock:
            txs = await self._load_all()
            written = 0
            for year, month in months:
                await asyncio.to_thread(self.backend.write_worksheet, f"{year}-{month:02d}", self.month_rows(txs, year, month))
                written += 1
            d_rows, m_rows = self.totals_rows(txs)
            await asyncio.to_thread(self.backend.write_worksheet, "Kunlik", d_rows)
            await asyncio.to_thread(self.backend.write_worksheet, "Oylik", m_rows)
            return written + 2

    async def sync_dirty(self) -> Optional[int]:
        """Sync months marked dirty. On failure they stay dirty and the exception propagates."""
        months = self.pending_months
        if not months:
            return None
        self._dirty.difference_update(months)
        try:
            return await self.sync_months(months)
        except Exception:
            self._dirty.update(months)
            raise

    async def sync_everything(self) -> int:
        txs = await self._load_all()
        months = sorted({month_key(t.transaction_date) for t in txs}) or [month_key(datetime.date.today())]
        return await self.sync_months(months)

    async def run_forever(self, alerter: Any = None, interval: float = 20.0) -> None:
        """Background loop: push dirty months every `interval` seconds."""
        while True:
            try:
                await self.sync_dirty()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Google Sheets sync failed: %s", type(exc).__name__)
                if alerter is not None:
                    alerter.alert_nowait("sheets", type(exc).__name__)
            await asyncio.sleep(interval)


def build_sync_from_env(
    raw_credentials: Optional[str],
    spreadsheet_id: Optional[str],
    owner_id: int,
    apps_script_url: Optional[str] = None,
    apps_script_secret: Optional[str] = None,
) -> Optional[SheetsSync]:
    """Create a SheetsSync, or None when Google Sheets is not configured / settings are unusable.

    The Apps Script route (URL + secret, no key file) is preferred when both values are present.
    """
    if apps_script_url and apps_script_secret:
        url = apps_script_url.strip()
        if not url.startswith("https://script.google.com/"):
            logger.warning("GOOGLE_APPS_SCRIPT_URL must start with https://script.google.com/ ; Sheets sync disabled.")
            return None
        logger.info("Google Sheets sync enabled (Apps Script web app).")
        return SheetsSync(AppsScriptBackend(url, apps_script_secret.strip()), owner_id)

    if not raw_credentials or not spreadsheet_id:
        return None
    try:
        info = parse_service_account(raw_credentials)
    except Exception as exc:
        logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON is not usable (%s); Google Sheets sync disabled.", type(exc).__name__)
        return None
    logger.info("Google Sheets sync enabled (service account %s).", info.get("client_email"))
    return SheetsSync(GspreadBackend(info, spreadsheet_id.strip()), owner_id)
