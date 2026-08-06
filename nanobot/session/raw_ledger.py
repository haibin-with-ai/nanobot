"""真人原始消息的只增账本。

Session 是可清空的模型工作上下文；这份账本是真人入口消息的权威副本。
`/new`、会话压缩、上下文裁剪和 Dream 整理都不触碰它。
只存入口原始数据（正文、附件、时间、路由、消息 ID、回复关系），
不存运行时上下文和自动化改写等系统加工物。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from nanobot.bus.events import InboundMessage
from nanobot.session.automation_turns import automation_history_overrides
from nanobot.session.turn_continuation import internal_continuation_inbound


class RawMessageLedger:
    """Append-only JSONL ledger for raw human channel messages."""

    def __init__(self, workspace: Path):
        self.path = Path(workspace) / "messages" / "raw-user-messages.jsonl"

    def should_record(self, msg: InboundMessage) -> bool:
        """True only for a human channel-entry message not yet recorded."""
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        if msg._raw_ledger_recorded:
            return False
        if msg.channel in {"system", "cron"} or msg.sender_id in {"subagent", "cron"}:
            return False
        if internal_continuation_inbound(metadata):
            return False
        _, automation_extra = automation_history_overrides(metadata)
        return not automation_extra

    @staticmethod
    def _record(msg: InboundMessage) -> dict[str, object]:
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        return {
            "ts": msg.timestamp.isoformat(),
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender_id,
            "content": msg.content,
            "media": list(msg.media or []),
            "message_id": metadata.get("message_id") or metadata.get("interaction_id"),
            "reply_to": metadata.get("reply_to"),
        }

    @staticmethod
    def _sync_directory(path: Path) -> None:
        directory_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _write_or_rollback(fd: int, data: bytes) -> None:
        """Write all bytes and fsync, or kernel-truncate back to the start offset."""
        offset = os.lseek(fd, 0, os.SEEK_END)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, offset)
            raise

    def append(self, msg: InboundMessage) -> None:
        """Durably append one message; raises without leaving a partial record."""
        data = (json.dumps(self._record(msg), ensure_ascii=False) + "\n").encode("utf-8")
        parent_created = not self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            self._write_or_rollback(fd, data)
        finally:
            os.close(fd)
        if created:
            self._sync_directory(self.path.parent)
        if parent_created:
            self._sync_directory(self.path.parent.parent)
        msg._raw_ledger_recorded = True
