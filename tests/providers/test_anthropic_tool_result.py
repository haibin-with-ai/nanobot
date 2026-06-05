"""Tests for AnthropicProvider._tool_result_block image_url conversion.

Regression for: tool results containing OpenAI-format image_url blocks
(e.g. from read_file on an image file, via build_image_content_blocks)
were passed to Anthropic unconverted, causing silent image drops with a
"Non-transient LLM error with image content, retrying without images"
warning.
"""

from nanobot.providers.anthropic_provider import AnthropicProvider


def test_tool_result_block_converts_image_url_in_list_content():
    """image_url blocks inside tool_result list content must be translated
    to Anthropic-native image blocks; sibling text blocks pass through."""
    msg = {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
                "_meta": {"path": "/tmp/x.png"},
            },
            {"type": "text", "text": "(Image file: /tmp/x.png)"},
        ],
    }
    block = AnthropicProvider._tool_result_block(msg)

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_1"  # underscores are valid per Anthropic, preserved
    content = block["content"]
    assert isinstance(content, list)
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "AAAA",
        },
    }
    assert content[1] == {"type": "text", "text": "(Image file: /tmp/x.png)"}


def test_tool_result_block_preserves_string_content():
    """String content must be passed through unchanged; the image-conversion
    path for lists must not affect the string path."""
    msg = {
        "role": "tool",
        "tool_call_id": "call_2",
        "content": "plain tool output",
    }
    block = AnthropicProvider._tool_result_block(msg)

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_2"  # underscores are valid per Anthropic, preserved
    assert block["content"] == "plain tool output"


def test_sanitize_tool_id_preserves_underscores():
    """Native Anthropic ids (``toolu_01...``) and OpenAI ids (``call_abc``)
    contain underscores, which Anthropic's ``^[a-zA-Z0-9_-]{1,64}$`` pattern
    allows. Mangling them to hyphens previously invalidated the thinking-block
    signature that commits to the original tool_use ids, triggering
    "thinking blocks ... cannot be modified" on replay.
    """
    from nanobot.providers.anthropic_provider import _sanitize_tool_id

    assert _sanitize_tool_id("toolu_01Ksa8yvcuRXp3TSVp7gteE3") == "toolu_01Ksa8yvcuRXp3TSVp7gteE3"
    assert _sanitize_tool_id("call_abc_123") == "call_abc_123"
    # genuinely illegal chars are still replaced
    assert _sanitize_tool_id("weird:id|x.y") == "weird-id-x-y"
