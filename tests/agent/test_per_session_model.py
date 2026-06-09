"""Per-session model override resolution (Stage 1).

A `/model X` switch must affect only the current session, never the global
runtime model. cron jobs resolve their own preset. These tests exercise the
pure resolution path on AgentLoop without running a full turn.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ModelPresetConfig, _resolve_tool_config_refs
from nanobot.providers.factory import ProviderSnapshot

# Resolve ToolsConfig forward refs so AgentLoop() works in isolated test runs.
_resolve_tool_config_refs()


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens, temperature=0.1, reasoning_effort=None
    )
    return provider


def _session(key: str, preset: str | None = None):
    metadata: dict = {}
    if preset is not None:
        metadata["model_preset"] = preset
    return SimpleNamespace(key=key, metadata=metadata)


def _make_loop(tmp_path):
    base_provider = _provider("base-model")
    fast_provider = _provider("fast-model", max_tokens=4096)

    def loader(name: str) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider=fast_provider,
            model="fast-model",
            context_window_tokens=32_768,
            signature=(name, "fast-model"),
        )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=base_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"fast": ModelPresetConfig(model="fast-model", provider="openai")},
        preset_snapshot_loader=loader,
    )
    return loop, base_provider, fast_provider


def test_session_override_resolves_independently(tmp_path) -> None:
    loop, base_provider, fast_provider = _make_loop(tmp_path)
    sess_a = _session("a", preset="fast")
    sess_b = _session("b")  # no override

    prov_a, model_a, ctx_a = loop._effective_run_model(sess_a)
    prov_b, model_b, ctx_b = loop._effective_run_model(sess_b)

    # Session A rides the per-session override.
    assert prov_a is fast_provider
    assert model_a == "fast-model"
    assert ctx_a == 32_768

    # Session B falls back to the global model.
    assert prov_b is base_provider
    assert model_b == "base-model"
    assert ctx_b == 1000

    # The global runtime model is never mutated by a per-session override.
    assert loop.model == "base-model"
    assert loop.provider is base_provider


def test_no_session_falls_back_to_global(tmp_path) -> None:
    loop, base_provider, _ = _make_loop(tmp_path)
    prov, model, ctx = loop._effective_run_model(None)
    assert prov is base_provider
    assert model == "base-model"
    assert ctx == 1000


def test_invalid_preset_falls_back_to_global(tmp_path) -> None:
    loop, base_provider, _ = _make_loop(tmp_path)
    sess = _session("c", preset="does-not-exist")

    # Must not raise — fall back to global.
    prov, model, ctx = loop._effective_run_model(sess)
    assert prov is base_provider
    assert model == "base-model"
    assert ctx == 1000


def test_resolve_session_snapshot_is_cached(tmp_path) -> None:
    loop, _, _ = _make_loop(tmp_path)
    first = loop._resolve_session_snapshot("fast")
    second = loop._resolve_session_snapshot("fast")
    # Same signature → cache hit → identical object (loader not re-invoked).
    assert first is second
