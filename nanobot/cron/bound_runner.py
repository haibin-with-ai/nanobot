"""Execution helpers for session-bound cron jobs."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Protocol

from loguru import logger

from nanobot.agent.tools.cron import CronTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.cron.session_delivery import origin_delivery_context
from nanobot.cron.session_turns import (
    CRON_DEFER_UNTIL_IDLE_META,
    CRON_TRIGGER_META,
    is_bound_cron_job,
)
from nanobot.cron.types import CronJob
from nanobot.cron.webui_metadata import cron_proactive_delivery_metadata
from nanobot.utils.prompt_templates import render_template

# A job that names no model runs on the slower, more careful preset: nobody is
# watching a scheduled run, so a wrong answer costs more than a slow one.
DEFAULT_CRON_MODEL_PRESET = "deep"


class BoundCronAgent(Protocol):
    tools: Any
    sessions: Any

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        ...

    async def process_direct(self, content: str, **kwargs: Any) -> OutboundMessage | None:
        ...

    def set_session_model_preset(self, session_key: str, name: str) -> Any:
        ...


class CronRunRecorder(Protocol):
    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        ...


def _cron_prompt_ref(prompt: str) -> dict[str, Any]:
    return {
        "id": "cron.agent_turn.reminder",
        "version": 1,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _bound_session_delivery_context(
    job: CronJob,
    *,
    turn_seed: str,
    source_label: str | None,
) -> tuple[str, str, dict[str, Any]]:
    channel, chat_id, metadata = origin_delivery_context(job)

    if channel == "websocket":
        metadata["webui"] = True
        metadata.update(
            cron_proactive_delivery_metadata(
                "websocket",
                metadata,
                turn_seed=turn_seed,
                source_label=source_label,
            )
        )

    return channel, chat_id, metadata


async def run_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Run a cron job in its bound session, or in one created just for this run."""
    # A bare session_key is not a binding: the bound path needs the origin
    # channel and chat too, and refuses jobs that only push a message out.
    if is_bound_cron_job(job):
        return await run_bound_cron_job(job, agent=agent, cron=cron)
    if job.payload.session_key and job.payload.kind == "agent_turn":
        # 静默降级会让「回复原会话」的任务一直答到别处去，必须留声。
        logger.warning(
            "Cron job '{}' names a session but lacks origin channel/chat; "
            "running it in a per-run session instead",
            job.id,
        )
    return await run_unbound_cron_job(job, agent=agent, cron=cron)


async def run_unbound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Execute a cron job in a session created for this run alone.

    The session key carries the run id, so two overlapping runs of the same job
    never write into each other's history.
    """
    prompt = render_template(
        "agent/cron_reminder.md",
        strip=True,
        message=job.payload.message,
    )
    run_id = _new_run_id(job)
    # 每次触发都新开一个会话文件，所以清理也放在造文件的地方，每天最多一次。
    agent.sessions.maybe_prune_cron_run_sessions()
    session_key = f"cron:{job.id}:{run_id}"
    model = job.payload.model or DEFAULT_CRON_MODEL_PRESET
    record_base: dict[str, Any] = {
        "job_id": job.id,
        "job_name": job.name,
        "session_key": session_key,
        "model": model,
        "prompt_ref": _cron_prompt_ref(prompt),
        "prompt_vars": {"message": job.payload.message},
        "rendered_prompt": prompt,
    }
    cron.write_run_record(run_id, {**record_base, "status": "queued"})

    cron_tool = agent.tools.get("cron")
    cron_token = None
    if isinstance(cron_tool, CronTool):
        cron_token = cron_tool.set_cron_context(True)
    try:
        # Reuse the session preset path so cron resolves models exactly the way
        # an interactive /model does, including unknown-preset errors.
        agent.set_session_model_preset(session_key, model)
        resp = await agent.process_direct(
            prompt,
            session_key=session_key,
            channel="cron",
            chat_id=job.id,
            sender_id="cron",
        )
    except (Exception, asyncio.CancelledError) as exc:
        cron.write_run_record(
            run_id,
            {
                **record_base,
                "status": "error",
                "error": str(exc) or exc.__class__.__name__,
            },
        )
        raise
    finally:
        if isinstance(cron_tool, CronTool) and cron_token is not None:
            cron_tool.reset_cron_context(cron_token)

    response = resp.content if resp else ""
    cron.write_run_record(run_id, {**record_base, "status": "ok", "response": response})
    return response


def _new_run_id(job: CronJob) -> str:
    return f"{job.id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"


async def run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Execute a session-bound cron job as a normal agent session turn."""
    session_key = job.payload.session_key
    if not session_key:
        raise ValueError(f"cron job {job.id} is missing payload.session_key")

    prompt = render_template(
        "agent/cron_reminder.md",
        strip=True,
        message=job.payload.message,
    )
    prompt_ref = _cron_prompt_ref(prompt)
    run_id = _new_run_id(job)
    channel, chat_id, metadata = _bound_session_delivery_context(
        job,
        turn_seed=f"cron:{job.id}",
        source_label=job.name,
    )
    metadata[CRON_TRIGGER_META] = {
        "job_id": job.id,
        "job_name": job.name,
        "run_id": run_id,
        "prompt_ref": prompt_ref,
        "persist_content": (
            f"Scheduled cron job triggered: {job.name}\n\n{job.payload.message}"
        ),
    }
    metadata[CRON_DEFER_UNTIL_IDLE_META] = True
    run_record_base: dict[str, Any] = {
        "job_id": job.id,
        "job_name": job.name,
        "session_key": session_key,
        "prompt_ref": prompt_ref,
        "prompt_vars": {"message": job.payload.message},
        "rendered_prompt": prompt,
    }

    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "queued",
        },
    )

    cron_tool = agent.tools.get("cron")
    cron_token = None
    if isinstance(cron_tool, CronTool):
        cron_token = cron_tool.set_cron_context(True)
    try:
        resp = await agent.submit_cron_turn(
            InboundMessage(
                channel=channel,
                sender_id="cron",
                chat_id=chat_id,
                content=prompt,
                metadata=metadata,
                session_key_override=session_key,
            )
        )
    except (Exception, asyncio.CancelledError) as exc:
        error_text = str(exc) or exc.__class__.__name__
        cron.write_run_record(
            run_id,
            {
                **run_record_base,
                "status": "error",
                "error": error_text,
            },
        )
        raise
    finally:
        if isinstance(cron_tool, CronTool) and cron_token is not None:
            cron_tool.reset_cron_context(cron_token)

    response = resp.content if resp else ""
    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "ok",
            "response": response,
        },
    )
    return response
