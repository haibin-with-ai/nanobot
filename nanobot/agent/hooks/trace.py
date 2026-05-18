"""TraceHook: lightweight JSONL trace logging for agent iterations."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from nanobot.agent.hook import AgentHook, AgentHookContext


class TraceHook(AgentHook):
    """Append one JSONL line per iteration for offline analysis."""

    def __init__(self, trace_file: Path) -> None:
        super().__init__()
        self._trace_file = trace_file

    async def after_iteration(self, context: AgentHookContext) -> None:
        entry = {
            "v": 1,
            "timestamp": time.time(),
            "model": context.model,
            "iteration": context.iteration,
            "finish_reason": context.stop_reason,
            "usage": dict(context.usage),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        self._trace_file.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._append_sync, line)

    def _append_sync(self, line: str) -> None:
        with self._trace_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
