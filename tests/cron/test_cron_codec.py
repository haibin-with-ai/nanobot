"""One codec owns the jobs.json field names, on both the read and write side."""

from __future__ import annotations

import json

from nanobot.cron.service import CronService
from nanobot.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronRunRecord,
    CronSchedule,
)


def test_cron_job_owns_its_store_field_codec() -> None:
    job = CronJob(
        id="job-1",
        name="daily",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        payload=CronPayload(message="hello", model="deep"),
    )

    stored = job.to_store_dict()

    assert stored["schedule"]["everyMs"] == 60_000
    assert stored["payload"]["message"] == "hello"
    assert stored["payload"]["model"] == "deep"
    assert stored["state"]["runHistory"] == []
    assert stored["deleteAfterRun"] is False


def test_store_dict_round_trips_every_field() -> None:
    """Encode and decode must agree; that is the whole point of one codec."""
    job = CronJob(
        id="job-1",
        name="daily",
        enabled=False,
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Shanghai"),
        payload=CronPayload(
            kind="agent_turn",
            message="hello",
            deliver=True,
            channel="discord",
            to="chat-9",
            channel_meta={"a": 1},
            session_key="discord:chat-9",
            origin_channel="discord",
            origin_chat_id="chat-9",
            origin_metadata={"b": 2},
            model="deep",
        ),
        state=CronJobState(
            next_run_at_ms=3,
            last_run_at_ms=2,
            last_status="ok",
            last_error=None,
            run_history=[CronRunRecord(run_at_ms=2, status="ok", duration_ms=11)],
        ),
        created_at_ms=1,
        updated_at_ms=2,
        delete_after_run=True,
    )

    assert CronJob.from_store_dict(job.to_store_dict()) == job


def test_cron_service_persists_through_job_codec(tmp_path, monkeypatch) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    service._store = type("Store", (), {"version": 1, "jobs": [CronJob(id="j", name="n")]})()
    marker = {"id": "codec-owned", "name": "from codec"}
    monkeypatch.setattr(CronJob, "to_store_dict", lambda self: marker)

    service._save_store()

    data = json.loads(service.store_path.read_text(encoding="utf-8"))
    assert data["jobs"] == [marker]
