"""模型 preset 的解析与报错，spawn 与 cron 共用同一套文案。"""

from __future__ import annotations

from typing import Any


class UnknownPresetError(LookupError):
    """Model asked for a preset that does not exist; surface it as a tool error."""


def resolve_preset(resolver: Any, model: str | None, default: Any = None) -> Any:
    """未指定 preset 就用 default；resolver 缺席时忽略 model 而不是报错。"""
    if not model or resolver is None:
        return default
    try:
        return resolver.resolve_preset(model)
    except (KeyError, ValueError) as e:
        # resolver 的报错已经带上可用 preset 列表，原样透出别再拼一遍
        detail = e.args[0] if e.args else str(e)
        raise UnknownPresetError(str(detail)) from None
