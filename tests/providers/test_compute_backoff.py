"""C4 公共退避 helper：compute_backoff（纯计算，带封顶）+ retry_after_exceeds_cap（fail-fast 谓词）。

三处退避逻辑（image_generation._codex_retry_delay、transcription、C3 链尾重试）曾各写一份，
transcription 那份还残缺（固定 1/2/4s、无 jitter、不读 Retry-After）。收敛成一个真源。
fail-fast 拆成独立谓词，只给会真正等待的 C3 用；image/transcription 不 fail-fast，行为不变。
"""

import pytest

from nanobot.providers.base import compute_backoff, retry_after_exceeds_cap


class TestComputeBackoff:
    def test_honors_retry_after_within_cap(self) -> None:
        # Retry-After=5、cap=120 → 约 5（jitter 上浮至多 +10%），且不超 cap。
        for _ in range(200):
            delay = compute_backoff(1, 5.0, base=1.0, cap=120.0)
            assert 5.0 <= delay <= 5.5

    def test_retry_after_never_exceeds_cap(self) -> None:
        # 即便 Retry-After 顶到 cap，加 jitter 也被 cap 夹住。
        for _ in range(200):
            delay = compute_backoff(1, 120.0, base=1.0, cap=120.0)
            assert delay <= 120.0

    def test_zero_retry_after_means_immediate(self) -> None:
        # Retry-After=0 → 立即（约 0），不能被误当作缺失而退化成 cap。
        assert compute_backoff(1, 0.0, base=1.0, cap=120.0) == 0.0

    def test_exponential_when_no_retry_after(self) -> None:
        # 缺 Retry-After、base=2、attempt=3 → 2*2**2=8，jitter 0.9~1.1 → [7.2, 8.8]。
        for _ in range(200):
            delay = compute_backoff(3, None, base=2.0, cap=120.0)
            assert 7.2 <= delay <= 8.8

    def test_exponential_capped(self) -> None:
        # attempt 很大 → 指数退避被 cap 夹住，不越界。
        for _ in range(200):
            delay = compute_backoff(30, None, base=1.0, cap=120.0)
            assert 108.0 <= delay <= 120.0


class TestRetryAfterExceedsCap:
    def test_exceeds(self) -> None:
        assert retry_after_exceeds_cap(600.0, 120.0) is True

    def test_at_cap_is_not_exceeding(self) -> None:
        assert retry_after_exceeds_cap(120.0, 120.0) is False

    def test_missing_never_exceeds(self) -> None:
        assert retry_after_exceeds_cap(None, 120.0) is False
