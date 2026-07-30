"""A cron job may name the model it runs on, and may stay unbound.

Upstream requires every agent job to be bound to a session and disables the
rest. This fork runs unbound jobs in their own throwaway session, so being
unbound is a legal state rather than a defect.
"""

from __future__ import annotations

import json

from nanobot.cron.service import CronService
from nanobot.cron.types import CronPayload, CronSchedule


def _store(tmp_path):
    return tmp_path / "cron.json"


def _agent_payload(**kw) -> CronPayload:
    return CronPayload(kind="agent_turn", message="ping", **kw)


class TestPayloadModelField:
    def test_model_defaults_to_unspecified(self):
        assert _agent_payload().model is None

    def test_model_survives_a_store_roundtrip(self):
        from dataclasses import asdict
        payload = _agent_payload(model="fast")
        assert CronPayload.from_store_dict(asdict(payload)).model == "fast"

    def test_unspecified_model_stays_unspecified(self):
        """None means 'never said'; resolution to deep happens at run time."""
        from dataclasses import asdict
        assert CronPayload.from_store_dict(asdict(_agent_payload())).model is None

    def test_preset_alias_is_not_accepted(self):
        """One name for one thing; an alias would drift from the runtime key."""
        assert CronPayload.from_store_dict(
            {"kind": "agent_turn", "message": "ping", "preset": "fast"}
        ).model is None


class TestModelReachesTheStoredJob:
    def test_add_job_persists_the_requested_model(self, tmp_path):
        service = CronService(_store(tmp_path))
        job = service.add_job(
            name="nightly",
            message="ping",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            model="fast",
        )
        assert job.payload.model == "fast"

    def test_stored_model_survives_a_reload(self, tmp_path):
        path = _store(tmp_path)
        job = CronService(path).add_job(
            name="nightly",
            message="ping",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            model="fast",
        )
        reloaded = CronService(path).get_job(job.id)
        assert reloaded is not None and reloaded.payload.model == "fast"

    def test_add_rejects_model_on_bound_job(self, tmp_path):
        import pytest

        service = CronService(_store(tmp_path))
        with pytest.raises(ValueError, match="bound cron job cannot specify a model"):
            service.add_job(
                name="bound",
                message="ping",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                session_key="discord:123",
                origin_channel="discord",
                origin_chat_id="chat-9",
                model="fast",
            )


class TestBindingIsAtomicAtStorageBoundary:
    def test_add_rejects_incomplete_binding(self, tmp_path):
        import pytest

        service = CronService(_store(tmp_path))
        with pytest.raises(ValueError, match="incomplete cron session binding"):
            service.add_job(
                name="half-bound",
                message="ping",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                session_key="discord:123",
            )

    def test_load_disables_incomplete_binding(self, tmp_path):
        path = _store(tmp_path)
        path.write_text(json.dumps({
            "version": 1,
            "jobs": [{
                "id": "half-bound-1",
                "name": "half-bound",
                "enabled": True,
                "payload": {
                    "kind": "agent_turn",
                    "message": "ping",
                    "sessionKey": "discord:123",
                },
                "schedule": {"kind": "every", "everyMs": 60000},
                "state": {"nextRunAtMs": 1},
            }],
        }))

        job = CronService(path).get_job("half-bound-1")

        assert job is not None
        assert job.enabled is False
        assert job.state.next_run_at_ms is None
        assert job.state.last_status == "error"
        assert "incomplete cron session binding" in (job.state.last_error or "")


class TestModelSurvivesAStoreRewrite:
    """Any full store rewrite must carry the model, or a restart silently
    downgrades every per-job preset back to the cron default."""

    def _seed(self, path) -> str:
        return CronService(path).add_job(
            name="nightly",
            message="ping",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            model="fast",
        ).id

    def _rewrite(self, path, job_id: str) -> None:
        service = CronService(path)
        assert service.get_job(job_id) is not None  # forces the lazy load
        service._save_store()

    def test_rewritten_file_still_carries_the_model(self, tmp_path):
        path = _store(tmp_path)
        job_id = self._seed(path)
        self._rewrite(path, job_id)
        payloads = {j["id"]: j["payload"] for j in json.loads(path.read_text())["jobs"]}
        assert payloads[job_id].get("model") == "fast"


class TestUnboundJobsStayEnabled:
    def test_adding_an_unbound_agent_job_keeps_it_enabled(self, tmp_path):
        service = CronService(_store(tmp_path))
        job = service.add_job(
            name="unbound",
            message="ping",
            schedule=CronSchedule(kind="every", every_ms=60_000),
        )
        assert job.enabled is True
        assert job.state.last_status != "error"
        assert job.state.next_run_at_ms is not None

    def test_loading_an_unbound_agent_job_keeps_it_enabled(self, tmp_path):
        path = _store(tmp_path)
        path.write_text(json.dumps({
            "version": 1,
            "jobs": [{
                "id": "unbound-1",
                "name": "unbound",
                "enabled": True,
                "payload": {"kind": "agent_turn", "message": "ping", "model": "fast"},
                "schedule": {"kind": "every", "everyMs": 60000},
                "state": {"nextRunAtMs": 1},
            }],
        }))
        job = CronService(path).get_job("unbound-1")
        assert job is not None
        assert job.enabled is True
        assert job.payload.model == "fast"
        assert job.state.last_status != "error"

    def test_malformed_legacy_delivery_payload_is_still_rejected(self, tmp_path):
        """Dropping the binding rule must not also drop legacy payload validation."""
        path = _store(tmp_path)
        path.write_text(json.dumps({
            "version": 1,
            "jobs": [{
                "id": "legacy-1",
                "name": "legacy",
                "enabled": True,
                "payload": {
                    "kind": "agent_turn", "message": "ping", "deliver": True, "channel": "discord",
                },
                "schedule": {"kind": "every", "everyMs": 60000},
                "state": {"nextRunAtMs": 1},
            }],
        }))
        job = CronService(path).get_job("legacy-1")
        assert job is not None
        assert job.enabled is False
        assert job.state.last_status == "error"


class TestToolExposesModel:
    def test_schema_offers_the_model_parameter(self):
        from nanobot.agent.tools.cron import CronTool
        assert "model" in CronTool(cron_service=None).parameters["properties"]

    def test_model_is_optional(self):
        from nanobot.agent.tools.cron import CronTool
        assert "model" not in CronTool(cron_service=None).parameters.get("required", [])
