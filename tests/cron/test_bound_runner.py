"""Two execution modes for cron jobs.

A job created from a chat continues that conversation. A job with no binding
gets a throwaway session of its own, so a nightly task cannot inherit or
pollute whatever a human was last talking about.
"""

from __future__ import annotations

import pytest

from nanobot.cron.bound_runner import run_cron_job
from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule

_BOUND = {
    "session_key": "discord:123",
    "origin_channel": "discord",
    "origin_chat_id": "chat-9",
}


def _job(job_id="job-1", **payload_kw) -> CronJob:
    payload = CronPayload(kind="agent_turn", message="ping", **payload_kw)
    return CronJob(
        id=job_id,
        name="nightly",
        enabled=True,
        payload=payload,
        schedule=CronSchedule(kind="every", every_ms=60_000),
        state=CronJobState(),
    )


class _Recorder:
    def __init__(self):
        self.records: list[tuple[str, dict]] = []

    def write_run_record(self, run_id, record):
        self.records.append((run_id, dict(record)))

    def status_of(self, run_id):
        return [r["status"] for rid, r in self.records if rid == run_id]

    @property
    def last(self):
        return self.records[-1][1]


class _Agent:
    """Minimal stand-in exposing only what the runner is allowed to touch."""

    def __init__(self, response="done"):
        self.tools = _Tools()
        self.direct_calls: list[dict] = []
        self.bound_calls: list = []
        self._response = response
        self.sessions = _Sessions()

    async def process_direct(self, content, **kw):
        self.direct_calls.append({"content": content, **kw})
        if isinstance(self._response, Exception):
            raise self._response
        return _Resp(self._response)

    async def submit_cron_turn(self, msg):
        self.bound_calls.append(msg)
        return _Resp(self._response)

    def set_session_model_preset(self, session_key, name):
        self.sessions.presets[session_key] = name
        if name == "nonexistent":
            raise KeyError(name)
        return _Runtime(name)


class _Runtime:
    def __init__(self, preset):
        self.model_preset = preset
        self.model = f"model-for-{preset}"


class _Sessions:
    def __init__(self):
        self.presets: dict[str, str] = {}
        self.deleted: list[str] = []
        self.prune_calls = 0

    def delete(self, key):
        self.deleted.append(key)

    def maybe_prune_cron_run_sessions(self):
        self.prune_calls += 1


class _Tools:
    def get(self, name):
        return None


class _Resp:
    def __init__(self, content):
        self.content = content


class TestUnboundRunsInItsOwnSession:
    @pytest.mark.asyncio
    async def test_session_key_carries_job_and_run(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(), agent=agent, cron=cron)
        key = agent.direct_calls[0]["session_key"]
        assert key.startswith("cron:job-1:")
        assert key != "cron:job-1:"

    @pytest.mark.asyncio
    async def test_two_runs_of_one_job_do_not_share_a_session(self):
        """Concurrent runs of the same job must not collide."""
        agent, cron = _Agent(), _Recorder()
        job = _job()
        await run_cron_job(job, agent=agent, cron=cron)
        await run_cron_job(job, agent=agent, cron=cron)
        keys = [c["session_key"] for c in agent.direct_calls]
        assert len(set(keys)) == 2

    @pytest.mark.asyncio
    async def test_the_turn_runs_exactly_once(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(), agent=agent, cron=cron)
        assert len(agent.direct_calls) == 1
        assert agent.bound_calls == []

    @pytest.mark.asyncio
    async def test_unspecified_model_runs_on_deep(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(), agent=agent, cron=cron)
        key = agent.direct_calls[0]["session_key"]
        assert agent.sessions.presets[key] == "deep"

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_the_default(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(model="fast"), agent=agent, cron=cron)
        key = agent.direct_calls[0]["session_key"]
        assert agent.sessions.presets[key] == "fast"

    @pytest.mark.asyncio
    async def test_run_record_names_the_session_and_model(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(model="fast"), agent=agent, cron=cron)
        record = cron.last
        assert record["status"] == "ok"
        assert record["session_key"].startswith("cron:job-1:")
        assert record["model"] == "fast"

    @pytest.mark.asyncio
    async def test_unknown_preset_fails_the_run_instead_of_hanging(self):
        """A queued run that cannot resolve must land on failed, not stay running."""
        agent, cron = _Agent(), _Recorder()
        with pytest.raises(KeyError):
            await run_cron_job(_job(model="nonexistent"), agent=agent, cron=cron)
        assert cron.last["status"] == "error"
        assert agent.direct_calls == []

    @pytest.mark.asyncio
    async def test_turn_failure_is_recorded_as_error(self):
        agent, cron = _Agent(response=RuntimeError("boom")), _Recorder()
        with pytest.raises(RuntimeError):
            await run_cron_job(_job(), agent=agent, cron=cron)
        assert cron.last["status"] == "error"
        assert "boom" in cron.last["error"]

    @pytest.mark.asyncio
    async def test_run_record_keeps_no_provider_secrets(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(model="fast"), agent=agent, cron=cron)
        blob = repr(cron.records)
        assert "api_key" not in blob
        assert "Authorization" not in blob


