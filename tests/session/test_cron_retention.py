"""Retention for the throwaway sessions created by unbound cron runs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nanobot.cron.bound_runner import run_cron_job
from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from nanobot.session.manager import SessionManager

DAY_MS = 86_400_000
NOW_MS = 1_800_000_000_000


def _key(job: str, ms: int, uid: str = "abcd1234") -> str:
    return f"cron:{job}:{ms}:{uid}"


@pytest.fixture
def manager(tmp_path):
    return SessionManager(tmp_path)


def _write(manager: SessionManager, key: str, size: int = 0) -> None:
    session = manager.get_or_create(key)
    session.add_message("user", "x" * size if size else "ping")
    manager.save(session)


def _existing(manager: SessionManager) -> set[str]:
    keys = (manager._session_key_from_path(p) for p in manager.sessions_dir.glob("*.jsonl"))
    return {key for key in keys if key}


class TestOnlyCronRunSessionsAreEligible:
    def test_a_stale_cron_run_session_is_removed(self, manager):
        for i in range(4):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 1
        assert len(_existing(manager)) == 3

    def test_the_only_run_of_a_job_is_kept_however_stale(self, manager):
        _write(manager, _key("job1", NOW_MS - 400 * DAY_MS))
        assert manager.prune_cron_run_sessions(now_ms=NOW_MS)["count"] == 0
        assert _existing(manager)

    @pytest.mark.parametrize(
        "key",
        [
            "discord:123456",
            "cli:direct",
            "cron:job1",
            "cron:job1:job2:1700000000000:abcd1234",
            "cron:job1:notanumber:abcd1234",
            "cron:job1:1700000000000:XYZ",
            "cron:job1:1700000000000:abcd1234:extra",
        ],
    )
    def test_non_cron_run_keys_are_never_touched(self, manager, key):
        _write(manager, key)
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 0
        assert _existing(manager)

    def test_key_codec_has_one_copy_of_the_job_id(self):
        from nanobot.session.manager import make_cron_run_session_key
        key = make_cron_run_session_key("job1", "1700000000000:abcd1234")
        assert key == "cron:job1:1700000000000:abcd1234"

    def test_key_codec_round_trips(self):
        from nanobot.session.manager import parse_cron_run_session_key
        assert parse_cron_run_session_key(_key("job1", 1700000000000)) == (
            "job1",
            1700000000000,
        )

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("cron:073339b1:1785371400012", ("073339b1", 1785371400012)),
            (
                "cron:74737c87-a618-4017-9290-41672bdb5fc4:1784075400005",
                ("74737c87-a618-4017-9290-41672bdb5fc4", 1784075400005),
            ),
        ],
    )
    def test_run_keys_written_before_the_random_suffix_still_decode(self, key, expected):
        """线上已有的 per-run 会话是 cron:{job}:{ms}，没有随机后缀，也得认。"""
        from nanobot.session.manager import parse_cron_run_session_key
        assert parse_cron_run_session_key(key) == expected

    def test_run_sessions_written_before_the_random_suffix_are_pruned(self, manager):
        for i in range(4):
            _write(manager, f"cron:073339b1:{NOW_MS - (40 + i) * DAY_MS}")
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 1
        assert len(_existing(manager)) == 3

    def test_a_bound_cron_session_is_not_a_run_session(self, manager):
        _write(manager, "discord:chat-9")
        manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert _existing(manager)


class TestRetentionWindow:
    def test_recent_runs_survive(self, manager):
        _write(manager, _key("job1", NOW_MS - 5 * DAY_MS, "aaaaaaaa"))
        assert manager.prune_cron_run_sessions(now_ms=NOW_MS)["count"] == 0

    def test_the_boundary_day_survives(self, manager):
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - 400 * DAY_MS, f"bbbbbbb{i}"))
        survivor = _key("job1", NOW_MS - 30 * DAY_MS, "aaaaaaaa")
        _write(manager, survivor)
        report = manager.prune_cron_run_sessions(
            retain_days=30, keep_per_job=0, now_ms=NOW_MS
        )
        assert report["count"] == 3
        assert _existing(manager) == {survivor}

    def test_the_newest_runs_survive_however_old_they_are(self, manager):
        for i in range(5):
            _write(manager, _key("job1", NOW_MS - (100 + i) * DAY_MS, f"aaaaaaa{i}"))
        report = manager.prune_cron_run_sessions(keep_per_job=3, now_ms=NOW_MS)
        assert report["count"] == 2
        assert len(_existing(manager)) == 3

    def test_the_survivors_are_the_newest_ones(self, manager):
        keys = [_key("job1", NOW_MS - (100 + i) * DAY_MS, f"aaaaaaa{i}") for i in range(5)]
        for key in keys:
            _write(manager, key)
        manager.prune_cron_run_sessions(keep_per_job=3, now_ms=NOW_MS)
        assert _existing(manager) == set(keys[:3])

    def test_each_job_keeps_its_own_quota(self, manager):
        for job in ("job1", "job2"):
            for i in range(4):
                _write(manager, _key(job, NOW_MS - (100 + i) * DAY_MS, f"aaaaaaa{i}"))
        report = manager.prune_cron_run_sessions(keep_per_job=3, now_ms=NOW_MS)
        assert report["count"] == 2
        assert len(_existing(manager)) == 6


class TestDryRun:
    def test_dry_run_reports_without_deleting(self, manager):
        key = _key("job1", NOW_MS - 400 * DAY_MS, "aaaaaaa9")
        _write(manager, key)
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        report = manager.prune_cron_run_sessions(dry_run=True, now_ms=NOW_MS)
        assert report["count"] == 1
        assert report["keys"] == [key]
        assert len(_existing(manager)) == 4

    def test_dry_run_reports_reclaimable_bytes(self, manager):
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        _write(manager, _key("job1", NOW_MS - 400 * DAY_MS, "aaaaaaa9"), size=500)
        assert manager.prune_cron_run_sessions(dry_run=True, now_ms=NOW_MS)["bytes"] >= 500

    def test_nothing_to_do_reports_zero(self, manager):
        assert manager.prune_cron_run_sessions(dry_run=True, now_ms=NOW_MS) == {
            "keys": [],
            "count": 0,
            "bytes": 0,
        }


class TestFailureIsolation:
    def test_one_bad_file_does_not_stop_the_rest(self, manager, monkeypatch):
        keys = [_key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}") for i in range(4)]
        for key in keys:
            _write(manager, key)
        real = SessionManager.delete_session
        calls: list[str] = []

        def flaky(self, key):
            calls.append(key)
            if len(calls) == 1:
                self.invalidate(key)
                return False
            return real(self, key)

        monkeypatch.setattr(SessionManager, "delete_session", flaky)
        report = manager.prune_cron_run_sessions(keep_per_job=1, now_ms=NOW_MS)
        assert len(calls) == 3
        assert report["count"] == 2
        assert len(_existing(manager)) == 2

    def test_failed_deletes_do_not_count_as_reclaimed_bytes(self, manager, monkeypatch):
        for i in range(2):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"), size=500)
        monkeypatch.setattr(SessionManager, "delete_session", lambda self, key: False)
        report = manager.prune_cron_run_sessions(keep_per_job=1, now_ms=NOW_MS)
        assert report["count"] == 0
        assert report["bytes"] == 0


class TestThrottledSweep:
    def test_instance_sweeps_at_most_once_per_day(self, tmp_path):
        clock = Mock(return_value=10.0)
        manager = SessionManager(tmp_path, retention_clock=clock)
        first = manager.prune_cron_run_sessions()
        second = manager.prune_cron_run_sessions()
        assert first == {"keys": [], "count": 0, "bytes": 0}
        assert second is None

    def test_the_daily_window_uses_the_injected_monotonic_clock(self, tmp_path):
        clock = Mock(side_effect=[10.0, 10.0 + 86_399, 10.0 + 86_400])
        manager = SessionManager(tmp_path, retention_clock=clock)
        assert manager.prune_cron_run_sessions() is not None
        assert manager.prune_cron_run_sessions() is None
        assert manager.prune_cron_run_sessions() is not None

    def test_explicit_now_bypasses_the_runtime_throttle(self, manager):
        assert manager.prune_cron_run_sessions(now_ms=NOW_MS) is not None
        assert manager.prune_cron_run_sessions(now_ms=NOW_MS) is not None

    def test_cache_is_cleared_along_with_the_file(self, manager):
        key = _key("job1", NOW_MS - 400 * DAY_MS, "aaaaaaa9")
        _write(manager, key)
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert manager.get_or_create(key).messages == []


class TestRealCronCallPointWiresRetention:
    @pytest.mark.asyncio
    async def test_unbound_run_invokes_the_throttled_prune_once(self):
        sessions = SimpleNamespace(prune_cron_run_sessions=Mock())
        agent = SimpleNamespace(
            sessions=sessions,
            tools=SimpleNamespace(get=lambda _name: None),
            set_session_model_preset=lambda *_args: None,
        )

        async def process_direct(*_args, **_kwargs):
            return SimpleNamespace(content="done")

        agent.process_direct = process_direct
        cron = SimpleNamespace(write_run_record=Mock())
        job = CronJob(
            id="job-1",
            name="nightly",
            payload=CronPayload(message="ping"),
            schedule=CronSchedule(kind="every", every_ms=60_000),
            state=CronJobState(),
        )

        await run_cron_job(job, agent=agent, cron=cron)

        sessions.prune_cron_run_sessions.assert_called_once_with()
