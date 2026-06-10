"""Inline 'provider/model' strings must be normalized into provider + clean model.

Regression for the bug where a subagent configured with
`anthropic_claude_code/claude-sonnet-4-6` shipped the whole prefixed string to
the API as the model name, triggering a not_found_error. The prefix is only a
routing hint and must be stripped once at the resolution boundary.
"""

from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.factory import _split_inline_provider


def test_splits_known_provider_prefix():
    preset = _split_inline_provider(
        ModelPresetConfig(model="anthropic_claude_code/claude-sonnet-4-6")
    )
    assert preset.provider == "anthropic_claude_code"
    assert preset.model == "claude-sonnet-4-6"


def test_normalizes_dash_in_provider_prefix():
    preset = _split_inline_provider(
        ModelPresetConfig(model="openai-codex/gpt-5.5")
    )
    assert preset.provider == "openai_codex"
    assert preset.model == "gpt-5.5"


def test_preserves_openrouter_style_model_id():
    """A '/' that belongs to the model id (prefix is not a provider) stays put."""
    preset = _split_inline_provider(
        ModelPresetConfig(model="google/gemini-3.5-flash")
    )
    assert preset.provider == "auto"
    assert preset.model == "google/gemini-3.5-flash"


def test_does_not_touch_explicit_provider():
    """When provider is set explicitly, the model is left exactly as configured."""
    preset = _split_inline_provider(
        ModelPresetConfig(provider="openrouter", model="google/gemini-3.5-flash")
    )
    assert preset.provider == "openrouter"
    assert preset.model == "google/gemini-3.5-flash"


def test_empty_rest_is_left_unchanged():
    preset = _split_inline_provider(
        ModelPresetConfig(model="anthropic_claude_code/")
    )
    # Degenerate config: nothing to split into, so it is passed through untouched.
    assert preset.model == "anthropic_claude_code/"


def test_clean_model_is_untouched():
    preset = _split_inline_provider(ModelPresetConfig(model="claude-sonnet-4-6"))
    assert preset.provider == "auto"
    assert preset.model == "claude-sonnet-4-6"
