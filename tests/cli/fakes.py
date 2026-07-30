"""Shared test doubles for CLI tests.

One stub, one place to fix when the real interface moves.
"""

from typing import Any


class SessionManagerStub:
    """Structural substitute for SessionManager in gateway startup tests."""

    def __init__(self, _workspace: Any = None) -> None:
        pass

    def prune_cron_run_sessions(self, **_kwargs: object) -> dict[str, object]:
        return {"keys": [], "count": 0, "bytes": 0}

    def flush_all(self) -> int:
        return 0
