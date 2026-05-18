"""Base TTS provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSError(Exception):
    """Raised when TTS synthesis fails."""


class TTSProvider(ABC):
    """Abstract base class for text-to-speech providers."""

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize text to an audio file at ``output_path``.

        Returns:
            The path to the generated audio file.

        Raises:
            TTSError: If synthesis fails.
        """
        ...
