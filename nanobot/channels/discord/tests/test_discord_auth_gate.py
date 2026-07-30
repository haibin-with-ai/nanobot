from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("discord")
import discord

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord.runtime import DiscordBotClient, DiscordChannel, DiscordConfig


class _InteractionResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


def _unauthorized_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=99,
        type=discord.MessageType.default,
        author=SimpleNamespace(id=42, display_name="intruder"),
        channel=SimpleNamespace(id=7),
        guild=None,
        content="look at this",
        attachments=[SimpleNamespace(id=8, filename="payload.bin")],
        reference=None,
    )


@pytest.mark.asyncio
async def test_unauthorized_dm_is_rejected_before_payload_loading() -> None:
    channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
    channel._download_attachments = AsyncMock(return_value=([], []))
    channel.send = AsyncMock()

    await channel._handle_discord_message(_unauthorized_message())

    channel._download_attachments.assert_not_awaited()
    channel.send.assert_awaited_once()
    assert "pairing code" in channel.send.await_args.args[0].content.lower()


@pytest.mark.asyncio
async def test_rejected_slash_answers_denial_and_never_publishes() -> None:
    channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
    channel.bus.publish_inbound = AsyncMock()
    client = DiscordBotClient(channel, intents=discord.Intents.none())
    response = _InteractionResponse()
    interaction = SimpleNamespace(
        id=101,
        user=SimpleNamespace(id=42),
        channel_id=7,
        channel=SimpleNamespace(id=7),
        guild_id=5,
        response=response,
    )

    await client._forward_slash_command(interaction, "/new")

    assert response.messages == [
        {"content": "You are not allowed to use this bot.", "ephemeral": True}
    ]
    channel.bus.publish_inbound.assert_not_awaited()
