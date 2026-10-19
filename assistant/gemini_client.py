"""Google Gemini API client wrapper for the Telegram Business assistant."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from assistant.claude_cli import ClaudeAuthError, ClaudeError, ClaudeLimitError, ClaudeTimeout

logger = logging.getLogger(__name__)

# Fast, reliable lightweight models to fall back to if the selected model is experiencing high demand (503)
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite")


class GeminiError(ClaudeError):
    """Base error for Gemini API interactions."""


class GeminiLimitError(ClaudeLimitError):
    """Raised when Gemini usage limits or quota are hit (HTTP 429)."""


class GeminiAuthError(ClaudeAuthError):
    """Raised when Gemini API key is missing or rejected."""


class GeminiClient:
    """Async client communicating with Google Gemini REST API with retry and fallback support."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.5-flash-lite",
        timeout: int = 60,
        retry_delay: float = 1.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-3.5-flash-lite"
        self.timeout = max(1, timeout)
        self.retry_delay = retry_delay

    @property
    def auth_mode(self) -> str:
        return f"Gemini API key ({self.model})"

    def _build_models_to_try(self) -> List[str]:
        models = [self.model]
        for fb in DEFAULT_FALLBACK_MODELS:
            if fb not in models:
                models.append(fb)
        return models

    async def _post_generate(
        self, session: aiohttp.ClientSession, model_name: str, payload: Dict[str, Any]
    ) -> tuple[int, str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        params = {"key": self.api_key}
        async with session.post(url, params=params, json=payload) as resp:
            status = resp.status
            text = await resp.text()
            return status, text

    async def _execute_payload(self, payload: Dict[str, Any]) -> str:
        """Execute request payload against Gemini API with retry and model fallback."""
        if not self.api_key:
            raise GeminiAuthError(
                "GEMINI_API_KEY is not configured in .env. "
                "Get a free API key at https://aistudio.google.com/ and set GEMINI_API_KEY in .env."
            )

        client_timeout = aiohttp.ClientTimeout(total=float(self.timeout))
        models_to_try = self._build_models_to_try()
        last_error: Optional[Exception] = None

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                for model_idx, current_model in enumerate(models_to_try):
                    max_attempts = 2 if len(models_to_try) > 1 else 3
                    for attempt in range(1, max_attempts + 1):
                        try:
                            status, text = await self._post_generate(session, current_model, payload)
                        except asyncio.TimeoutError:
                            raise ClaudeTimeout(f"Gemini API request timed out after {self.timeout}s")
                        except Exception as e:
                            last_error = GeminiError(f"Gemini network request failed: {e}")
                            if attempt < max_attempts:
                                await asyncio.sleep(self.retry_delay)
                                continue
                            break

                        if status == 200:
                            if current_model != self.model:
                                logger.info(
                                    "Successfully generated reply using fallback model: %s (primary %s was unavailable)",
                                    current_model,
                                    self.model,
                                )
                            try:
                                data = json.loads(text)
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

                        # Non-retryable client errors
                        if status == 429:
                            raise GeminiLimitError("Gemini rate limit or quota exceeded.")
                        elif status in (400, 401, 403):
                            if "API_KEY_INVALID" in text or "key not valid" in text.lower():
                                raise GeminiAuthError(f"Gemini API key is invalid: {text[:250]}")
                            raise GeminiError(f"Gemini client error ({status}): {text[:250]}")

                        # 503 / 500 / 502 / 504: Transient high demand / server error
                        last_error = GeminiError(f"Gemini server error ({status}): {text[:250]}")
                        logger.warning(
                            "Gemini model %s returned status %d (attempt %d/%d). %s",
                            current_model,
                            status,
                            attempt,
                            max_attempts,
                            "Retrying..." if attempt < max_attempts else "Switching to fallback model...",
                        )
                        if attempt < max_attempts:
                            await asyncio.sleep(self.retry_delay * attempt)

                if last_error:
                    raise last_error
                raise GeminiError("Gemini request failed: All models and retries exhausted.")

        except (GeminiError, ClaudeError):
            raise
        except Exception as e:
            raise GeminiError(f"Gemini request failed: {e}")

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text completion from Gemini with automatic retry and model fallback."""
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

        return await self._execute_payload(payload)

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        prompt: str = "Ushbu ovozli xabarni so'zma-so'z o'zbek tilida transkripsiya qil. Faqat eshitilgan gapni yoz, boshqa hech qanday izoh qo'shma.",
    ) -> str:
        """Transcribe audio bytes (OGG Opus, MP3, WAV) into text using Gemini multimodal API."""
        import base64
        b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_audio,
                            }
                        },
                        {
                            "text": prompt,
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024,
            },
        }
        return await self._execute_payload(payload)

    async def inspect(self) -> Dict[str, Any]:
        """Perform a quick ping query to verify connectivity and API key validity."""
        reply = await self.generate("Reply with exactly: OK", "ping")
        return {
            "auth": self.auth_mode,
            "provider": "google_gemini",
            "model": self.model,
            "reply": reply,
        }
