"""slash 命令的真实分支测试：不替换 _forward_slash_command，只替换 Discord 响应与总线。

命令树形状的契约测试证明的是菜单长什么样，证明不了一次 interaction 怎么走过
鉴权、回执与 publish。这里补上放行与 publish 失败两条路径，
拒绝路径在 test_discord_auth_gate.py。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("discord")
import discord

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord.runtime import DiscordBotClient, DiscordChannel, DiscordConfig


class _InteractionResponse:
    """response.send_message 只能用一次，之后的话必须走 followup，和真实 Discord 一致。"""

    def __init__(self, sink: list[dict[str, object]]) -> None:
        self._sink = sink
        self._used = False

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        if self._used:
            raise RuntimeError("This interaction has already been responded to")
        self._used = True
        self._sink.append({"via": "response", "content": content, "ephemeral": ephemeral})


class _InteractionFollowup:
    def __init__(self, sink: list[dict[str, object]]) -> None:
        self._sink = sink

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self._sink.append({"via": "followup", "content": content, "ephemeral": ephemeral})


def _interaction() -> SimpleNamespace:
    sink: list[dict[str, object]] = []
    return SimpleNamespace(
        id=101,
        user=SimpleNamespace(id=42),
        channel_id=7,
        channel=SimpleNamespace(id=7),
        guild_id=5,
        response=_InteractionResponse(sink),
        followup=_InteractionFollowup(sink),
        sent=sink,
    )


def _allowed_client() -> tuple[DiscordChannel, DiscordBotClient]:
    channel = DiscordChannel(DiscordConfig(token="test", allow_from=["42"]), MessageBus())
    client = DiscordBotClient(channel, intents=discord.Intents.none())
    return channel, client


@pytest.mark.asyncio
async def test_allowed_slash_acks_then_publishes_once() -> None:
    channel, client = _allowed_client()
    channel._handle_message = AsyncMock()
    interaction = _interaction()

    await client._forward_slash_command(interaction, "/new")

    assert interaction.sent == [
        {"via": "response", "content": "Processing /new...", "ephemeral": True}
    ]
    channel._handle_message.assert_awaited_once()
    kwargs = channel._handle_message.await_args.kwargs
    assert kwargs["content"] == "/new"
    assert kwargs["chat_id"] == "7"
    assert kwargs["metadata"]["is_slash_command"] is True


def _preset_autocomplete_callback(client: DiscordBotClient):
    return client.tree.get_command("model")._params["preset"].autocomplete


@pytest.mark.asyncio
async def test_model_preset_autocomplete_matches_substring() -> None:
    """输入 fable 要能挑出 claude-fable-5：大小写不敏感的子串匹配。"""
    channel, client = _allowed_client()
    channel._model_preset_names = lambda: [
        "default", "claude-fable-5", "claude-sonnet-5", "codex-gpt-5.6-sol",
    ]
    callback = _preset_autocomplete_callback(client)

    choices = await callback(_interaction(), "FABLE")

    assert [c.value for c in choices] == ["claude-fable-5"]
    assert all(c.name == c.value for c in choices)


@pytest.mark.asyncio
async def test_model_preset_autocomplete_empty_input_lists_all() -> None:
    channel, client = _allowed_client()
    channel._model_preset_names = lambda: ["default", "a", "b"]
    callback = _preset_autocomplete_callback(client)

    choices = await callback(_interaction(), "")

    assert [c.value for c in choices] == ["default", "a", "b"]


@pytest.mark.asyncio
async def test_model_preset_autocomplete_caps_at_25() -> None:
    channel, client = _allowed_client()
    channel._model_preset_names = lambda: [f"preset-{i:02d}" for i in range(30)]
    callback = _preset_autocomplete_callback(client)

    choices = await callback(_interaction(), "preset")

    assert len(choices) == 25


def test_model_preset_names_reads_catalog_and_orders_default_first(monkeypatch) -> None:
    from nanobot.agent import model_presets

    monkeypatch.setattr(
        model_presets, "load_model_preset_catalog",
        lambda: {"zeta": object(), "default": object(), "alpha": object()},
    )
    channel, _ = _allowed_client()

    assert channel._model_preset_names() == ["default", "alpha", "zeta"]


def test_model_preset_names_survives_broken_config(monkeypatch) -> None:
    """配置读不出来时降级为空列表，autocomplete 不炸命令树。"""
    from nanobot.agent import model_presets

    def _boom() -> dict:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(model_presets, "load_model_preset_catalog", _boom)
    channel, _ = _allowed_client()

    assert channel._model_preset_names() == []


@pytest.mark.asyncio
async def test_publish_failure_is_reported_instead_of_silence() -> None:
    """回执已经发过 Processing，publish 再炸就必须补一句失败，不能让用户干等。"""
    channel, client = _allowed_client()
    channel._handle_message = AsyncMock(side_effect=RuntimeError("bus down"))
    interaction = _interaction()

    await client._forward_slash_command(interaction, "/new")

    assert interaction.sent[0]["content"] == "Processing /new..."
    tail = interaction.sent[1:]
    assert tail, "publish 炸了却什么都没告诉用户"
    assert tail[0]["via"] == "followup"
    assert "failed" in str(tail[0]["content"]).lower()
