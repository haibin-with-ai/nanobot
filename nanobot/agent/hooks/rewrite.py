"""Command rewrite hook using external rtk binary."""

from __future__ import annotations

import asyncio
import os
import traceback
from contextlib import suppress
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


class CommandRewriteHook(AgentHook):
    """Rewrite exec tool commands via an external rtk binary before execution."""

    def __init__(
        self,
        enabled: bool = False,
        verbose: bool = False,
        timeout: float = 5.0,
        binary_path: str = "rtk",
        path_append: str = "",
    ) -> None:
        self._enabled = enabled
        self._verbose = verbose
        self._timeout = timeout
        self._binary_path = binary_path
        self._path_append = path_append

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if not self._enabled:
            return
        for tc in context.tool_calls:
            if tc.name != "exec":
                continue
            command = tc.arguments.get("command")
            if not isinstance(command, str) or not command:
                continue
            env: dict[str, str] | None = None
            if self._path_append:
                env = os.environ.copy()
                env["PATH"] = f"{env.get('PATH', '')}{os.pathsep}{self._path_append}"
            try:
                rewritten = await self._rewrite(command, env)
            except Exception:
                logger.debug(
                    "CommandRewriteHook: unexpected error for tool {}: {}",
                    tc.name,
                    traceback.format_exc(),
                )
                continue
            if rewritten is not None:
                if self._verbose:
                    logger.debug("rtk rewrite: {} → {}", command, rewritten)
                tc.arguments["command"] = rewritten

    async def _rewrite(
        self,
        command: str,
        env: dict[str, str] | None,
    ) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            self._binary_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(command.encode()),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            self._ensure_killed(proc)
            return None
        if proc.returncode in (0, 3):
            return stdout.decode().rstrip("\n\r")
        return None

    @staticmethod
    def _ensure_killed(proc: asyncio.subprocess.Process) -> None:
        with suppress(Exception):
            proc.kill()
