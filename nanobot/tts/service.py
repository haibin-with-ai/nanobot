"""TTS service orchestrator."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.config.paths import get_media_dir
from nanobot.tts.base import TTSError
from nanobot.tts.factory import create_provider

if TYPE_CHECKING:
    from nanobot.config.schema import TTSConfig


class TTSService:
    """High-level TTS service that wraps a provider."""

    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self._provider = create_provider(config)
        self.logger = logger.bind(component="tts")

    def should_trigger(self, sender_id: str, metadata: dict[str, Any]) -> bool:
        """Return whether TTS should be triggered for this outbound message."""
        if not self.config.enabled:
            return False
        if metadata.get("_outbound_tts"):
            return True
        if sender_id in self.config.auto_tts_senders:
            return True
        return False

    async def synthesize(self, text: str) -> str:
        """Synthesize text to an audio file and return its path.

        The text is truncated to ``max_text_length`` if necessary.
        """
        if len(text) > self.config.max_text_length:
            text = text[: self.config.max_text_length]
            self.logger.debug("TTS text truncated to {} chars", self.config.max_text_length)

        output_path = get_media_dir() / f"tts_{uuid.uuid4().hex}.mp3"
        result = await self._provider.synthesize(text, output_path)
        return str(result)
