"""Tests for TTS service and providers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.config.schema import TTSConfig
from nanobot.tts.base import TTSError, TTSProvider
from nanobot.tts.factory import create_provider
from nanobot.tts.service import TTSService


class _FakeProvider(TTSProvider):
    """Fake provider for testing."""

    def __init__(self, voice: str | None = None, **kwargs) -> None:
        self.voice = voice

    async def synthesize(self, text: str, output_path: Path) -> Path:
        output_path.write_text(f"fake audio: {text}")
        return output_path


class _FailingProvider(TTSProvider):
    """Provider that always fails."""

    async def synthesize(self, text: str, output_path: Path) -> Path:
        raise TTSError("synth failed")


def test_should_trigger_disabled() -> None:
    config = TTSConfig(enabled=False)
    service = TTSService(config)
    assert service.should_trigger("123", {}) is False


def test_should_trigger_metadata() -> None:
    config = TTSConfig(enabled=True)
    service = TTSService(config)
    assert service.should_trigger("123", {"_outbound_tts": True}) is True
    assert service.should_trigger("123", {}) is False


def test_should_trigger_auto_sender() -> None:
    config = TTSConfig(enabled=True, auto_tts_senders=["456"])
    service = TTSService(config)
    assert service.should_trigger("456", {}) is True
    assert service.should_trigger("123", {}) is False


@pytest.mark.asyncio
async def test_synthesize(tmp_path: Path) -> None:
    config = TTSConfig(enabled=True, max_text_length=100)
    service = TTSService(config)
    service._provider = _FakeProvider()

    with patch("nanobot.tts.service.get_media_dir", return_value=tmp_path):
        path = await service.synthesize("hello world")

    assert Path(path).exists()
    assert Path(path).read_text() == "fake audio: hello world"


@pytest.mark.asyncio
async def test_synthesize_truncation(tmp_path: Path) -> None:
    config = TTSConfig(enabled=True, max_text_length=5)
    service = TTSService(config)
    service._provider = _FakeProvider()

    with patch("nanobot.tts.service.get_media_dir", return_value=tmp_path):
        path = await service.synthesize("hello world")

    assert Path(path).read_text() == "fake audio: hello"


@pytest.mark.asyncio
async def test_synthesize_failure(tmp_path: Path) -> None:
    config = TTSConfig(enabled=True)
    service = TTSService(config)
    service._provider = _FailingProvider()

    with patch("nanobot.tts.service.get_media_dir", return_value=tmp_path):
        with pytest.raises(TTSError, match="synth failed"):
            await service.synthesize("hello")


def test_factory_edge_tts() -> None:
    config = TTSConfig(provider="edge-tts", voice="en-US-AriaNeural")
    provider = create_provider(config)
    assert provider.voice == "en-US-AriaNeural"


def test_factory_fish() -> None:
    config = TTSConfig(provider="fish", voice="test-voice", provider_config={"api_key": "abc"})
    provider = create_provider(config)
    assert provider.voice == "test-voice"
    assert provider.api_key == "abc"


def test_factory_unknown() -> None:
    config = MagicMock()
    config.provider = "unknown"
    config.provider_config = {}
    with pytest.raises(TTSError, match="Unknown TTS provider"):
        create_provider(config)
