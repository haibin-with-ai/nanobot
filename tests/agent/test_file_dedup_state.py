"""Read-dedup state must be owned by FileStates, not patched by callers.

A read with force=True means "give me the full content". It must also mean
the *next* read cannot be deduped away, because the caller never saw a
recorded-state read it can trust. The old code tried to express this by
writing `entry.can_dedup = False` on an entry it had fetched earlier, but
`record_read` replaces the dict slot with a fresh ReadState, so those writes
landed on an orphaned object and did nothing.
"""

import pytest

from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.filesystem import ReadFileTool

DEDUP_STUB = "File unchanged since last read"


@pytest.fixture()
def tool(tmp_path):
    return ReadFileTool(workspace=tmp_path, file_states=FileStates())


@pytest.mark.asyncio
async def test_force_read_disables_dedup_for_next_read(tool, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")

    first = await tool.execute(path=str(f), force=True)
    assert "alpha" in first

    second = await tool.execute(path=str(f), force=False)
    assert DEDUP_STUB not in second
    assert "alpha" in second
    assert "beta" in second


@pytest.mark.asyncio
async def test_dedup_still_works_after_a_non_force_read(tool, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")

    first = await tool.execute(path=str(f), force=False)
    assert "alpha" in first

    second = await tool.execute(path=str(f), force=False)
    assert DEDUP_STUB in second


@pytest.mark.asyncio
async def test_dedup_state_survives_only_via_file_states(tool, tmp_path):
    """The recorded entry itself must carry the dedupable flag."""
    f = tmp_path / "note.txt"
    f.write_text("alpha\n", encoding="utf-8")

    await tool.execute(path=str(f), force=True)
    entry = tool._file_states.get(f)
    assert entry is not None
    assert entry.can_dedup is False

    await tool.execute(path=str(f), force=False)
    entry = tool._file_states.get(f)
    assert entry is not None
    assert entry.can_dedup is True
