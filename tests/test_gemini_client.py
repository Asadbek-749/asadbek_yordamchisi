"""Tests for GeminiClient REST API integration."""
from __future__ import annotations

import json
import pytest

from assistant.gemini_client import (
    GeminiAuthError,
    GeminiClient,
    GeminiError,
    GeminiLimitError,
)


class MockResponse:
    def __init__(self, status: int, data: dict | str):
        self.status = status
        self._data = data

    async def text(self) -> str:
        if isinstance(self._data, str):
            return self._data
        return json.dumps(self._data)

    async def json(self) -> dict:
        if isinstance(self._data, dict):
            return self._data
        return json.loads(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockSession:
    def __init__(self, response: MockResponse):
        self._response = response
        self.last_url = None
        self.last_json = None
        self.last_params = None

    def post(self, url: str, params: dict = None, json: dict = None):
        self.last_url = url
        self.last_params = params
        self.last_json = json
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_gemini_client_success(monkeypatch):
    mock_resp = MockResponse(
        200,
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "  Salom, men assistentman!  "}]
                    }
                }
            ]
        },
    )
    mock_session = MockSession(mock_resp)
    monkeypatch.setattr("aiohttp.ClientSession", lambda **kwargs: mock_session)

    client = GeminiClient(api_key="AIzaSyTestKey123", model="gemini-1.5-flash")
    result = await client.generate("System instructions", "Salom")

    assert result == "Salom, men assistentman!"
    assert mock_session.last_params["key"] == "AIzaSyTestKey123"
    assert "system_instruction" in mock_session.last_json
    assert mock_session.last_json["contents"][0]["parts"][0]["text"] == "Salom"


@pytest.mark.asyncio
async def test_gemini_client_missing_key():
    client = GeminiClient(api_key="")
    with pytest.raises(GeminiAuthError) as exc_info:
        await client.generate("sys", "usr")
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_client_rate_limit(monkeypatch):
    mock_resp = MockResponse(429, {"error": {"message": "Resource has been exhausted"}})
    mock_session = MockSession(mock_resp)
    monkeypatch.setattr("aiohttp.ClientSession", lambda **kwargs: mock_session)

    client = GeminiClient(api_key="AIzaSyTestKey123")
    with pytest.raises(GeminiLimitError):
        await client.generate("sys", "usr")


@pytest.mark.asyncio
async def test_gemini_client_invalid_key(monkeypatch):
    mock_resp = MockResponse(400, "API_KEY_INVALID: Key not found")
    mock_session = MockSession(mock_resp)
    monkeypatch.setattr("aiohttp.ClientSession", lambda **kwargs: mock_session)

    client = GeminiClient(api_key="AIzaSyBadKey")
    with pytest.raises(GeminiAuthError):
        await client.generate("sys", "usr")


@pytest.mark.asyncio
async def test_gemini_client_retry_on_503(monkeypatch):
    responses = [
        MockResponse(503, {"error": {"code": 503, "message": "High demand"}}),
        MockResponse(200, {"candidates": [{"content": {"parts": [{"text": "Succeeded after retry"}]}}]}),
    ]

    class SequentialMockSession:
        def __init__(self, resps):
            self._resps = list(resps)
            self.calls = 0

        def post(self, url: str, params: dict = None, json: dict = None):
            self.calls += 1
            if self._resps:
                return self._resps.pop(0)
            return MockResponse(500, "Error")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session = SequentialMockSession(responses)
    monkeypatch.setattr("aiohttp.ClientSession", lambda **kwargs: mock_session)

    client = GeminiClient(api_key="AIzaSyTestKey123", retry_delay=0.001)
    result = await client.generate("sys", "usr")
    assert result == "Succeeded after retry"
    assert mock_session.calls == 2


@pytest.mark.asyncio
async def test_gemini_client_fallback_model(monkeypatch):
    class FallbackMockSession:
        def __init__(self):
            self.attempted_urls = []

        def post(self, url: str, params: dict = None, json: dict = None):
            self.attempted_urls.append(url)
            if "gemini-3.8-flash" in url:
                return MockResponse(503, {"error": {"code": 503, "message": "High demand"}})
            # Fallback model succeeds
            return MockResponse(200, {"candidates": [{"content": {"parts": [{"text": "Fallback reply"}]}}]})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session = FallbackMockSession()
    monkeypatch.setattr("aiohttp.ClientSession", lambda **kwargs: mock_session)

    client = GeminiClient(api_key="AIzaSyTestKey123", model="gemini-3.8-flash", retry_delay=0.001)
    result = await client.generate("sys", "usr")
    assert result == "Fallback reply"
    assert any("gemini-3.8-flash" in u for u in mock_session.attempted_urls)
    assert any("gemini-3.1-flash-lite" in u or "gemini-3.5" in u for u in mock_session.attempted_urls)
