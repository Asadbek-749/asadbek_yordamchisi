"""Google Gemini API client wrapper for the Telegram Business assistant."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

from assistant.claude_cli import ClaudeAuthError, ClaudeError, ClaudeLimitError, ClaudeTimeout

logger = logging.getLogger(__name__)


class GeminiError(ClaudeError):
    """Base error for Gemini API interactions."""


class GeminiLimitError(ClaudeLimitError):
    """Raised when Gemini usage limits or quota are hit (HTTP 429)."""


class GeminiAuthError(ClaudeAuthError):
    """Raised when Gemini API key is missing or rejected."""


class GeminiClient:
    """Async client communicating with Google Gemini REST API."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.1-flash-lite",
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-3.1-flash-lite"
        self.timeout = max(1, timeout)

    @property
    def auth_mode(self) -> str:
        return f"Gemini API key ({self.model})"

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text completion from Gemini."""
        if not self.api_key:
            raise GeminiAuthError(
                "GEMINI_API_KEY is not configured in .env. "
                "Get a free API key at https://aistudio.google.com/ and set GEMINI_API_KEY in .env."
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 1024,
            },
        }

        if system_prompt and system_prompt.strip():
            payload["system_instruction"] = {
                "parts": [{"text": system_prompt.strip()}]
            }

        client_timeout = aiohttp.ClientTimeout(total=float(self.timeout))

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, params=params, json=payload) as resp:
                    status = resp.status
                    text = await resp.text()

                    if status == 200:
                        try:
                            data = await resp.json()
                        except Exception:
                            raise GeminiError(f"Invalid JSON response: {text[:200]}")

                        candidates = data.get("candidates", [])
                        if not candidates:
                            prompt_feedback = data.get("promptFeedback", {})
                            block_reason = prompt_feedback.get("blockReason")
                            if block_reason:
                                logger.warning("Gemini prompt blocked: %s", block_reason)
                            return ""

                        first_cand = candidates[0]
                        content = first_cand.get("content", {})
                        parts = content.get("parts", [])
                        if not parts:
                            return ""
                        return str(parts[0].get("text", "")).strip()

                    # Handle API errors
                    if status == 429:
                        raise GeminiLimitError("Gemini rate limit or quota exceeded.")
                    elif status in (400, 401, 403):
                        if "API_KEY_INVALID" in text or "key not valid" in text.lower():
                            raise GeminiAuthError(f"Gemini API key is invalid: {text[:250]}")
                        raise GeminiError(f"Gemini client error ({status}): {text[:250]}")
                    else:
                        raise GeminiError(f"Gemini server error ({status}): {text[:250]}")

        except asyncio.TimeoutError:
            raise ClaudeTimeout(f"Gemini API request timed out after {self.timeout}s")
        except (GeminiError, ClaudeError):
            raise
        except Exception as e:
            raise GeminiError(f"Gemini request failed: {e}")

    async def inspect(self) -> Dict[str, Any]:
        """Perform a quick ping query to verify connectivity and API key validity."""
        reply = await self.generate("Reply with exactly: OK", "ping")
        return {
            "auth": self.auth_mode,
            "provider": "google_gemini",
            "model": self.model,
            "reply": reply,
        }
