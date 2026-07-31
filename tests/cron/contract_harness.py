"""Shared harness for the bound/unbound cron contract tests.

Not a test module. It exists so both execution modes can be exercised against
one behaviour model instead of two hand-written sets of doubles that only
implement whatever the assertion of the day happens to touch.

What stays real:

* ``SessionManager`` — real persistence, real cache, real prune, real key codec.
  Session files, the preset metadata written by the preset path, and the cache
  eviction done by ``delete_session`` are all observed on the real object.
* ``nanobot.cron.bound_runner`` — the code under test, untouched.

What is replaced: only the outward I/O of the agent, i.e. the LLM call. The
stub mirrors ``AgentLoop.set_session_model_preset`` (resolve preset or raise
``KeyError``, persist it into session metadata, save) and resolves the model of
a turn the way the loop does, through ``model_preset_from_metadata``. Both
execution paths therefore record *which model the turn actually ran on*, which
is the property the previous fakes could not express.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nanobot.bus.events import InboundMessage
from nanobot.cron.session_turns import CRON_TRIGGER_META
from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from nanobot.session.manager import SessionManager
from nanobot.session.model_selection import (
    SESSION_MODEL_PRESET_METADATA_KEY,
    model_preset_from_metadata,
)

#: Preset a session runs on when nothing ever selected one for it.
FALLBACK_PRESET = "default"

#: Presets the stubbed runtime resolver knows about.
KNOWN_PRESETS = ("deep", "fast", "default")

BOUND_BINDING = {
    "session_key": "discord:123",
    "origin_channel": "discord",
    "origin_chat_id": "chat-9",
}


@dataclass
class Turn:
    """One executed agent turn, as observed from inside the agent."""

    mode: str  # "direct" (unbound) | "session" (bound)
    session_key: str
    model: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Runtime:
    model_preset: str

    @property
    def model(self) -> str:
        return f"model-for-{self.model_preset}"


@dataclass
class _Response:
    content: str


class _NoTools:
    def get(self, name: str) -> None:
        return None


class RunRecorder:
    """Real recorder semantics: every write is kept, in order."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        self.records.append((run_id, dict(record)))

    @property
    def last(self) -> dict[str, Any]:
        return self.records[-1][1]

    @property
    def run_ids(self) -> list[str]:
        return [run_id for run_id, _ in self.records]

    def statuses(self) -> list[str]:
        return [record["status"] for _, record in self.records]


class ContractAgent:
    """Agent stub over a real SessionManager.

    Only the LLM call is faked. Session lookup, preset persistence, message
    append and save all go through the real manager, so a turn's model is read
    back out of persisted session state rather than out of a dict the test
    wrote for itself.
    """

    def __init__(
        self,
        sessions: SessionManager,
        *,
        response: str = "done",
        failure: Exception | None = None,
        presets: tuple[str, ...] = KNOWN_PRESETS,
    ) -> None:
        self.sessions = sessions
        self.tools = _NoTools()
        self.turns: list[Turn] = []
        self._response = response
        self._failure = failure
        self._presets = presets

    # --- preset path (mirrors AgentLoop.set_session_model_preset) ---------
    def set_session_model_preset(self, session_key: str, name: str) -> _Runtime:
        if name not in self._presets:
            raise KeyError(name)
        session = self.sessions.get_or_create(session_key)
        session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = name
        self.sessions.save(session)
        return _Runtime(name)

    def model_of_session(self, session_key: str) -> str:
        session = self.sessions.get_or_create(session_key)
        return model_preset_from_metadata(session.metadata) or FALLBACK_PRESET

    # --- unbound path ----------------------------------------------------
    async def process_direct(self, content: str, **kwargs: Any) -> _Response:
        session_key = kwargs["session_key"]
        return self._run_turn("direct", session_key, content, dict(kwargs))

    # --- bound path ------------------------------------------------------
    async def submit_cron_turn(self, msg: InboundMessage) -> _Response:
        trigger = msg.metadata.get(CRON_TRIGGER_META) or {}
        persisted = trigger.get("persist_content", msg.content)
        return self._run_turn("session", msg.session_key, persisted, dict(msg.metadata))

    # --- shared turn body ------------------------------------------------
    def _run_turn(
        self,
        mode: str,
        session_key: str,
        content: str,
        metadata: dict[str, Any],
    ) -> _Response:
        model = self.model_of_session(session_key)
        self.turns.append(Turn(mode, session_key, model, content, metadata))
        if self._failure is not None:
            raise self._failure
        session = self.sessions.get_or_create(session_key)
        session.add_message("user", content)
        session.add_message("assistant", self._response)
        self.sessions.save(session)
        return _Response(self._response)


def make_job(job_id: str = "job-1", bound: bool = False, **payload_kw: Any) -> CronJob:
    """Build a cron job; ``bound=True`` adds the full (atomic) binding."""
    if bound:
        payload_kw = {**BOUND_BINDING, **payload_kw}
    payload = CronPayload(kind="agent_turn", message="ping", **payload_kw)
    return CronJob(
        id=job_id,
        name="nightly",
        enabled=True,
        payload=payload,
        schedule=CronSchedule(kind="every", every_ms=60_000),
        state=CronJobState(),
    )


def session_files(manager: SessionManager) -> set[str]:
    """Session keys currently present on disk."""
    keys = (
        manager._session_key_from_path(path)
        for path in manager.sessions_dir.glob("*.jsonl")
    )
    return {key for key in keys if key}


def cached_keys(manager: SessionManager) -> set[str]:
    """Session keys currently held in the manager's in-memory caches."""
    return set(manager._cache) | set(manager._overflow_cache)
