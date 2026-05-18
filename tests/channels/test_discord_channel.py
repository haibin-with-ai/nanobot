"""Discord channel tests for the native WebSocket implementation."""

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig


def _make_channel(group_policy: str = "open") -> DiscordChannel:
    channel = DiscordChannel(
        DiscordConfig(enabled=True, token="token", allow_from=["*"], group_policy=group_policy),
        MessageBus(),
    )
    channel._bot_user_id = "nanobot-id"
    return channel


def test_group_policy_open_ignores_message_mentioning_other_bot_only() -> None:
    channel = _make_channel(group_policy="open")

    payload = {
        "channel_id": "channel-1",
        "mentions": [{"id": "hermes-id", "bot": True, "username": "hermes"}],
    }

    assert channel._should_respond_in_group(payload, "<@hermes-id> hello") is False


def test_group_policy_open_accepts_message_without_bot_mention() -> None:
    channel = _make_channel(group_policy="open")

    payload = {"channel_id": "channel-1", "mentions": []}

    assert channel._should_respond_in_group(payload, "hello") is True


def test_group_policy_open_accepts_message_mentioning_nanobot() -> None:
    channel = _make_channel(group_policy="open")

    payload = {
        "channel_id": "channel-1",
        "mentions": [{"id": "nanobot-id", "bot": True, "username": "nanobot"}],
    }

    assert channel._should_respond_in_group(payload, "<@nanobot-id> hello") is True


def test_group_policy_mention_accepts_message_mentioning_nanobot_among_other_bots() -> None:
    channel = _make_channel(group_policy="mention")

    payload = {
        "channel_id": "channel-1",
        "mentions": [
            {"id": "hermes-id", "bot": True, "username": "hermes"},
            {"id": "nanobot-id", "bot": True, "username": "nanobot"},
        ],
    }

    assert channel._should_respond_in_group(payload, "<@hermes-id> <@nanobot-id> compare") is True
