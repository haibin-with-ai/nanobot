"""真人原始消息账本：只增不删，独立于 Session 生命周期。"""

from __future__ import annotations

import json
from datetime import datetime

from nanobot.bus.events import InboundMessage
from nanobot.session.raw_ledger import RawMessageLedger


def _msg(**overrides) -> InboundMessage:
    fields = dict(
        channel="discord",
        sender_id="1087972814725853196",
        chat_id="1486282968648519720",
        content="hello",
        metadata={"message_id": "m1", "reply_to": "m0"},
    )
    fields.update(overrides)
    return InboundMessage(**fields)


def _records(ledger: RawMessageLedger) -> list[dict]:
    text = ledger.path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestAppend:
    def test_append_writes_entry_fields(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        ts = datetime(2026, 8, 6, 11, 0, 0)
        ledger.append(_msg(timestamp=ts, media=["/tmp/a.png"]))

        records = _records(ledger)
        assert len(records) == 1
        record = records[0]
        assert record["ts"] == ts.isoformat()
        assert record["channel"] == "discord"
        assert record["chat_id"] == "1486282968648519720"
        assert record["sender_id"] == "1087972814725853196"
        assert record["content"] == "hello"
        assert record["media"] == ["/tmp/a.png"]
        assert record["message_id"] == "m1"
        assert record["reply_to"] == "m0"

    def test_missing_channel_ids_keep_stable_shape(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(metadata={}))

        record = _records(ledger)[0]
        assert record["message_id"] is None
        assert record["reply_to"] is None

    def test_slash_interaction_id_is_used_as_message_id(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(metadata={"interaction_id": "i1"}))

        assert _records(ledger)[0]["message_id"] == "i1"

    def test_identical_messages_are_both_kept(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(content="same"))
        ledger.append(_msg(content="same"))

        assert [r["content"] for r in _records(ledger)] == ["same", "same"]

    def test_media_only_message_is_recorded(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(content="", media=["/tmp/photo.jpg"]))

        record = _records(ledger)[0]
        assert record["content"] == ""
        assert record["media"] == ["/tmp/photo.jpg"]

    def test_append_marks_message_as_recorded(self, tmp_path):
        ledger = RawMessageLedger(tmp_path)
        msg = _msg()
        ledger.append(msg)

        assert msg._raw_ledger_recorded is True
        assert ledger.should_record(msg) is False


class TestShouldRecord:
    def test_human_message_is_recorded(self, tmp_path):
        assert RawMessageLedger(tmp_path).should_record(_msg()) is True

    def test_command_message_is_recorded(self, tmp_path):
        assert RawMessageLedger(tmp_path).should_record(_msg(content="/new")) is True

    def test_system_channel_is_excluded(self, tmp_path):
        assert RawMessageLedger(tmp_path).should_record(_msg(channel="system")) is False

    def test_subagent_sender_is_excluded(self, tmp_path):
        assert RawMessageLedger(tmp_path).should_record(_msg(sender_id="subagent")) is False

    def test_unbound_cron_sender_is_excluded(self, tmp_path):
        msg = _msg(channel="cron", sender_id="cron")
        assert RawMessageLedger(tmp_path).should_record(msg) is False

    def test_cron_automation_turn_is_excluded(self, tmp_path):
        from nanobot.cron.session_turns import CRON_TRIGGER_META

        msg = _msg(metadata={CRON_TRIGGER_META: {"job_id": "j1", "message": "run"}})
        assert RawMessageLedger(tmp_path).should_record(msg) is False

    def test_local_trigger_turn_is_excluded(self, tmp_path):
        from nanobot.triggers.local_session_turns import LOCAL_TRIGGER_META

        msg = _msg(metadata={LOCAL_TRIGGER_META: {"trigger_id": "t1"}})
        assert RawMessageLedger(tmp_path).should_record(msg) is False

    def test_internal_continuation_is_excluded(self, tmp_path):
        from nanobot.session.turn_continuation import INTERNAL_CONTINUATION_META

        msg = _msg(metadata={INTERNAL_CONTINUATION_META: True})
        assert RawMessageLedger(tmp_path).should_record(msg) is False


class TestRecordShapeIsRawInput:
    def test_record_has_no_runtime_context_or_metadata_dump(self, tmp_path):
        """账本只存入口原始数据，不存系统 metadata 加工物。"""
        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(metadata={
            "message_id": "m1",
            "reply_to": None,
            "_wants_stream": True,
            "guild_id": "g1",
        }))

        record = _records(ledger)[0]
        assert set(record) == {
            "ts", "channel", "chat_id", "sender_id",
            "content", "media", "message_id", "reply_to",
        }


class TestFailureRecovery:
    def test_unencodable_content_fails_without_touching_file(self, tmp_path):
        """序列化/编码失败也是写入失败：必须报错且不留半条记录。"""
        import pytest

        ledger = RawMessageLedger(tmp_path)
        msg = _msg(content="bad \ud800 surrogate")

        with pytest.raises(UnicodeEncodeError):
            ledger.append(msg)

        assert not ledger.path.exists() or ledger.path.read_bytes() == b""
        assert msg._raw_ledger_recorded is False

    def test_failed_write_rolls_back_partial_bytes(self, tmp_path):
        """落盘失败后文件回退到写前状态，重试不会毒化 JSONL。"""
        from unittest.mock import patch

        import pytest

        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(content="first"))

        with patch("nanobot.session.raw_ledger.os.fsync", side_effect=OSError("io")):
            with pytest.raises(OSError):
                ledger.append(_msg(content="second"))

        assert [r["content"] for r in _records(ledger)] == ["first"]

        ledger.append(_msg(content="retry"))
        assert [r["content"] for r in _records(ledger)] == ["first", "retry"]

    def test_first_append_fsyncs_parent_directory(self, tmp_path):
        """新建账本时同步目录项，否则断电可能丢掉首批记录。"""
        from unittest.mock import patch

        ledger = RawMessageLedger(tmp_path)
        with patch("nanobot.session.raw_ledger.os.fsync") as mock_fsync:
            ledger.append(_msg(content="first"))
            assert mock_fsync.call_count == 3  # file + messages dir + workspace dir

            mock_fsync.reset_mock()
            ledger.append(_msg(content="second"))
            assert mock_fsync.call_count == 1  # file only

    def test_partial_os_write_rolls_back_written_bytes(self, tmp_path):
        """磁盘满时部分字节已落盘：必须回滚，不留半条毒化后续记录。"""
        from unittest.mock import patch

        import pytest

        ledger = RawMessageLedger(tmp_path)
        ledger.append(_msg(content="first"))

        real_write = __import__("os").write
        state = {"called": False}

        def flaky_write(fd, data):
            if not state["called"]:
                state["called"] = True
                return real_write(fd, data[:5])  # 部分写入
            raise OSError("disk full")

        with patch("nanobot.session.raw_ledger.os.write", side_effect=flaky_write):
            with pytest.raises(OSError):
                ledger.append(_msg(content="second"))

        assert [r["content"] for r in _records(ledger)] == ["first"]

        ledger.append(_msg(content="retry"))
        assert [r["content"] for r in _records(ledger)] == ["first", "retry"]
