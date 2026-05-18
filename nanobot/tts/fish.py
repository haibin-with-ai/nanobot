"""Fish Audio TTS provider using httpx."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from nanobot.tts.base import TTSProvider, TTSError


class FishTTSProvider(TTSProvider):
    """TTS provider using Fish Audio API."""

    def __init__(
        self,
        voice: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.voice = voice or ""
        self.api_key = api_key or os.environ.get("FISH_AUDIO_API_KEY", "")
        self.api_url = api_base or os.environ.get(
            "FISH_AUDIO_BASE_URL",
            "https://api.fish.audio/v1/tts",
        )

    async def synthesize(self, text: str, output_path: Path) -> Path:
        if not self.api_key:
            raise TTSError("Fish Audio API key not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"text": text}
        if self.voice:
            payload["reference_id"] = self.voice

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                output_path.write_bytes(response.content)
        except httpx.HTTPStatusError as e:
            logger.warning("Fish Audio TTS HTTP error: {}", e)
            raise TTSError(f"Fish Audio TTS failed: {e.response.status_code}") from e
        except Exception as e:
            logger.warning("Fish Audio TTS request failed: {}", e)
            raise TTSError(f"Fish Audio TTS request failed: {e}") from e

        return output_path
