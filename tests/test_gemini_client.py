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
