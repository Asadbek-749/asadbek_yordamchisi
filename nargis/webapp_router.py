"""Router for Telegram Mini App (Web App) commands and incoming submissions."""
from __future__ import annotations

import json
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from webapp.server import load_applications, save_application

logger = logging.getLogger(__name__)


def build_webapp_router(owner_id: Optional[int], webapp_url: Optional[str] = None) -> Router:
    """Build router for Telegram Mini App."""
    router = Router(name="webapp_router")

    # ──────────────────────────────────────────────────────────────────────
    # 1. /webapp, /app, /qabul, /ariza — Mini App link and opening button
    # ──────────────────────────────────────────────────────────────────────
    @router.message(Command("webapp", "app", "qabul", "ariza"))
    @router.message(F.text.in_({"🚀 Qabul Mini App", "📱 Mini App", "🎓 Qabul 2026"}))
    async def cmd_open_webapp(message: Message) -> None:
        url = webapp_url
        if url:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚀 Qabul Mini App-ni ochish",
                            web_app=WebAppInfo(url=url),
                        )
                    ]
                ]
            )
            await message.answer(
                "✨ <b>O'rganAilik & Nargis AI — Qabul 2026 Mini App</b> ✨\n\n"
                "Quyidagi tugma orqali rasmiy Mini App-ga kiring:\n"
                "• 📝 <b>Ariza topshirish</b> — qabul anketasini to'ldirish\n"
                "• 📊 <b>Arizam holati</b> — qabul bosqichini real-vaqtda kuzatish\n"
                "• 💡 <b>Kurslar katalogi</b> — AI va amaliy dasturlash dasturlari\n\n"
                "<i>Tugmani bosing:</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            await message.answer(
                "✨ <b>O'rganAilik & Nargis AI — Qabul Mini App</b> ✨\n\n"
                "Mini App fayllari tayyorlandi va tizimga integratsiya qilindi.\n\n"
                "💡 <i>Mini App to'liq Telegram ichida ochilishi uchun:</i>\n"
                "<code>.env</code> faylida <b>WEBAPP_URL</b> parametrini ko'rsating "
                "(masalan: <code>WEBAPP_URL=https://your-domain.up.railway.app</code> yoki ngrok/Cloudflare havolasi).\n\n"
                "Shunda bot pastki menyusida <b>[Qabul Mini App]</b> tugmasi paydo bo'ladi!",
                parse_mode="HTML",
            )

    # ──────────────────────────────────────────────────────────────────────
    # 2. Receiving data from Telegram WebApp (tg.sendData)
    # ──────────────────────────────────────────────────────────────────────
    @router.message(F.web_app_data)
    async def handle_web_app_data(message: Message) -> None:
        raw_data = message.web_app_data.data if message.web_app_data else "{}"
        try:
            payload = json.loads(raw_data)
        except Exception:
            payload = {"raw": raw_data}

        app_id = payload.get("appId") or f"APP-{hash(raw_data) % 100000:05d}"
        full_name = payload.get("fullName") or message.from_user.full_name or "Nomzod"
        phone = payload.get("phone", "-")
        course_title = payload.get("courseTitle", payload.get("courseKey", "AI Yo'nalishi"))
        level = payload.get("level", "Boshlang'ich")
        goal = payload.get("goal", "-")

        record = {
            "appId": app_id,
            "fullName": full_name,
            "phone": phone,
            "courseTitle": course_title,
            "level": level,
            "goal": goal,
            "userId": message.from_user.id,
            "username": message.from_user.username,
            "status": "new",
        }
        save_application(record)

        # 1. Warm confirmation to student
        confirm_text = (
            f"🎉 <b>Arizangiz muvaffaqiyatli qabul qilindi, {full_name}!</b> ✨💫\n\n"
            f"Sizning anketangiz <b>O'rganAilik</b> qabul komissiyasi va <b>Nargis AI</b> "
            f"tizimiga rasman kiritildi.\n\n"
            f"🆔 <b>Ariza kodi:</b> <code>{app_id}</code>\n"
            f"🎯 <b>Tanlangan yo'nalish:</b> {course_title}\n"
            f"📞 <b>Aloqa raqami:</b> {phone}\n"
            f"📊 <b>Tayyorgarlik darajasi:</b> {level}\n\n"
            f"Tez orada ustozimiz Asadbek va qabul mentorlari siz bilan bog'lanishadi! "
            f"Orzularingiz sari yangi bosqich muborak bo'lsin! 🚀"
        )
        await message.answer(confirm_text, parse_mode="HTML")

        # 2. Instant notification to Asadbek Ustoz
        if owner_id and message.from_user.id != owner_id:
            uname = f"@{message.from_user.username}" if message.from_user.username else f"ID: <code>{message.from_user.id}</code>"
            owner_alert = (
                f"🎓 <b>YANGI ARIZA KELDI! (Mini App orqali)</b> ⚡️\n\n"
                f"🆔 <b>Ariza kodi:</b> <code>{app_id}</code>\n"
                f"👤 <b>Nomzod:</b> {full_name}\n"
                f"📞 <b>Telefon:</b> {phone}\n"
                f"🎯 <b>Kurs:</b> {course_title}\n"
                f"📊 <b>Daraja:</b> {level}\n"
                f"💡 <b>Ijodiy maqsad:</b> {goal}\n"
                f"💬 <b>Telegram:</b> {uname}\n\n"
                f"<i>/arizalar buyrug'i orqali barcha arizalarni ko'rishingiz mumkin.</i>"
            )
            try:
                await message.bot.send_message(chat_id=owner_id, text=owner_alert, parse_mode="HTML")
            except Exception as notify_err:
                logger.warning("Could not notify owner about webapp submission: %s", notify_err)

    # ──────────────────────────────────────────────────────────────────────
    # 3. /arizalar — Owner command to view incoming applications
    # ──────────────────────────────────────────────────────────────────────
    if owner_id:
        is_owner = F.from_user.id == owner_id

        @router.message(is_owner, Command("arizalar"))
        @router.message(is_owner, F.text == "🎓 Arizalar")
        async def cmd_list_applications(message: Message) -> None:
            apps = load_applications()
            if not apps:
                await message.answer("🎓 Hozircha Mini App orqali tushgan arizalar yo'q.")
                return

            lines = [f"🎓 <b>Mini App orqali kelgan arizalar ({len(apps)} ta):</b>\n"]
            for i, app in enumerate(apps[:15], 1):
                app_id = app.get("appId", "APP")
                name = app.get("fullName", "Noma'lum")
                phone = app.get("phone", "-")
                course = app.get("courseTitle", "-")
                lines.append(
                    f"{i}. <b>{name}</b> (<code>{app_id}</code>)\n"
                    f"   🎯 {course} | 📞 {phone}\n"
                )

            await message.answer("\n".join(lines), parse_mode="HTML")

    return router
