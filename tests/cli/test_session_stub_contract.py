"""The shared CLI stub may only fake methods the real SessionManager still has.

Eight copies of this stub drifted apart once already: they kept faking
``maybe_prune_cron_run_sessions`` long after the real manager dropped it, so the
tests kept passing against an interface nobody ships.
"""

import pytest

from nanobot.session.manager import SessionManager
from tests.cli.fakes import SessionManagerStub

_FAKED_METHODS = sorted(name for name in vars(SessionManagerStub) if not name.startswith("_"))


def test_the_stub_fakes_something():
    assert _FAKED_METHODS


@pytest.mark.parametrize("name", _FAKED_METHODS)
def test_stub_does_not_fake_a_method_the_real_manager_lacks(name):
    assert callable(getattr(SessionManager, name, None)), (
        f"SessionManagerStub fakes {name}(), SessionManager no longer has it"
    )
