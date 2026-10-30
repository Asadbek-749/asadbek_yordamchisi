"""Unit tests for Telegram Mini App server, REST endpoints, and router handlers."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

from webapp.server import create_webapp, load_applications, save_application
from tests.test_fin_webapp import make_init_data
from assistant.config import load_settings
from nargis.webapp_router import build_webapp_router


def test_webapp_url_config(tmp_path):
    """Verify that WEBAPP_URL is loaded correctly into Settings."""
    env_content = (
        "TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11\n"
        "OWNER_ID=5146977644\n"
        "WEBAPP_URL=https://nargis-mini-app.up.railway.app\n"
        "AI_PROVIDER=gemini\n"
        "GEMINI_API_KEY=test-key\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text(env_content, encoding="utf-8")

    settings = load_settings(env_file)
    assert settings.webapp_url == "https://nargis-mini-app.up.railway.app"
    assert settings.owner_id == 5146977644


def test_save_and_load_applications(tmp_path):
    """Test saving and retrieving student admission applications."""
    data_dir = tmp_path / "data"
    
    app1 = {
        "appId": "APP-11111",
        "fullName": "Sardor Aliyev",
        "phone": "+998901234567",
        "courseTitle": "Sun'iy intellekt & Prompt Engineering",
        "level": "beginner",
        "goal": "AI yordamida startup qurish",
    }
    app2 = {
        "appId": "APP-22222",
        "fullName": "Madina Karimova",
        "phone": "+998939876543",
        "courseTitle": "Vibe Coding",
        "level": "intermediate",
        "goal": "Full-stack Mini App yaratish",
    }

    save_application(app1, data_dir=data_dir)
    save_application(app2, data_dir=data_dir)

    loaded = load_applications(data_dir=data_dir)
    assert len(loaded) == 2
    # newest first
    assert loaded[0]["appId"] == "APP-22222"
    assert loaded[1]["appId"] == "APP-11111"


from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_webapp_endpoints(tmp_path):
    """Test HTTP endpoints for Mini App (/health, /api/apply, /api/stats, /api/applications)."""
    data_dir = tmp_path / "data"
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    app = create_webapp(bot=mock_bot, owner_id=5146977644, data_dir=data_dir, bot_token="123456:TEST")
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. /health
        resp = await client.get("/health")
        assert resp.status == 200
        text = await resp.text()
        assert text == "OK"

        # 2. /api/apply
        apply_payload = {
            "appId": "APP-77777",
            "fullName": "Jasur Bek",
            "phone": "+998940001122",
            "courseTitle": "AI Pro",
            "level": "advanced",
            "goal": "Agentic tizimlar",
            "userId": 987654321,
            "username": "jasurbek",
        }
        resp = await client.post("/api/apply", json=apply_payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["appId"] == "APP-77777"

        # Verify owner got notified via bot
        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args[1]
        assert call_args["chat_id"] == 5146977644
        assert "Jasur Bek" in call_args["text"]
        assert "APP-77777" in call_args["text"]

        # 3. /api/stats
        resp = await client.get("/api/stats")
        assert resp.status == 200
        stats = await resp.json()
        assert stats["status"] == "active"
        assert stats["appCount"] == 1

        # 4. /api/applications holds applicants' phone numbers: owner-only (signed Telegram initData)
        resp = await client.get("/api/applications")
        assert resp.status == 401
        not_owner = {"Authorization": "tma " + make_init_data("123456:TEST", 111)}
        assert (await client.get("/api/applications", headers=not_owner)).status == 401
        owner = {"Authorization": "tma " + make_init_data("123456:TEST", 5146977644)}
        resp = await client.get("/api/applications", headers=owner)
        assert resp.status == 200
        apps_data = await resp.json()
        assert apps_data["ok"] is True
        assert len(apps_data["applications"]) == 1
        assert apps_data["applications"][0]["fullName"] == "Jasur Bek"
    finally:
        await client.close()


def test_build_webapp_router():
    """Verify router configuration for WebApp."""
    router = build_webapp_router(owner_id=5146977644, webapp_url="https://example.com/app")
    assert router is not None
    assert router.name == "webapp_router"