class TestBoundStillUsesTheSessionTurnPath:
    @pytest.mark.asyncio
    async def test_bound_job_goes_through_submit_cron_turn(self):
        agent, cron = _Agent(), _Recorder()
        job = _job(**_BOUND)
        await run_cron_job(job, agent=agent, cron=cron)
        assert len(agent.bound_calls) == 1
        assert agent.direct_calls == []

    @pytest.mark.asyncio
    async def test_bound_job_keeps_its_own_session(self):
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(**_BOUND), agent=agent, cron=cron)
        assert agent.bound_calls[0].session_key_override == "discord:123"

    @pytest.mark.asyncio
    async def test_bound_job_carries_the_cron_trigger_metadata(self):
        from nanobot.cron.session_turns import CRON_DEFER_UNTIL_IDLE_META, CRON_TRIGGER_META
        agent, cron = _Agent(), _Recorder()
        await run_cron_job(_job(**_BOUND), agent=agent, cron=cron)
        metadata = agent.bound_calls[0].metadata
        assert CRON_TRIGGER_META in metadata
        assert metadata[CRON_DEFER_UNTIL_IDLE_META] is True

    @pytest.mark.asyncio
    async def test_bound_job_with_model_is_rejected_instead_of_ignored(self):
        agent, cron = _Agent(), _Recorder()

        with pytest.raises(ValueError, match="bound cron job cannot specify a model"):
            await run_cron_job(
                _job(**_BOUND, model="fast"), agent=agent, cron=cron
            )

        assert agent.bound_calls == []
        assert agent.direct_calls == []


class TestIncompleteBindingIsRejected:
    """Binding fields are atomic and must never degrade to an unbound run."""

    @pytest.mark.asyncio
    async def test_session_key_without_origin_is_rejected(self):
        agent, cron = _Agent(), _Recorder()

        with pytest.raises(ValueError, match="incomplete cron session binding"):
            await run_cron_job(_job(session_key="discord:123"), agent=agent, cron=cron)

        assert agent.bound_calls == []
        assert agent.direct_calls == []

    @pytest.mark.asyncio
    async def test_partial_origin_is_rejected(self):
        agent, cron = _Agent(), _Recorder()
        job = _job(session_key="discord:123", origin_channel="discord")

        with pytest.raises(ValueError, match="incomplete cron session binding"):
            await run_cron_job(job, agent=agent, cron=cron)

        assert agent.bound_calls == []
        assert agent.direct_calls == []

    @pytest.mark.asyncio
    async def test_proactive_delivery_payload_runs_unbound(self):
        """deliver/channel/to means the job pushes a message somewhere; it does
        not continue a conversation, so it must not hijack a bound session."""
        agent, cron = _Agent(), _Recorder()
        job = _job(**_BOUND, deliver=True, channel="discord", to="chat-9")
        await run_cron_job(job, agent=agent, cron=cron)
        assert agent.bound_calls == []
        assert agent.direct_calls[0]["session_key"].startswith("cron:job-1:")


class TestUnboundRunsSweepTheirOwnLitter:
    @pytest.mark.asyncio
    async def test_each_unbound_run_asks_for_a_sweep(self) -> None:
        agent = _Agent()

        await run_cron_job(_job(), agent=agent, cron=_Recorder())

        assert agent.sessions.prune_calls == 1

    @pytest.mark.asyncio
    async def test_bound_runs_do_not_create_run_sessions(self) -> None:
        agent = _Agent()

        await run_cron_job(_job(**_BOUND), agent=agent, cron=_Recorder())

        assert agent.sessions.prune_calls == 0
