"""Tests for ContextPruningConfig schema classes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot.config.schema import (
    AgentDefaults,
    ContextPruningConfig,
    HardClearConfig,
    SoftTrimConfig,
)


def test_default_values():
    cfg = ContextPruningConfig()
    assert cfg.enabled is False
    assert cfg.keep_last_assistants == 3
    assert cfg.context_budget_multiplier == 4
    assert cfg.soft_trim.enabled is False
    assert cfg.soft_trim.chunk_size == 16_000
    assert cfg.soft_trim.chunk_count == 3
    assert cfg.hard_clear.enabled is True
    assert cfg.hard_clear.ratio == 0.5


def test_camel_case_serialization():
    cfg = ContextPruningConfig(
        enabled=True,
        keep_last_assistants=1,
        soft_trim=SoftTrimConfig(enabled=True, chunk_size=8000, chunk_count=2),
        hard_clear=HardClearConfig(enabled=False, ratio=0.3),
        context_budget_multiplier=2,
    )
    d = cfg.model_dump(by_alias=True)
    assert d["enabled"] is True
    assert d["keepLastAssistants"] == 1
    assert d["softTrim"]["chunkSize"] == 8000
    assert d["hardClear"]["ratio"] == 0.3
    assert d["contextBudgetMultiplier"] == 2


def test_snake_case_input():
    cfg = ContextPruningConfig.model_validate(
        {
            "enabled": True,
            "keep_last_assistants": 5,
            "soft_trim": {"enabled": True, "chunk_size": 2000},
            "hard_clear": {"enabled": False, "ratio": 0.2},
            "context_budget_multiplier": 6,
        }
    )
    assert cfg.keep_last_assistants == 5
    assert cfg.soft_trim.chunk_size == 2000
    assert cfg.hard_clear.ratio == 0.2
    assert cfg.context_budget_multiplier == 6


def test_agent_defaults_contains_context_pruning():
    defaults = AgentDefaults()
    assert isinstance(defaults.context_pruning, ContextPruningConfig)
    assert defaults.context_pruning.enabled is False


def test_hard_clear_ratio_boundary():
    HardClearConfig(ratio=0.0)
    HardClearConfig(ratio=1.0)
    with pytest.raises(ValidationError):
        HardClearConfig(ratio=-0.1)
    with pytest.raises(ValidationError):
        HardClearConfig(ratio=1.1)
