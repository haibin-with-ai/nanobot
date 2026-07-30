"""Regression coverage for production cron-store migration shapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.cron.service import CronService
from nanobot.cron.types import CronPayload


def _job(job_id: str, payload: dict) -> dict:
    return {
        "id": job_id,
        "name": job_id,
        "enabled": True,
        "payload": {"message": "ping", **payload},
        "schedule": {"kind": "every", "everyMs": 60_000},
        "state": {"nextRunAtMs": 1},
    }


def test_load_migrates_real_legacy_agent_turn_shapes(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    _job(
                        "camel-delivery",
                        {
                            "kind": "agentTurn",
                            "deliver": True,
                            "channel": "discord",
                            "to": "chat-1",
                        },
                    ),
                    _job(
                        "model-session",
                        {
                            "kind": "agent_turn",
                            "deliver": False,
                            "channel": "discord",
                            "to": "chat-2",
                            "sessionKey": "discord:orphan",
                            "model": "fast",
                        },
                    ),
                    _job(
                        "bound-session",
                        {
                            "kind": "agent_turn",
                            "channel": "discord",
                            "to": "chat-3",
                            "sessionKey": "discord:existing",
                        },
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    jobs = {job.id: job for job in CronService(path).list_jobs(include_disabled=True)}

    assert all(job.enabled for job in jobs.values())
    assert all(job.payload.kind == "agent_turn" for job in jobs.values())

    camel = jobs["camel-delivery"].payload
    assert (camel.session_key, camel.origin_channel, camel.origin_chat_id) == (
        "discord:chat-1",
        "discord",
        "chat-1",
    )
    assert (camel.deliver, camel.channel, camel.to, camel.channel_meta) == (
        False,
        None,
        None,
        {},
    )

    model = jobs["model-session"].payload
    assert model.model == "fast"
    assert (model.session_key, model.origin_channel, model.origin_chat_id) == (
        None,
        None,
        None,
    )
    assert (model.deliver, model.channel, model.to, model.channel_meta) == (
        False,
        None,
        None,
        {},
    )

    bound = jobs["bound-session"].payload
    assert bound.model is None
    assert (bound.session_key, bound.origin_channel, bound.origin_chat_id) == (
        "discord:existing",
        "discord",
        "chat-3",
    )
    assert (bound.deliver, bound.channel, bound.to, bound.channel_meta) == (
        False,
        None,
        None,
        {},
    )


def test_payload_kind_codec_normalizes_aliases_and_rejects_unknown_values() -> None:
    assert CronPayload.from_store_dict({"kind": "agentTurn"}).kind == "agent_turn"
    assert CronPayload.from_store_dict({"kind": "systemEvent"}).kind == "system_event"

    with pytest.raises(ValueError, match="unknown cron payload kind"):
        CronPayload.from_store_dict({"kind": "agent-turn"})
