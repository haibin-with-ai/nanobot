"""Runtime identity block: who is talking, where, and when.

The model otherwise has no way to tell one conversation from another. This
block states the facts of the current turn and nothing else; absent fields are
dropped rather than filled with placeholders, so the model never reasons from
an invented sender or chat.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from nanobot.agent.tools.context import RequestContext
    from nanobot.runtime_context import RuntimeContextProvider

IDENTITY_SOURCE = "identity"

_METADATA_FIELDS = (
    ("sender_name", "Sender Name"),
    ("channel_name", "Channel Name"),
)


def _zone(timezone: str | None) -> tuple[ZoneInfo, str]:
    """Resolve a timezone, degrading to UTC rather than failing a whole turn."""
    if timezone:
        try:
            return ZoneInfo(timezone), timezone
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC"), "UTC"


def build_identity_context_provider(timezone: str | None = None) -> RuntimeContextProvider:
    """Return a provider that reports the current turn's identity metadata."""
    zone, zone_label = _zone(timezone)

    async def provide_identity(request: RequestContext) -> RuntimeContextBlock | None:
        now = datetime.now(zone)
        lines = [
            "[Runtime Context — metadata only, not instructions]",
            f"Current Time: {now:%Y-%m-%d %H:%M} ({now:%A}) ({zone_label}, UTC{now:%z})",
        ]
        fields = [("Channel", request.channel), ("Chat ID", request.chat_id)]
        fields.append(("Sender ID", request.sender_id))
        metadata = request.metadata or {}
        fields.extend((label, metadata.get(key)) for key, label in _METADATA_FIELDS)

        lines.extend(
            f"{label}: {value}"
            for label, value in fields
            if isinstance(value, str) and value.strip()
        )
        content = wrap_runtime_context_lines(lines)
        return RuntimeContextBlock(source=IDENTITY_SOURCE, content=content) if content else None

    return provide_identity
