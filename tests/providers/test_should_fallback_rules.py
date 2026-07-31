"""_should_fallback 的判定顺序锁定表。

这些用例在重构前后必须给出完全相同的结论：规则表的顺序即语义，
任何调换都会让某一行变色。
"""

import pytest

from nanobot.providers.base import LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider


def _resp(**kwargs) -> LLMResponse:
    kwargs.setdefault("content", "")
    kwargs.setdefault("finish_reason", "error")
    return LLMResponse(**kwargs)


CASES = [
    # (说明, response, 期望切换)
    ("欠费文案即便带 400 也要换 provider", _resp(content="Insufficient balance", error_status_code=400), True),
    ("kind=authentication 直接切", _resp(error_kind="authentication"), True),
    ("鉴权 token 压过 invalid_request 类型", _resp(error_type="invalid_request_error", error_code="invalid_api_key"), True),
    ("内容过滤换谁都一样", _resp(error_kind="content_filter"), False),
    ("超长上下文不切", _resp(error_kind="context_length"), False),
    ("结构化字段里出现 invalid_request 也不切", _resp(error_type="invalid_request_error"), False),
    ("裸 403 视为鉴权问题，切", _resp(error_status_code=403), True),
    ("403 但带 content_filter，不切", _resp(error_status_code=403, error_kind="content_filter"), False),
    ("正文里的 unauthorized 也算鉴权", _resp(content="Unauthorized"), True),
    ("provider 明说别重试就不切", _resp(error_should_retry=False, error_status_code=500), False),
    ("400 参数错误不切，哪怕 should_retry=True", _resp(error_status_code=400, error_should_retry=True), False),
    ("should_retry=True 且状态码无结论时切", _resp(error_should_retry=True), True),
    ("429 限流切", _resp(error_status_code=429), True),
    ("503 服务端错误切", _resp(error_status_code=503), True),
    ("kind=timeout 切", _resp(error_kind="timeout"), True),
    ("正文 empty choices 当瞬时故障切", _resp(content="model returned empty choices"), True),
    ("认不出来的错误默认不切", _resp(content="something weird happened"), False),
]


@pytest.mark.parametrize("why,response,expected", CASES, ids=[c[0] for c in CASES])
def test_should_fallback_verdicts(why: str, response: LLMResponse, expected: bool) -> None:
    assert FallbackProvider._should_fallback(response) is expected, why
