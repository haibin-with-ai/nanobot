"""Edge TTS provider using edge-tts library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.tts.base import TTSProvider, TTSError


class EdgeTTSProvider(TTSProvider):
    """TTS provider using Microsoft Edge's online text-to-speech service."""

    def __init__(self, voice: str | None = None, **kwargs: Any) -> None:
        self.voice = voice or "en-US-AriaNeural"

    async def synthesize(self, text: str, output_path: Path) -> Path:
        try:
            import edge_tts
        except ImportError as e:
            raise TTSError("edge-tts library not installed") from e

        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(str(output_path))
        return output_path
