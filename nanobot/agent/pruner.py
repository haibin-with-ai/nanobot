"""Context pruner for transient tool result trimming."""

from __future__ import annotations

from typing import Any

from nanobot.config.schema import ContextPruningConfig


def _soft_trim(content: str, target_size: int) -> str:
    """Trim a string to target_size by removing middle lines or characters."""
    if len(content) <= target_size:
        return content
    lines = content.split("\n")
    if len(lines) > 2:
        # Try keeping first and last line with a placeholder in between.
        candidate = lines[0] + "\n...\n" + lines[-1]
        if len(candidate) <= target_size:
            return candidate
    # Fall back to removing middle characters.
    placeholder = "...[{} characters trimmed]".format(len(content) - target_size)
    keep = target_size - len(placeholder)
    if keep <= 0:
        # Edge case: target_size smaller than placeholder itself.
        return content[:target_size]
    head = keep // 2
    tail = keep - head
    return content[:head] + placeholder + content[-tail:]


class ContextPruner:
    """Immutable pruner that trims or clears oversized tool result messages."""

    def __init__(self, config: ContextPruningConfig) -> None:
        self.config = config

    @staticmethod
    def _find_protected_boundary(messages: list[dict[str, Any]], keep_last_assistants: int) -> int:
        """Return the index of the first message that is protected."""
        if keep_last_assistants <= 0:
            return len(messages)
        assistant_indices = [
            idx for idx, msg in enumerate(messages) if msg.get("role") == "assistant"
        ]
        if len(assistant_indices) < keep_last_assistants:
            return 0
        return assistant_indices[-keep_last_assistants]

    def prune(self, messages: list[dict[str, Any]], context_window_chars: int) -> list[dict[str, Any]]:
        """Return a new message list with eligible tool results trimmed/cleared."""
        if not self.config.enabled:
            return messages

        boundary = self._find_protected_boundary(messages, self.config.keep_last_assistants)

        result: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if idx >= boundary:
                result.append(dict(msg))
                continue

            role = msg.get("role")
            content = msg.get("content")

            if role != "tool":
                result.append(dict(msg))
                continue

            if not isinstance(content, str):
                if isinstance(content, list):
                    if any(
                        isinstance(block, dict) and block.get("type") in ("image_url", "image")
                        for block in content
                    ):
                        result.append(dict(msg))
                        continue
                result.append(dict(msg))
                continue

            new_msg = dict(msg)
            # hard_clear takes priority
            if (
                self.config.hard_clear.enabled
                and context_window_chars > 0
                and len(content) / context_window_chars > self.config.hard_clear.ratio
            ):
                new_msg["content"] = "[{} characters cleared]".format(len(content))
                result.append(new_msg)
                continue

            if self.config.soft_trim.enabled and len(content) > self.config.soft_trim.chunk_size:
                target_size = self.config.soft_trim.chunk_size * self.config.soft_trim.chunk_count
                new_msg["content"] = _soft_trim(content, target_size)
                result.append(new_msg)
                continue

            result.append(new_msg)

        return result
