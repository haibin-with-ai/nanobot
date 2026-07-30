"""Provider wrapper that transparently fails over to fallback models on error."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
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
# 限流冷却：命中后这一档模型暂时不再排进候选。
QUOTA_COOLDOWN_DEFAULT_S = 600.0
QUOTA_COOLDOWN_MIN_S = 60.0
QUOTA_COOLDOWN_MAX_S = 1800.0
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


class FallbackProvider(LLMProvider):
    """Wrap a primary provider and transparently failover to fallback models.

    When the primary model returns a fallbackable error before content has been
    streamed, the wrapper tries each fallback model in order. Streamed timeout
    errors are the recovery exception: the caller may close the current stream
    segment, then the wrapper continues failover with later deltas in a new
    segment. Each fallback model may reside on a different provider — a factory
    callable creates the underlying provider on-the-fly.

    Key design:
    - Failover is request-scoped (the wrapper itself is stateless between turns).
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
    ):
        self._primary = primary
        self._fallback_presets = list(fallback_presets)
        self._provider_factory = provider_factory
        self._fallback_model_observer = fallback_model_observer
        self._has_fallbacks = bool(fallback_presets)
        self._primary_failures = 0
        self._primary_tripped_at: float | None = None
        self._clock = clock or time.monotonic
        self._quota_cooldowns: dict[tuple[str, str], float] = {}

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
        label = getattr(self._primary, "name", None) or type(self._primary).__name__
        return (str(label), model)

    @staticmethod
    def _preset_key(preset: Any) -> tuple[str, str]:
        return (str(getattr(preset, "provider", "") or ""), preset.model)

    def _cooldown_remaining(self, key: tuple[str, str]) -> float:
        until = self._quota_cooldowns.get(key)
        if until is None:
            return 0.0
        remaining = until - self._clock()
        if remaining <= 0:
            self._quota_cooldowns.pop(key, None)
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

    def _note_quota_cooldown(self, key: tuple[str, str], response: LLMResponse) -> None:
        # 只认限流：503 之类也会带 Retry-After，但那是重试提示，不是配额耗尽。
        if not self._is_rate_limited(response):
            return
        retry_after = LLMProvider._extract_retry_after_from_response(response)
        wait = min(max(retry_after or QUOTA_COOLDOWN_DEFAULT_S, QUOTA_COOLDOWN_MIN_S), QUOTA_COOLDOWN_MAX_S)
        self._quota_cooldowns[key] = self._clock() + wait
        logger.warning(
            "Model '{}' rate limited; cooling it down for {}s", key[1], int(wait)
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
        for preset in self._fallback_presets:
            attempt_kwargs = {**kwargs, "model": preset.model, "max_tokens": preset.max_tokens,
                              "temperature": preset.temperature}
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

    def _note_primary_failure(self, response: LLMResponse) -> None:
        if (response.error_kind or "").lower() == "refusal":
            return
        self._primary_failures += 1
        if self._primary_failures >= _PRIMARY_FAILURE_THRESHOLD:
            self._primary_tripped_at = self._clock()

    async def _try_with_fallback(
        self,
        call: Callable[[LLMProvider, dict[str, Any]], Awaitable[LLMResponse]],
        kwargs: dict[str, Any],
        has_streamed: list[bool] | None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        candidates = self._candidates(kwargs)
        primary_model = candidates[0].model
        cooling = self._cooling_down(primary_model)
        last_response: LLMResponse | None = None
        for candidate in candidates:
            if candidate.key in cooling or (candidate.primary and not self._primary_available()):
                continue
            provider = await self._resolve_provider(candidate)
            if provider is None:
                continue
            if not candidate.primary:
                await self._notify_fallback_model(candidate.model)
            response = await call(provider, candidate.kwargs)
            if response.finish_reason != "error":
                if candidate.primary:
                    self._primary_failures = 0
                    self._primary_tripped_at = None
                return response
            if not await self._recover_stream(response, has_streamed, on_stream_recover, kwargs):
                return response
            self._note_quota_cooldown(candidate.key, response)
            if candidate.primary:
                last_response = response
                if not self._should_fallback(response):
                    return response
                self._note_primary_failure(response)
            else:
                last_response = response
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
        if LLMProvider.is_arrearage_response(response):
            return True
        status = response.error_status_code
        kind = (response.error_kind or "").lower()
        error_type = (response.error_type or "").lower()
        code = (response.error_code or "").lower()
        text = (response.content or "").lower()
        structured_values = (kind, error_type, code)

        if kind in _AUTHENTICATION_ERROR_KINDS:
            return True
        if any(
            token in value
            for value in structured_values
            for token in _AUTHENTICATION_ERROR_TOKENS
        ):
            return True
        if kind in _NON_FALLBACK_ERROR_KINDS:
            return False
        if any(
            token in value
            for value in structured_values
            for token in _NON_FALLBACK_ERROR_KINDS
        ):
            return False
        if status in {401, 403}:
            return True
        if any(token in text for token in _AUTHENTICATION_ERROR_TOKENS):
            return True
        if response.error_should_retry is False:
            return False
        if status in {400, 404, 422}:
            return False
        if response.error_should_retry is True:
            return True
        if status is not None and (status in {408, 409, 429} or 500 <= status <= 599):
            return True
        if kind in _FALLBACK_ERROR_KINDS:
            return True
        return any(token in value for value in (kind, error_type, code, text) for token in _FALLBACK_ERROR_TOKENS)
