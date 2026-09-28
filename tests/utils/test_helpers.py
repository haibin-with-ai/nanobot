from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest
import tiktoken

from nanobot.utils import helpers
from nanobot.utils.helpers import (
    _write_text_atomic,
    current_time_str,
    split_message,
    truncate_text_to_tokens,
)


def test_split_message_no_code_blocks_unchanged():
    content = "alpha beta gamma delta"

    assert split_message(content, max_len=12) == ["alpha beta", "gamma delta"]


def test_split_message_nonpositive_maxlen_returns_unsplit():
    content = "alpha beta gamma delta"

    assert split_message(content, max_len=0) == [content]
    assert split_message(content, max_len=-1) == [content]


def test_truncate_text_to_tokens_keeps_text_within_budget():
    text = "hello world " * 100

    result = truncate_text_to_tokens(text, 10_000)

    assert result == text


def test_truncate_text_to_tokens_truncates_over_budget():
    enc = tiktoken.get_encoding("cl100k_base")
    text = "word " * 1_000

    result = truncate_text_to_tokens(text, 50)

    assert result.endswith("\n... (truncated)")
    assert len(enc.encode(result)) <= 50


def test_truncate_text_to_tokens_non_positive_budget_returns_text():
    text = "anything"

    assert truncate_text_to_tokens(text, 0) == text


def test_current_time_str_rejects_unknown_timezone():
    with pytest.raises(ZoneInfoNotFoundError):
        current_time_str("Not/AZone")


def test_write_text_atomic_fsyncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "pairing.json"
    fsync_calls: list[int] = []
    closed_fds: list[int] = []

    def fake_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    monkeypatch.setattr(helpers.os, "fsync", fake_fsync)
    monkeypatch.setattr(helpers.os, "open", lambda path, flags: 12345)
    monkeypatch.setattr(helpers.os, "close", lambda fd: closed_fds.append(fd))

    _write_text_atomic(target, '{"approved": {}}')

    assert target.read_text(encoding="utf-8") == '{"approved": {}}'
    assert len(fsync_calls) == 2
    assert fsync_calls[0] != 12345
    assert fsync_calls[1] == 12345
    assert closed_fds == [12345]


def test_write_text_atomic_keeps_file_when_directory_fsync_is_unsupported(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "pairing.json"
    fsync_calls: list[int] = []

    def fake_open(path, flags):
        raise OSError("directory fsync unsupported")

    monkeypatch.setattr(helpers.os, "fsync", lambda fd: fsync_calls.append(fd))
    monkeypatch.setattr(helpers.os, "open", fake_open)

    _write_text_atomic(target, '{"pending": {}}')

    assert target.read_text(encoding="utf-8") == '{"pending": {}}'
    assert len(fsync_calls) == 1


def test_write_text_atomic_propagates_replace_failure_without_replacing_target(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "pairing.json"
    target.write_text('{"original": true}', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="rename failed"):
        _write_text_atomic(target, '{"replacement": true}')

    assert target.read_text(encoding="utf-8") == '{"original": true}'
    assert list(tmp_path.iterdir()) == [target]


# --- image payload shrinking (2026-09-28: a dozen 2.5 MB PNGs in one subagent
# history pushed the request past Anthropic's 32 MB cap and killed the run) ---

def _png(size, mode="RGB"):
    import io
    import os as _os

    from PIL import Image

    w, h = size
    channels = len(mode)
    im = Image.frombytes(mode, size, _os.urandom(w * h * channels))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _decode(url):
    import base64
    import io

    from PIL import Image

    header, b64 = url.split(",", 1)
    return header, Image.open(io.BytesIO(base64.b64decode(b64)))


def test_image_blocks_shrink_large_panel_to_model_resolution():
    raw = _png((1672, 941))
    assert len(raw) > 4_000_000

    block = helpers.build_image_content_blocks(raw, "image/png", "/p.png", "(p)")[0]
    header, im = _decode(block["image_url"]["url"])

    assert header == "data:image/jpeg;base64"
    assert max(im.size) == helpers.MODEL_IMAGE_MAX_EDGE
    assert len(block["image_url"]["url"]) < len(raw) / 3
    assert block["_meta"]["path"] == "/p.png"


def test_image_blocks_reencode_heavy_png_already_within_edge():
    raw = _png((1254, 1254))

    block = helpers.build_image_content_blocks(raw, "image/png", "/q.png", "(q)")[0]
    header, im = _decode(block["image_url"]["url"])

    assert header == "data:image/jpeg;base64"
    assert im.size == (1254, 1254)
    assert len(block["image_url"]["url"]) < len(raw) / 2


def test_image_blocks_keep_alpha_as_png():
    raw = _png((2400, 1200), mode="RGBA")

    block = helpers.build_image_content_blocks(raw, "image/png", "/a.png", "(a)")[0]
    header, im = _decode(block["image_url"]["url"])

    assert header == "data:image/png;base64"
    assert im.size == (helpers.MODEL_IMAGE_MAX_EDGE, 784)


def test_image_blocks_leave_small_and_undecodable_images_untouched():
    import base64

    small = _png((64, 64))
    junk = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    for raw in (small, junk):
        block = helpers.build_image_content_blocks(raw, "image/png", "/s.png", "(s)")[0]
        assert block["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(raw).decode()
