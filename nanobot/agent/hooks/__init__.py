"""Agent hooks package."""

from nanobot.agent.hooks.rewrite import CommandRewriteHook
from nanobot.agent.hooks.trace import TraceHook

__all__ = ["CommandRewriteHook", "TraceHook"]
