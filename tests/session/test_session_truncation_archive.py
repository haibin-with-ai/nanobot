"""Truncation archiving: any shrink of a persisted session snapshots the old file.

Contract: before SessionManager.save() overwrites an existing session file with
fewer messages than are already on disk (clear / compaction / pruning / file cap),
the pre-truncation file is copied verbatim into sessions/archive/<stem>-<n>.jsonl
with a monotonically increasing index. A normal append never archives.
"""

from __future__ import annotations

from pathlib import Path

from nanobot.session.manager import SessionManager


def _msg(i: int) -> dict:
    return {"role": "user", "content": f"m{i}"}


def _archive_dir(mgr: SessionManager) -> Path:
    return mgr.sessions_dir / "archive"


def _stem(mgr: SessionManager, key: str) -> str:
    return mgr._storage_key(key)


def _archives(mgr: SessionManager, key: str) -> list[Path]:
    stem = _stem(mgr, key)
    return sorted(_archive_dir(mgr).glob(f"{stem}-*.jsonl"))


def test_clear_then_save_archives_old_file(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    key = "discord:123"
    session = mgr.get_or_create(key)
    session.messages.extend([_msg(1), _msg(2), _msg(3)])
    mgr.save(session)

    live_path = mgr._get_session_path(key)
    original_bytes = live_path.read_bytes()

    session.clear()
    mgr.save(session)

    archives = _archives(mgr, key)
    assert len(archives) == 1
    assert archives[0].name == f"{_stem(mgr, key)}-1.jsonl"
    # Verbatim copy of the pre-truncation file.
    assert archives[0].read_bytes() == original_bytes


def test_normal_append_does_not_archive(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    key = "discord:append"
    session = mgr.get_or_create(key)
    session.messages.append(_msg(1))
    mgr.save(session)
    session.messages.append(_msg(2))
    mgr.save(session)

    assert _archives(mgr, key) == []


def test_shrink_via_compaction_archives_and_increments(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    key = "discord:compact"
    session = mgr.get_or_create(key)
    session.messages.extend([_msg(i) for i in range(5)])
    mgr.save(session)

    # First shrink (compaction replaces 5 messages with 1 summary).
    five_msg_bytes = mgr._get_session_path(key).read_bytes()
    session.messages[:] = [{"role": "assistant", "content": "summary"}]
    mgr.save(session)

    # Grow again, then shrink again.
    session.messages.extend([_msg(i) for i in range(3)])
    mgr.save(session)
    four_msg_bytes = mgr._get_session_path(key).read_bytes()
    session.messages[:] = session.messages[:1]
    mgr.save(session)

    archives = _archives(mgr, key)
    assert [p.name for p in archives] == [
        f"{_stem(mgr, key)}-1.jsonl",
        f"{_stem(mgr, key)}-2.jsonl",
    ]
    assert archives[0].read_bytes() == five_msg_bytes
    assert archives[1].read_bytes() == four_msg_bytes


def test_first_save_of_new_session_never_archives(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    key = "discord:fresh"
    session = mgr.get_or_create(key)  # empty
    mgr.save(session)
    assert _archives(mgr, key) == []


def test_archive_dir_is_invisible_to_session_listing(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    key = "discord:hidden"
    session = mgr.get_or_create(key)
    session.messages.extend([_msg(1), _msg(2)])
    mgr.save(session)
    session.clear()
    mgr.save(session)

    # Top-level jsonl glob (used by loading/GC) must not descend into archive/.
    top_level = {p.name for p in mgr.sessions_dir.glob("*.jsonl")}
    assert all(not name.endswith("-1.jsonl") for name in top_level)
    assert _archives(mgr, key)  # but the archive itself exists
