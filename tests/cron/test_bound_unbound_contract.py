"""One contract, two execution modes.

Both cron paths end in the same promises: exactly one turn, on a session whose
key has a known shape, running on a known model, with a run record that says
what happened, and with disk and cache agreeing afterwards. These tests state
those promises once and run them against bound and unbound alike, on a real
``SessionManager`` (see ``contract_harness``), so a promise that only one path
keeps shows up as a failure instead of as a missing test.
"""

from __future__ import annotations

import pytest

from nanobot.cron.bound_runner import DEFAULT_CRON_MODEL_PRESET, run_cron_job
from nanobot.session.manager import (
    SessionManager,
    make_cron_run_session_key,
    parse_cron_run_session_key,
)
from tests.cron.contract_harness import (
    BOUND_BINDING,
    ContractAgent,
    RunRecorder,
    cached_keys,
    make_job,
    session_files,
)

DAY_MS = 86_400_000
BOUND_KEY = BOUND_BINDING["session_key"]

MODES = ["unbound", "bound"]


@pytest.fixture
def manager(tmp_path) -> SessionManager:
    return SessionManager(tmp_path)


@pytest.fixture
def agent(manager) -> ContractAgent:
    return ContractAgent(manager)


def _job_for(mode: str, **kw):
    return make_job(bound=(mode == "bound"), **kw)


async def _run(mode: str, agent: ContractAgent, cron: RunRecorder, **kw):
    return await run_cron_job(_job_for(mode, **kw), agent=agent, cron=cron)


class TestExactlyOneTurn:
    @pytest.mark.parametrize("mode", MODES)
    async def test_one_turn_on_the_mode_specific_path(self, mode, agent):
        await _run(mode, agent, RunRecorder())

        assert len(agent.turns) == 1
        assert agent.turns[0].mode == ("session" if mode == "bound" else "direct")


class TestSessionKeyShape:
    async def test_unbound_key_is_a_parseable_run_key(self, agent):
        await _run("unbound", agent, RunRecorder())

        key = agent.turns[0].session_key
        parsed = parse_cron_run_session_key(key)
        assert parsed is not None
        assert parsed[0] == "job-1"
        assert key.count("job-1") == 1

    async def test_bound_key_is_the_bound_session_and_not_a_run_key(self, agent):
        await _run("bound", agent, RunRecorder())

        key = agent.turns[0].session_key
        assert key == BOUND_KEY
        assert parse_cron_run_session_key(key) is None

    @pytest.mark.parametrize("mode", MODES)
    async def test_the_turn_session_is_the_one_persisted_on_disk(self, mode, agent, manager):
        await _run(mode, agent, RunRecorder())

        assert agent.turns[0].session_key in session_files(manager)

    async def test_two_unbound_runs_do_not_share_a_session(self, agent):
        cron = RunRecorder()
        await _run("unbound", agent, cron)
        await _run("unbound", agent, cron)

        assert len({turn.session_key for turn in agent.turns}) == 2

    async def test_two_bound_runs_stay_in_the_one_session(self, agent, manager):
        cron = RunRecorder()
        await _run("bound", agent, cron)
        await _run("bound", agent, cron)

        assert {turn.session_key for turn in agent.turns} == {BOUND_KEY}
        assert len(manager.get_or_create(BOUND_KEY).messages) == 4


class TestModelLanding:
    """Where the model of a run comes from, per mode."""

    async def test_unbound_default_runs_on_deep(self, agent):
        await _run("unbound", agent, RunRecorder())

        assert agent.turns[0].model == DEFAULT_CRON_MODEL_PRESET == "deep"

    async def test_unbound_explicit_model_reaches_the_turn(self, agent):
        await _run("unbound", agent, RunRecorder(), model="fast")

        assert agent.turns[0].model == "fast"

    async def test_unbound_model_is_persisted_on_the_run_session(self, agent, manager):
        await _run("unbound", agent, RunRecorder(), model="fast")

        key = agent.turns[0].session_key
        manager.invalidate(key)  # force a reload from disk
        assert agent.model_of_session(key) == "fast"

    async def test_unbound_unknown_model_never_reaches_a_turn(self, agent):
        cron = RunRecorder()

        with pytest.raises(KeyError):
            await _run("unbound", agent, cron, model="nonexistent")

        assert agent.turns == []
        assert cron.last["status"] == "error"

    async def test_bound_job_may_not_name_a_model(self, agent):
        cron = RunRecorder()

        with pytest.raises(ValueError, match="bound cron job cannot specify a model"):
            await _run("bound", agent, cron, model="fast")

        assert agent.turns == []

    async def test_bound_run_inherits_the_session_preset_and_does_not_rewrite_it(
        self, agent, manager
    ):
        agent.set_session_model_preset(BOUND_KEY, "fast")

        await _run("bound", agent, RunRecorder())

        assert agent.turns[0].model == "fast"
        manager.invalidate(BOUND_KEY)
        assert agent.model_of_session(BOUND_KEY) == "fast"


