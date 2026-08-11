"""Subagent image-generation tool wiring.

generate_image must be usable from a spawned subagent, not just the main loop.
That requires the full injection chain: subagent scope on the tool, the
image_generation config copied into the subagent ToolsConfig, and the provider
configs threaded into the subagent ToolContext.
"""

from nanobot.agent.tools.image_generation import ImageGenerationToolConfig
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, ToolsConfig

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _manager(tmp_path, **kwargs):
    from nanobot.agent.subagent import SubagentManager

    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        **kwargs,
    )


def test_subagent_tools_include_usable_generate_image(tmp_path):
    """Enabled image generation + provider configs => subagent gets a live tool."""
    provider_marker = object()
    mgr = _manager(
        tmp_path,
        tools_config=ToolsConfig(
            image_generation=ImageGenerationToolConfig(enabled=True, provider="testprov"),
        ),
        image_generation_provider_configs={"testprov": provider_marker},
    )

    registry = mgr._build_tools()

    assert registry.has("generate_image")
    tool = registry.get("generate_image")
    # Empty provider_configs would make the tool a no-op that errors at call time.
    assert tool.provider_configs.get("testprov") is provider_marker


def test_subagent_omits_generate_image_when_disabled(tmp_path):
    """Default config leaves image generation off, so the subagent must not load it."""
    mgr = _manager(tmp_path)

    registry = mgr._build_tools()

    assert not registry.has("generate_image")
