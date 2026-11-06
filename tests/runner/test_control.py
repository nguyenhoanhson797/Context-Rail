"""Local control files recover after corruption instead of staying poisoned."""

from __future__ import annotations

from pathlib import Path

from context_rail.runner.control import merge_control, read_control
from context_rail.runner.dispatch import read_session, session_path, write_session


def test_merging_into_a_corrupt_control_clears_the_marker(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_control(path, identity="item", key="item") == {
        "item": "item",
        "corrupt": True,
    }

    merge_control(
        path,
        identity="item",
        key="item",
        values={"session": "session-1"},
    )
    assert read_control(path, identity="item", key="item") == {
        "schema": 1,
        "item": "item",
        "session": "session-1",
    }


def test_a_corrupt_session_file_recovers_after_a_new_write(tmp_path: Path) -> None:
    path = session_path(tmp_path, "item-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")
    assert read_session(tmp_path, "item-a", "droid") is None

    write_session(tmp_path, "item-a", "droid", "session-1")
    assert read_session(tmp_path, "item-a", "droid") == "session-1"
    assert read_session(tmp_path, "item-a", "codex") is None


def test_an_invalid_session_id_is_ignored(tmp_path: Path) -> None:
    write_session(tmp_path, "item-b", "droid", "session-1")
    path = session_path(tmp_path, "item-b")
    path.write_text(
        '{"schema": 1, "item": "item-b", "runner": "droid", "session": "--bad"}\n',
        encoding="utf-8",
    )
    assert read_session(tmp_path, "item-b", "droid") is None
