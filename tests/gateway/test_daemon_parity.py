"""A daemon answer equals a direct answer after the store changed behind its back."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke, try_daemon_invoke
from helpers import assert_parity, run_git, serving_daemon

_FIND = ("find", {"preset": "task"})


@contextmanager
def _warm_daemon(
    store: Path, tool: str = _FIND[0], params: dict[str, Any] | None = None
) -> Iterator[None]:
    """A daemon that has already answered one read, as a long-lived one would."""
    with serving_daemon(store):
        first = try_daemon_invoke(
            tool, dict(_FIND[1] if params is None else params), store=store, actor="human"
        )
        assert first is not None and first["ok"] is True, first
        yield


def _set_title(path: Path, title: str) -> None:
    text = path.read_text(encoding="utf-8")
    edited = re.sub(r"^title: .*$", f"title: {title}", text, count=1, flags=re.MULTILINE)
    path.write_text(edited, encoding="utf-8", newline="\n")


def test_a_hand_edited_title_is_seen(
    store_factory: Callable[..., Path], daemon_enabled: None
) -> None:
    store = store_factory(20)
    with _warm_daemon(store):
        _set_title(store / "items" / "task" / "t0.md", "Edited outside the rail")
        served = assert_parity(store, *_FIND)
    assert "Edited outside the rail" in json.dumps(served)


def test_a_new_file_on_disk_is_seen(
    store_factory: Callable[..., Path], daemon_enabled: None
) -> None:
    store = store_factory(20)
    with _warm_daemon(store):
        made = store / "items" / "task" / "hand-made.md"
        made.write_text(
            (store / "items" / "task" / "t0.md").read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        _set_title(made, "Made by hand")
        served = assert_parity(store, *_FIND)
    assert "Made by hand" in json.dumps(served)


def test_a_deleted_file_is_seen(
    store_factory: Callable[..., Path], daemon_enabled: None
) -> None:
    store = store_factory(20)
    with _warm_daemon(store):
        (store / "items" / "task" / "t0.md").unlink()
        served = assert_parity(store, *_FIND)
    assert '"t0"' not in json.dumps(served)


def test_a_git_checkout_is_seen(
    git_approved_store: SimpleNamespace, daemon_enabled: None
) -> None:
    fixture = git_approved_store
    with _warm_daemon(fixture.store):
        run_git(fixture.root, "checkout", "-b", "other")
        added = invoke(
            "add",
            {"preset": "task", "title": "Branch only"},
            store=fixture.store,
            actor="cto",
        )
        assert is_ok(added), added
        run_git(fixture.root, "add", "-A")
        run_git(fixture.root, "commit", "-m", "a task on the other branch")
        run_git(fixture.root, "checkout", "-")
        served = assert_parity(fixture.store, *_FIND)
    assert "Branch only" not in json.dumps(served)


def test_a_charter_edit_is_seen_by_the_approval_state(
    approved_store: Path, daemon_enabled: None
) -> None:
    show = {"slug": "charter"}
    with _warm_daemon(approved_store, "show", show):
        charter = next(approved_store.rglob("charter.md"))
        with charter.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\nEdited outside the rail.\n")
        served = assert_parity(approved_store, "show", show)
    assert served["value"]["approval"]["state"] != "approved"


def test_a_broken_manifest_fails_the_same_way(
    store_factory: Callable[..., Path], daemon_enabled: None
) -> None:
    store = store_factory(20)
    with _warm_daemon(store):
        manifest = store / "rail.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "pack: basic", "pack: nosuchpack"
            ),
            encoding="utf-8",
            newline="\n",
        )
        served = assert_parity(store, *_FIND)
    assert served["ok"] is False