class TestRunRecord:
    @pytest.mark.parametrize("mode", MODES)
    async def test_record_goes_queued_then_ok_under_one_run_id(self, mode, agent):
        cron = RunRecorder()

        await _run(mode, agent, cron)

        assert cron.statuses() == ["queued", "ok"]
        assert len(set(cron.run_ids)) == 1

    @pytest.mark.parametrize("mode", MODES)
    async def test_record_names_the_session_the_turn_ran_in(self, mode, agent):
        cron = RunRecorder()

        await _run(mode, agent, cron)

        assert cron.last["session_key"] == agent.turns[0].session_key

    @pytest.mark.parametrize("mode", MODES)
    async def test_record_carries_job_identity_and_the_response(self, mode, agent):
        cron = RunRecorder()

        await _run(mode, agent, cron)

        assert cron.last["job_id"] == "job-1"
        assert cron.last["job_name"] == "nightly"
        assert cron.last["response"] == "done"

    @pytest.mark.parametrize("mode", MODES)
    async def test_record_keeps_no_provider_secrets(self, mode, agent):
        cron = RunRecorder()

        await _run(mode, agent, cron)

        blob = repr(cron.records)
        assert "api_key" not in blob
        assert "Authorization" not in blob

    @pytest.mark.parametrize(
        "mode",
        [
            "unbound",
            pytest.param(
                "bound",
                marks=pytest.mark.xfail(
                    strict=True,
                    reason=(
                        "已知不对称：unbound 的 model 由任务自带，写得进 run record；"
                        "bound 跑在用户会话里，模型由会话 preset 决定，runner 手上没有它，"
                        "BoundCronAgent 协议也没有读取接口。要补必须先扩协议，本轮不做。"
                        "哪天补上了这条会 XPASS 并报错，提醒把 xfail 摘掉。"
                    ),
                ),
            ),
        ],
    )
    async def test_record_states_the_model_the_turn_ran_on(self, mode, agent):
        cron = RunRecorder()

        await _run(mode, agent, cron)

        assert cron.last.get("model") == agent.turns[0].model


class TestFailureStatus:
    @pytest.mark.parametrize("mode", MODES)
    async def test_turn_failure_lands_on_error_and_propagates(self, mode, manager):
        agent = ContractAgent(manager, failure=RuntimeError("boom"))
        cron = RunRecorder()

        with pytest.raises(RuntimeError):
            await _run(mode, agent, cron)

        assert cron.last["status"] == "error"
        assert "boom" in cron.last["error"]
        assert len(agent.turns) == 1

    @pytest.mark.parametrize("mode", MODES)
    async def test_a_failed_run_never_reports_ok(self, mode, manager):
        agent = ContractAgent(manager, failure=RuntimeError("boom"))
        cron = RunRecorder()

        with pytest.raises(RuntimeError):
            await _run(mode, agent, cron)

        assert "ok" not in cron.statuses()

    @pytest.mark.parametrize("mode", MODES)
    async def test_a_failed_turn_writes_no_conversation(self, mode, manager):
        agent = ContractAgent(manager, failure=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await _run(mode, agent, RunRecorder())

        key = agent.turns[0].session_key
        assert manager.get_or_create(key).messages == []


class TestIncompleteBindingIsRejectedInBothDirections:
    @pytest.mark.parametrize(
        "payload",
        [
            {"session_key": BOUND_KEY},
            {"session_key": BOUND_KEY, "origin_channel": "discord"},
            {"origin_channel": "discord", "origin_chat_id": "chat-9"},
        ],
    )
    async def test_partial_binding_never_degrades_into_a_run(self, payload, agent):
        job = make_job(**payload)

        with pytest.raises(ValueError, match="incomplete cron session binding"):
            await run_cron_job(job, agent=agent, cron=RunRecorder())

        assert agent.turns == []


class TestPruneKeepsDiskAndCacheConsistent:
    @pytest.mark.parametrize("mode", MODES)
    async def test_prune_is_swept_only_by_the_path_that_creates_run_sessions(
        self, mode, agent, manager
    ):
        await _run(mode, agent, RunRecorder())

        run_keys = {
            key for key in session_files(manager) if parse_cron_run_session_key(key)
        }
        assert bool(run_keys) is (mode == "unbound")

    async def test_expired_run_session_leaves_neither_file_nor_cache_entry(
        self, agent, manager
    ):
        await _run("unbound", agent, RunRecorder())
        key = agent.turns[0].session_key
        assert key in session_files(manager)
        assert key in cached_keys(manager)

        report = manager.prune_cron_run_sessions(
            retain_days=0, keep_per_job=0, now_ms=2_000_000_000_000
        )

        assert report["count"] == 1
        assert key not in session_files(manager)
        assert key not in cached_keys(manager)

    async def test_a_pruned_session_comes_back_empty_not_from_cache(self, agent, manager):
        await _run("unbound", agent, RunRecorder())
        key = agent.turns[0].session_key

        manager.prune_cron_run_sessions(
            retain_days=0, keep_per_job=0, now_ms=2_000_000_000_000
        )

        assert manager.get_or_create(key).messages == []

    async def test_prune_never_touches_the_bound_session(self, agent, manager):
        await _run("bound", agent, RunRecorder())
        stale = make_cron_run_session_key("job-1", "1700000000000:abcd1234")
        stale_session = manager.get_or_create(stale)
        stale_session.add_message("user", "old")
        manager.save(stale_session)

        manager.prune_cron_run_sessions(
            retain_days=0, keep_per_job=0, now_ms=2_000_000_000_000
        )

        assert BOUND_KEY in session_files(manager)
        assert len(manager.get_or_create(BOUND_KEY).messages) == 2
        assert stale not in session_files(manager)
