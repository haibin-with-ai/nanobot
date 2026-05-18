"""TTS provider factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.config.paths import get_media_dir
from nanobot.tts.base import TTSProvider, TTSError
from nanobot.tts.edge import EdgeTTSProvider
from nanobot.tts.fish import FishTTSProvider

if TYPE_CHECKING:
    from nanobot.config.schema import TTSConfig


def create_provider(config: TTSConfig) -> TTSProvider:
    """Create a TTS provider from config."""
    if config.provider == "edge-tts":
        return EdgeTTSProvider(
            voice=config.voice or None,
            **config.provider_config,
        )
    if config.provider == "fish":
        return FishTTSProvider(
            voice=config.voice or None,
            **config.provider_config,
        )
    raise TTSError(f"Unknown TTS provider: {config.provider}")
