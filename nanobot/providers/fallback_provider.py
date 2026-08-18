"""Provider wrapper that transparently fails over to fallback models on error."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse

# Circuit breaker tuned to match OpenAICompatProvider's Responses API breaker.
_PRIMARY_FAILURE_THRESHOLD = 3
_PRIMARY_COOLDOWN_S = 60
_FALLBACK_ERROR_KINDS = frozenset({
    "timeout",
    "connection",
    "server_error",
    "rate_limit",
    "overloaded",
    # 与上游相反：模型拒答换一个模型再问，不当作终态。
    "refusal",
})
# 限流冷却：命中后这一档模型暂时不再排进候选。按错误类型分流两档时长。
# 账号配额/账单耗尽：短期不会恢复，长冷却切走，成本近零（有替补）。本期固定，不加配置口子。
QUOTA_EXHAUSTED_COOLDOWN_S = 600.0
# 瞬时限流（rate_limit / overloaded）：秒级恢复，honor Retry-After 并夹在下面区间。
TRANSIENT_COOLDOWN_DEFAULT_S = 30.0
TRANSIENT_COOLDOWN_MIN_S = 5.0
TRANSIENT_COOLDOWN_MAX_S = 120.0
_AUTHENTICATION_ERROR_KINDS = frozenset({
    "authentication",
    "auth",
    "permission",
})
_AUTHENTICATION_ERROR_TOKENS = (
    "authentication_error",
    "authentication error",
    "invalid_api_key",
    "invalid api key",
    "incorrect_api_key",
    "incorrect api key",
    "expired_api_key",
    "expired api key",
    "invalid credential",
    "expired credential",
    "credential has expired",
    "credentials have expired",
    "invalid_token",
    "invalid token",
    "expired_token",
    "expired token",
    "unauthorized",
    "permission_denied",
    "permission denied",
    "access_denied",
    "account_deactivated",
    "organization_deactivated",
)
_NON_FALLBACK_ERROR_KINDS = frozenset({
    "content_filter",
    "context_length",
    "invalid_request",
})
_FALLBACK_ERROR_TOKENS = (
    "rate_limit",
    "rate limit",
    "too_many_requests",
    "too many requests",
    "overloaded",
    "server_error",
    "server error",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "connection",
    "empty",  # API returned empty choices (e.g. DeepSeek peak hours), transient
    "insufficient_quota",
    "insufficient quota",
    "quota_exceeded",
    "quota exceeded",
    "quota_exhausted",
    "quota exhausted",
    "billing_hard_limit",
    "insufficient_balance",
    "balance",
    "out of credits",
)


FallbackModelObserver = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _Candidate:
    key: tuple[str, str]
    model: str
    provider: LLMProvider | None
    preset: Any | None
    kwargs: dict[str, Any]
    primary: bool = False


@dataclass(frozen=True)
class _ErrorFacts:
    """一次失败响应里所有参与判定的信号，统一小写。"""

    response: LLMResponse
    status: int | None
    kind: str
    error_type: str
    code: str
    text: str

    @classmethod
    def of(cls, response: LLMResponse) -> _ErrorFacts:
        return cls(
            response=response,
            status=response.error_status_code,
            kind=(response.error_kind or "").lower(),
            error_type=(response.error_type or "").lower(),
            code=(response.error_code or "").lower(),
            text=(response.content or "").lower(),
        )

    def structured_has(self, tokens: Iterable[str]) -> bool:
        """只看结构化字段，不看正文。"""
        values = (self.kind, self.error_type, self.code)
        return any(token in value for value in values for token in tokens)

    def anywhere_has(self, tokens: Iterable[str]) -> bool:
        values = (self.kind, self.error_type, self.code, self.text)
        return any(token in value for value in values for token in tokens)


@dataclass(frozen=True)
class _FallbackRule:
    verdict: bool
    why_here: str
    matches: Callable[[_ErrorFacts], bool]


# 顺序即语义：取第一条命中的规则。分四段，段内顺序也有依赖，见每条的 why_here。
_FALLBACK_RULES: tuple[_FallbackRule, ...] = (
    # 第一段：能明确指认「换一家还有救」的强信号。它们的报文里常混着
    # invalid_request、400 这类特征，排在后面会被第二段直接判死。
    _FallbackRule(
        True,
        "欠费报文常带 400 或 invalid_request，必须抢在状态码与非切换规则之前",
        lambda f: LLMProvider.is_arrearage_response(f.response),
    ),
    _FallbackRule(
        True,
        "error_kind 已经点名鉴权，最干净的信号，先用",
        lambda f: f.kind in _AUTHENTICATION_ERROR_KINDS,
    ),
    _FallbackRule(
        True,
        "OpenAI 家族把 invalid_api_key 塞进 error_type=invalid_request_error，"
        "这条若排到下一条之后会被误判成不可切换",
        lambda f: f.structured_has(_AUTHENTICATION_ERROR_TOKENS),
    ),
    # 第二段：明确「换谁都一样」的信号。
    _FallbackRule(
        False,
        "内容过滤、超长、参数非法，换模型解决不了",
        lambda f: f.kind in _NON_FALLBACK_ERROR_KINDS,
    ),
    _FallbackRule(
        False,
        "同上，但信号藏在 error_type / error_code 里",
        lambda f: f.structured_has(_NON_FALLBACK_ERROR_KINDS),
    ),
    # 第三段：结构化信号用尽，才落到 HTTP 状态码。
    _FallbackRule(
        True,
        "401/403 视为鉴权问题；排在第二段之后，403 + content_filter 才不会被误切",
        lambda f: f.status in {401, 403},
    ),
    _FallbackRule(
        True,
        "正文里的鉴权关键词，可靠性低于结构化字段，所以排在状态码之后",
        lambda f: any(token in f.text for token in _AUTHENTICATION_ERROR_TOKENS),
    ),
    _FallbackRule(
        False,
        "provider 显式说了别重试，压过下面所有状态码启发",
        lambda f: f.response.error_should_retry is False,
    ),
    _FallbackRule(
        False,
        "400/404/422 是确定性请求错误；排在 should_retry=True 之前，"
        "不让 SDK 的乐观重试标记把它救活",
        lambda f: f.status in {400, 404, 422},
    ),
    _FallbackRule(
        True,
        "排除确定性错误后，provider 说可重试就换一家试",
        lambda f: f.response.error_should_retry is True,
    ),
    _FallbackRule(
        True,
        "408/409/429 与 5xx 是典型的瞬时或过载",
        lambda f: f.status is not None and (f.status in {408, 409, 429} or 500 <= f.status <= 599),
    ),
    _FallbackRule(
        True,
        "provider 归一化过的瞬时错误类别",
        lambda f: f.kind in _FALLBACK_ERROR_KINDS,
    ),
    # 第四段：兜底的正文子串匹配，最不可靠，只在前面全无结论时生效。
    _FallbackRule(
        True,
        "限流、超时、空返回一类只在文案里露头的情况",
        lambda f: f.anywhere_has(_FALLBACK_ERROR_TOKENS),
    ),
)


def _declared_provider_name(preset: Any) -> str:
    """默认命名空间：配置里写的 provider 字段。调用方可传解析器换成真实后端名。"""
    return str(getattr(preset, "provider", "") or "")


class FallbackProvider(LLMProvider):
    """Wrap a primary provider and transparently failover to fallback models.

    When the primary model returns a fallbackable error before content has been
    streamed, the wrapper tries each fallback model in order. Streamed timeout
    errors are the recovery exception: the caller may close the current stream
    segment, then the wrapper continues failover with later deltas in a new
    segment. Each fallback model may reside on a different provider — a factory
    callable creates the underlying provider on-the-fly.

    Key design:
    - Candidate selection is request-scoped, but the wrapper is NOT stateless:
      `_primary_failures` / `_primary_tripped_at` drive the primary circuit
      breaker and `_cooldowns` keeps per-(provider, model) rate-limit
      deadlines. Both outlive a single turn and only reset in-process — a
      gateway restart forgets every cooldown.
    - That state is plain attribute mutation with no lock: it assumes all turns
      run on one event loop, so mutations never interleave mid-statement. Do not
      share one instance across threads or loops.
    - Skipped when content was already streamed to avoid duplicate output,
      except timeout recovery can resume in a new stream segment.
    - Recursive failover is prevented by the factory returning plain providers.
    - Primary provider is circuit-broken after repeated failures to avoid
      wasting requests on a known-bad endpoint.
    """

    supports_stream_recover_callback = True

    def __init__(
        self,
        primary: LLMProvider,
        fallback_presets: list[Any],
        provider_factory: Callable[[Any], LLMProvider],
        fallback_model_observer: FallbackModelObserver | None = None,
        clock: Callable[[], float] | None = None,
        primary_name: str | None = None,
        provider_name_resolver: Callable[[Any], str] | None = None,
    ):
        self._primary = primary
        self._fallback_presets = list(fallback_presets)
        self._provider_factory = provider_factory
        self._fallback_model_observer = fallback_model_observer
        self._has_fallbacks = bool(fallback_presets)
        self._primary_failures = 0
        self._primary_tripped_at: float | None = None
        self._clock = clock or time.monotonic
        self._cooldowns: dict[tuple[str, str], float] = {}
        # 冷却身份必须主备同名，否则同一个端点会被记成两个 key，冷却期内照打不误。
        self._name_of = provider_name_resolver or _declared_provider_name
        self._primary_name = primary_name or str(
            getattr(primary, "name", None) or type(primary).__name__
        )

    @property
    def generation(self):
        return self._primary.generation

    @generation.setter
    def generation(self, value):
        self._primary.generation = value

    def get_default_model(self) -> str:
        return self._primary.get_default_model()

    def set_fallback_model_observer(self, observer: FallbackModelObserver | None) -> None:
        """Attach a process-level observer without changing request call signatures."""
        self._fallback_model_observer = observer

    @property
    def supports_progress_deltas(self) -> bool:
        return bool(getattr(self._primary, "supports_progress_deltas", False))

    @property
    def model_attempt_budget(self) -> int:
        """一次请求最多会试几个模型，供调用方计算墙钟预算。"""
        return 1 + len(self._fallback_presets)

    def _primary_key(self, model: str) -> tuple[str, str]:
        return (self._primary_name, model)

    def _preset_key(self, preset: Any) -> tuple[str, str]:
        return (self._name_of(preset), preset.model)

    def _cooldown_remaining(self, key: tuple[str, str]) -> float:
        until = self._cooldowns.get(key)
        if until is None:
            return 0.0
        remaining = until - self._clock()
        if remaining <= 0:
            self._cooldowns.pop(key, None)
            return 0.0
        return remaining

    def _cooling_down(self, primary_model: str) -> set[tuple[str, str]]:
        """Keys to skip this turn; if everything is cooling, keep the freest one."""
        keys = [self._primary_key(primary_model)]
        keys += [self._preset_key(preset) for preset in self._fallback_presets]
        remaining = {key: self._cooldown_remaining(key) for key in keys}
        cooling = {key for key, value in remaining.items() if value > 0}
        if len(cooling) < len(remaining):
            return cooling
        freest = min(remaining, key=lambda key: remaining[key])
        return set(remaining) - {freest}

    _RATE_LIMIT_KINDS = frozenset({"rate_limit", "rate_limit_error", "quota", "quota_exceeded"})

    def _is_rate_limited(self, response: LLMResponse) -> bool:
        if response.error_status_code == 429:
            return True
        values = (response.error_kind or "", response.error_type or "", response.error_code or "")
        return any(
            value.lower() in self._RATE_LIMIT_KINDS
            or "rate_limit" in value.lower()
            or "quota" in value.lower()
            for value in values
        )

    def _note_cooldown(self, key: tuple[str, str], response: LLMResponse) -> None:
        # 只认限流：503 之类也会带 Retry-After，但那是重试提示，不是配额耗尽。
        if not self._is_rate_limited(response):
            return
        transient = LLMProvider._is_transient_429(response)
        if transient:
            retry_after = LLMProvider._extract_retry_after_from_response(response)
            wait = min(max(retry_after or TRANSIENT_COOLDOWN_DEFAULT_S,
                           TRANSIENT_COOLDOWN_MIN_S), TRANSIENT_COOLDOWN_MAX_S)
        else:
            # 配额型/未知：长冷却，不读 Retry-After 缩短。
            wait = QUOTA_EXHAUSTED_COOLDOWN_S
        self._cooldowns[key] = self._clock() + wait
        logger.warning(
            "Model '{}' {} 429; cooling it down for {}s",
            key[1], "transient" if transient else "quota-exhausted", int(wait),
        )

    def _primary_available(self) -> bool:
        """Return True if the primary provider is not currently tripped."""
        if self._primary_tripped_at is None:
            return True
        if self._clock() - self._primary_tripped_at >= _PRIMARY_COOLDOWN_S:
            # Half-open: allow one probe attempt.
            return True
        return False

    async def chat(self, **kwargs: Any) -> LLMResponse:
        if not self._has_fallbacks:
            return await self._primary.chat(**kwargs)
        return await self._try_with_fallback(
            lambda p, kw: p.chat(**kw), kwargs, has_streamed=None
        )

    async def chat_stream(self, **kwargs: Any) -> LLMResponse:
        on_stream_recover = kwargs.pop("on_stream_recover", None)
        if not self._has_fallbacks:
            return await self._primary.chat_stream(**kwargs)

        has_streamed: list[bool] = [False]
        original_delta = kwargs.get("on_content_delta")

        async def _tracking_delta(text: str) -> None:
            if text:
                has_streamed[0] = True
            if original_delta:
                await original_delta(text)

        kwargs["on_content_delta"] = _tracking_delta
        return await self._try_with_fallback(
            lambda p, kw: p.chat_stream(**kw),
            kwargs,
            has_streamed=has_streamed,
            on_stream_recover=on_stream_recover,
        )

    def _candidates(self, kwargs: dict[str, Any]) -> list[_Candidate]:
        model = kwargs.get("model") or self._primary.get_default_model()
        candidates = [_Candidate(self._primary_key(model), model, self._primary, None, kwargs, True)]
        reasoning_disabled = str(kwargs.get("reasoning_effort") or "").lower() in {
            "none", "disabled",
        }
        for preset in self._fallback_presets:
            attempt_kwargs = {**kwargs, "model": preset.model, "max_tokens": preset.max_tokens,
                              "temperature": preset.temperature}
            if not reasoning_disabled:
                if preset.reasoning_effort is None:
                    attempt_kwargs.pop("reasoning_effort", None)
                else:
                    attempt_kwargs["reasoning_effort"] = preset.reasoning_effort
            candidates.append(_Candidate(self._preset_key(preset), preset.model, None, preset,
                                         attempt_kwargs))
        return candidates

    async def _resolve_provider(self, candidate: _Candidate) -> LLMProvider | None:
        if candidate.provider is not None:
            return candidate.provider
        try:
            return await asyncio.to_thread(self._provider_factory, candidate.preset)
        except Exception as exc:
            logger.warning("Failed to create provider for fallback '{}': {}", candidate.model, exc)
            return None

    async def _recover_stream(
        self, response: LLMResponse, has_streamed: list[bool] | None,
        on_stream_recover: Callable[[], Awaitable[None]] | None,
        kwargs: dict[str, Any],
    ) -> bool:
        if has_streamed is None or not has_streamed[0]:
            return True
        if (response.error_kind or "").lower() != "timeout":
            return False
        has_streamed[0] = False
        if on_stream_recover:
            await on_stream_recover()
        else:
            kwargs["on_content_delta"] = None
        return True

    def _note_primary_failure(self, model: str, response: LLMResponse) -> None:
        if (response.error_kind or "").lower() == "refusal":
            # 拒答是内容判断，不是可用性故障：换模型可以，但别推熔断器。
            logger.info("Primary model '{}' refused; trying another model", model)
            return
        self._primary_failures += 1
        if self._primary_failures >= _PRIMARY_FAILURE_THRESHOLD:
            self._primary_tripped_at = self._clock()
            logger.warning(
                "Primary model '{}' circuit open after {} consecutive failures",
                model, self._primary_failures,
            )

    @staticmethod
    def _label(candidate: _Candidate) -> str:
        kind = "Primary model" if candidate.primary else "Fallback"
        return f"{kind} '{candidate.model}'"

    def _skip_reason(self, candidate: _Candidate) -> str | None:
        # 冷却中的候选已在构建阶段剔除，这里只剩熔断这一种回合内跳过。
        if candidate.primary and not self._primary_available():
            return "circuit open"
        return None

    async def _try_with_fallback(
        self,
        call: Callable[[LLMProvider, dict[str, Any]], Awaitable[LLMResponse]],
        kwargs: dict[str, Any],
        has_streamed: list[bool] | None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        candidates = self._candidates(kwargs)
        primary_model = candidates[0].model
        # 冷却中的候选静默剔除，不逐回合排进候选再 skip 刷日志（保留最空闲的一个兜底）。
        cooling = self._cooling_down(primary_model)
        candidates = [candidate for candidate in candidates if candidate.key not in cooling]
        last_response: LLMResponse | None = None
        # 上一个候选为什么没成，留到下一个候选真正开跑时一起写日志，
        # 这样 journal 里能顺着读完整条降级链。
        why_here: str | None = None
        for candidate in candidates:
            skip = self._skip_reason(candidate)
            if skip is not None:
                why_here = f"{self._label(candidate)} skipped: {skip}"
                continue
            provider = await self._resolve_provider(candidate)
            if provider is None:
                why_here = f"{self._label(candidate)} unavailable: provider construction failed"
                continue
            if why_here:
                logger.info("{}; trying fallback '{}'", why_here, candidate.model)
                why_here = None
            if not candidate.primary:
                await self._notify_fallback_model(candidate.model)
            response = await call(provider, candidate.kwargs)
            response.model = candidate.model
            if response.finish_reason != "error":
                if candidate.primary:
                    self._primary_failures = 0
                    self._primary_tripped_at = None
                return response
            if not await self._recover_stream(response, has_streamed, on_stream_recover, kwargs):
                return response
            self._note_cooldown(candidate.key, response)
            last_response = response
            why_here = f"{self._label(candidate)} failed: {(response.content or '').strip()[:120]}"
            if candidate.primary:
                if not self._should_fallback(response):
                    logger.warning(
                        "Primary model '{}' returned non-fallbackable error: {}",
                        candidate.model, (response.content or "")[:120],
                    )
                    return response
                self._note_primary_failure(candidate.model, response)
        if last_response is not None:
            return last_response
        return LLMResponse(
            content=f"Primary model '{primary_model}' circuit open and no fallbacks available",
            finish_reason="error",
        )

    async def _notify_fallback_model(self, model: str) -> None:
        if self._fallback_model_observer is None:
            return
        try:
            await self._fallback_model_observer(model)
        except Exception:
            logger.exception("fallback model observer failed for '{}'", model)

    @staticmethod
    def _should_fallback(response: LLMResponse) -> bool:
        """按 _FALLBACK_RULES 的顺序取第一条命中的规则，全不命中就不切。"""
        facts = _ErrorFacts.of(response)
        for rule in _FALLBACK_RULES:
            if rule.matches(facts):
                return rule.verdict
        return False
