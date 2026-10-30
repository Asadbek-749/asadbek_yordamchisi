"""Short, rate-limited error alerts to the bot owner (at most one Telegram message per hour)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

ALERT_TEXTS: Dict[str, str] = {
    "ai_quota": "⚠️ Gemini kvotasi tugadi (429). AI javoblari vaqtincha ishlamaydi, bot qoidalar bilan ishlashda davom etadi.",
    "ai_auth": "🔑 Gemini kaliti qabul qilinmadi. Railway'da GEMINI_API_KEY ni tekshiring.",
    "sheets": "📗 Google Sheets bilan sinxronlashda xato: {detail}. Jadval ulashilganini va kalitni tekshiring (/sheet).",
    "scheduler": "⏰ Eslatmalar rejalashtiruvchisida xato yuz berdi: {detail}. Loglarni tekshiring.",
    "error": "❗ Botda kutilmagan xato: {detail}. Batafsil Railway loglarida.",
}


class OwnerAlerter:
    """Sends a brief alert to the owner, but never more than once per `min_interval` seconds.

    Alerts suppressed inside the window are counted and mentioned in the next message.
    Only the error *class name* is ever sent, never messages that could contain keys or user data.
    """

    def __init__(
        self,
        bot: Any,
        owner_id: int,
        min_interval: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bot = bot
        self.owner_id = owner_id
        self.min_interval = min_interval
        self.clock = clock
        self._last_sent: float | None = None
        self._suppressed = 0

    async def alert(self, kind: str, detail: str = "") -> bool:
        """Returns True when a message was actually sent."""
        now = self.clock()
        if self._last_sent is not None and now - self._last_sent < self.min_interval:
            self._suppressed += 1
            return False

        text = ALERT_TEXTS.get(kind, ALERT_TEXTS["error"]).format(detail=(detail or "noma'lum")[:80])
        if self._suppressed:
            text += f"\n(Oxirgi soatda yana {self._suppressed} ta shunday xabar bostirildi.)"
        self._last_sent = now
        self._suppressed = 0
        try:
            await self.bot.send_message(self.owner_id, text)
            return True
        except Exception as exc:  # never let alerting itself break the bot
            logger.warning("Could not deliver owner alert: %s", type(exc).__name__)
            return False

    def alert_nowait(self, kind: str, detail: str = "") -> None:
        """Fire-and-forget variant for sync code paths; a no-op without a running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.alert(kind, detail))
