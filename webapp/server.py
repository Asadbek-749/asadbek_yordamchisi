"""Aiohttp web server for Nargis AI / O'rganAilik Telegram Mini App."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web
from aiogram import Bot

from webapp.fin_api import register_finance_routes, verify_init_data

logger = logging.getLogger("webapp.server")

WEBAPP_DIR = Path(__file__).parent.resolve()


def _get_applications_file(data_dir: Optional[Path] = None) -> Path:
    base = data_dir if data_dir else Path("data")
    base.mkdir(parents=True, exist_ok=True)
    return base / "applications.json"


def load_applications(data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = _get_applications_file(data_dir)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read applications file: %s", e)
        return []


def save_application(app_data: Dict[str, Any], data_dir: Optional[Path] = None) -> None:
    apps = load_applications(data_dir)
    apps.insert(0, app_data)  # newest first
    # keep last 200
    apps = apps[:200]
    p = _get_applications_file(data_dir)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Could not write applications file: %s", e)


BOT_KEY: web.AppKey[Optional[Bot]] = web.AppKey("bot")
OWNER_ID_KEY: web.AppKey[Optional[int]] = web.AppKey("owner_id")
DATA_DIR_KEY: web.AppKey[Optional[Path]] = web.AppKey("data_dir")


def create_webapp(
    bot: Optional[Bot] = None,
    owner_id: Optional[int] = None,
    data_dir: Optional[Path] = None,
    bot_token: Optional[str] = None,
) -> web.Application:
    """Create configured aiohttp application."""
    app = web.Application()
    app[BOT_KEY] = bot
    app[OWNER_ID_KEY] = owner_id
    app[DATA_DIR_KEY] = data_dir

    async def handle_health(request: web.Request) -> web.Response:
        return web.Response(text="OK", content_type="text/plain")

    async def handle_index(request: web.Request) -> web.FileResponse:
        index_file = WEBAPP_DIR / "index.html"
        return web.FileResponse(index_file)

    async def handle_api_apply(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        app_id = data.get("appId") or f"APP-{hash(str(data)) % 100000:05d}"
        full_name = data.get("fullName", "Noma'lum")
        phone = data.get("phone", "-")
        course_title = data.get("courseTitle", data.get("courseKey", "Umumiy"))
        level = data.get("level", "Boshlang'ich")
        goal = data.get("goal", "-")
        user_id = data.get("userId")
        username = data.get("username")

        app_record = {
            "appId": app_id,
            "fullName": full_name,
            "phone": phone,
            "courseTitle": course_title,
            "level": level,
            "goal": goal,
            "userId": user_id,
            "username": username,
            "status": "new",
            "submittedAt": data.get("submittedAt"),
        }
        save_application(app_record, request.app.get(DATA_DIR_KEY))

        # Notify Owner via Telegram Bot if available
        bot_instance: Optional[Bot] = request.app.get(BOT_KEY)
        owner: Optional[int] = request.app.get(OWNER_ID_KEY)

        if bot_instance and owner:
            uname_text = f"@{username}" if username else f"ID: {user_id or 'noma\'lum'}"
            msg_text = (
                f"🎓 <b>YANGI ARIZA KELDI! (Mini App)</b>\n\n"
                f"🆔 <b>Ariza kodi:</b> <code>{app_id}</code>\n"
                f"👤 <b>Nomzod:</b> {full_name}\n"
                f"📞 <b>Telefon:</b> {phone}\n"
                f"🎯 <b>Yo'nalish:</b> {course_title}\n"
                f"📊 <b>Daraja:</b> {level}\n"
                f"💡 <b>Maqsad:</b> {goal}\n"
                f"💬 <b>Telegram:</b> {uname_text}"
            )
            try:
                await bot_instance.send_message(chat_id=owner, text=msg_text, parse_mode="HTML")
            except Exception as notify_err:
                logger.warning("Could not notify owner about new application: %s", notify_err)

        return web.json_response({"ok": True, "appId": app_id})

    async def handle_api_stats(request: web.Request) -> web.Response:
        apps = load_applications(request.app.get(DATA_DIR_KEY))
        return web.json_response({
            "balance": "$12,450",
            "discipline": "94 / 100",
            "appCount": len(apps),
            "status": "active"
        })

    async def handle_api_applications(request: web.Request) -> web.Response:
        # Applicants' names and phone numbers: owner only (Telegram-signed initData required).
        owner = request.app.get(OWNER_ID_KEY)
        header = request.headers.get("Authorization", "")
        user = verify_init_data(header[4:] if header.startswith("tma ") else "", bot_token or "")
        if not owner or user is None or int(user["id"]) != int(owner):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        apps = load_applications(request.app.get(DATA_DIR_KEY))
        return web.json_response({"ok": True, "applications": apps[:30]})

    async def handle_fin_index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEBAPP_DIR / "fin" / "index.html", headers={"Cache-Control": "no-cache"})

    # Register Routes
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/apply", handle_api_apply)
    app.router.add_get("/api/stats", handle_api_stats)
    app.router.add_get("/api/applications", handle_api_applications)

    # Owner-only finance Mini App ("Nargis Moliya")
    if owner_id and bot_token:
        register_finance_routes(app, owner_id, bot_token)
    app.router.add_get("/fin/", handle_fin_index)
    app.router.add_get("/fin", handle_fin_index)
    app.router.add_static("/fin/", WEBAPP_DIR / "fin")

    # Public static assets: only what the admissions page needs (never server.py, no directory listing)
    async def handle_styles(request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEBAPP_DIR / "styles.css")

    async def handle_script(request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEBAPP_DIR / "app.js")

    app.router.add_get("/styles.css", handle_styles)
    app.router.add_get("/app.js", handle_script)
    app.router.add_static("/assets/", WEBAPP_DIR / "assets")

    return app


async def start_webapp_server(
    port: int,
    bot: Optional[Bot] = None,
    owner_id: Optional[int] = None,
    data_dir: Optional[Path] = None,
    host: str = "0.0.0.0",
) -> web.AppRunner:
    """Start background aiohttp server on given port."""
    app = create_webapp(
        bot=bot, owner_id=owner_id, data_dir=data_dir, bot_token=bot.token if bot is not None else None
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🚀 Nargis Mini App & Health server running at http://%s:%d", host, port)
    return runner


async def stop_webapp_server(runner: Optional[web.AppRunner]) -> None:
    """Gracefully cleanup the web runner."""
    if runner:
        await runner.cleanup()
