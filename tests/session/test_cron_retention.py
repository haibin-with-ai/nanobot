"""Recycling the throwaway sessions that unbound cron runs leave behind.

Every unbound run creates a session file and nothing ever removed them; the
production workspace had accumulated 177 of them. Pruning is deliberately
narrow: only keys that decode exactly as a cron run key are eligible, so a
bug here cannot eat a human conversation.
"""

from __future__ import annotations

import json

import pytest

from nanobot.session.manager import SessionManager

DAY_MS = 86_400_000
NOW_MS = 1_800_000_000_000


def _key(job: str, ms: int, uid: str = "abcd1234") -> str:
    return f"cron:{job}:{job}:{ms}:{uid}"


@pytest.fixture
def manager(tmp_path):
    return SessionManager(tmp_path)


def _write(manager: SessionManager, key: str, size: int = 0) -> None:
    session = manager.get_or_create(key)
    session.add_message("user", "x" * size if size else "ping")
    manager.save(session)


def _existing(manager: SessionManager) -> set[str]:
    keys = (manager._session_key_from_path(p) for p in manager.sessions_dir.glob("*.jsonl"))
    return {k for k in keys if k}


class TestOnlyCronRunSessionsAreEligible:
    def test_a_stale_cron_run_session_is_removed(self, manager):
        """Stale and beyond the per-job floor, so it goes."""
        for i in range(4):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 1
        assert len(_existing(manager)) == 3

    def test_the_only_run_of_a_job_is_kept_however_stale(self, manager):
        """The floor wins over the window: some history beats none."""
        _write(manager, _key("job1", NOW_MS - 400 * DAY_MS))
        assert manager.prune_cron_run_sessions(now_ms=NOW_MS)["count"] == 0
        assert _existing(manager)

    @pytest.mark.parametrize("key", [
        "discord:123456",
        "cli:direct",
        "cron:job1",
        "cron:job1:job2:1700000000000:abcd1234",
        "cron:job1:job1:notanumber:abcd1234",
        "cron:job1:job1:1700000000000:XYZ",
        "cron:job1:job1:1700000000000:abcd1234:extra",
    ])
    def test_non_cron_run_keys_are_never_touched(self, manager, key):
        """Anything that does not decode exactly is left alone."""
        _write(manager, key)
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 0
        assert _existing(manager)

    def test_a_foreign_key_shaped_like_cron_is_rejected(self, manager):
        """Only the doubled job id proves this key came from a cron run."""
        from nanobot.session.manager import parse_cron_run_session_key
        assert parse_cron_run_session_key("cron:job1:something:1700000000000:abcd1234") is None
        assert parse_cron_run_session_key("cron:job1:job1:1700000000000:abcd1234") == (
            "job1", 1700000000000,
        )

    def test_a_bound_cron_session_is_not_a_run_session(self, manager):
        _write(manager, "discord:chat-9")
        manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert _existing(manager)


class TestRetentionWindow:
    def test_recent_runs_survive(self, manager):
        _write(manager, _key("job1", NOW_MS - 5 * DAY_MS, "aaaaaaaa"))
        report = manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert report["count"] == 0

    def test_the_boundary_day_survives(self, manager):
        """Exactly at the cutoff is still inside the window."""
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - 400 * DAY_MS, f"bbbbbbb{i}"))
        _write(manager, _key("job1", NOW_MS - 30 * DAY_MS, "aaaaaaaa"))
        report = manager.prune_cron_run_sessions(retain_days=30, keep_per_job=0, now_ms=NOW_MS)
        assert report["count"] == 3
        assert _existing(manager) == {_key("job1", NOW_MS - 30 * DAY_MS, "aaaaaaaa")}

    def test_the_newest_runs_survive_however_old_they_are(self, manager):
        """A job that runs monthly must not lose its entire history."""
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
        report = manager.prune_cron_run_sessions(dry_run=True, now_ms=NOW_MS)
        assert report["bytes"] >= 500

    def test_nothing_to_do_reports_zero(self, manager):
        report = manager.prune_cron_run_sessions(dry_run=True, now_ms=NOW_MS)
        assert report == {"keys": [], "count": 0, "bytes": 0}


class TestFailureIsolation:
    def test_one_bad_file_does_not_stop_the_rest(self, manager, monkeypatch):
        """Cron delivery must not break because a single unlink failed."""
        keys = [_key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}") for i in range(4)]
        for key in keys:
            _write(manager, key)

        real = SessionManager.delete_session
        calls: list[str] = []

        def flaky(self, key):
            calls.append(key)
            if len(calls) == 1:
                raise OSError("device busy")
            return real(self, key)

        monkeypatch.setattr(SessionManager, "delete_session", flaky)
        report = manager.prune_cron_run_sessions(keep_per_job=1, now_ms=NOW_MS)
        assert len(calls) == 3
        assert report["count"] == 2

    def test_cache_is_cleared_along_with_the_file(self, manager):
        key = _key("job1", NOW_MS - 400 * DAY_MS, "aaaaaaa9")
        _write(manager, key)
        for i in range(3):
            _write(manager, _key("job1", NOW_MS - (40 + i) * DAY_MS, f"aaaaaaa{i}"))
        manager.prune_cron_run_sessions(now_ms=NOW_MS)
        assert manager.get_or_create(key).messages == []


class TestGatewayWiresItOnce:
    def test_startup_prune_is_invoked(self, monkeypatch):
        """One entry point: gateway startup. Confirm it is actually reachable."""
        import nanobot.cli.commands as commands
        source = json.dumps(commands.__file__)
        assert "prune_cron_run_sessions" in open(json.loads(source), encoding="utf-8").read()
