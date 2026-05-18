"""Text-to-speech module."""

from __future__ import annotations

from nanobot.tts.base import TTSError, TTSProvider
from nanobot.tts.factory import create_provider
from nanobot.tts.service import TTSService

__all__ = ["TTSError", "TTSProvider", "create_provider", "TTSService"]
