"""cron add 的 model 必须当场校验，不能等到半夜触发才炸。

spawn 早就在参数校验阶段 resolve preset，认不出来立刻报「unknown preset, available: ...」。
cron 过去把 model 当裸字符串存进 payload，拼错的任务要到真正触发时才抛异常。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.tools.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from nanobot.agent.tools.cron import CronTool


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve_preset(self, name: str) -> Any:
        self.calls.append(name)
        if name != "fast":
            raise KeyError(f"unknown preset '{name}', available: fast, deep")
        return MagicMock(name="fast-runtime")


@pytest.fixture
def route():
    token = bind_request_context(
        RequestContext(channel="discord", chat_id="42", session_key="discord:42", metadata={})
    )
    yield
    reset_request_context(token)


@pytest.fixture
def cron_service() -> MagicMock:
    service = MagicMock()
    service.add_job.return_value = MagicMock(name="job", id="job-1")
    service.add_job.return_value.name = "nightly"
    return service


@pytest.mark.asyncio
async def test_unknown_preset_is_rejected_before_the_job_exists(route, cron_service) -> None:
    tool = CronTool(cron_service=cron_service, default_timezone="UTC", runtime_resolver=_Resolver())

    result = await tool.execute(
        action="add", message="ping", every_seconds=60, model="deeeep"
    )

    assert "deeeep" in result
    assert "available" in result
    cron_service.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_known_preset_still_creates_the_job(route, cron_service) -> None:
    resolver = _Resolver()
    tool = CronTool(cron_service=cron_service, default_timezone="UTC", runtime_resolver=resolver)

    result = await tool.execute(action="add", message="ping", every_seconds=60, model="fast")

    assert "Created job" in result
    assert resolver.calls == ["fast"]
    assert cron_service.add_job.call_args.kwargs["model"] == "fast"


@pytest.mark.asyncio
async def test_missing_resolver_keeps_the_old_permissive_behaviour(route, cron_service) -> None:
    """没有 resolver 时忽略 model 校验，与 spawn 的处理一致。"""
    tool = CronTool(cron_service=cron_service, default_timezone="UTC")

    result = await tool.execute(action="add", message="ping", every_seconds=60, model="whatever")

    assert "Created job" in result
    assert cron_service.add_job.call_args.kwargs["model"] == "whatever"
