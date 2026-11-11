"""Hashed session files: the token is a secret, the file is only its hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from context_rail.core.locate import local_dir
from context_rail.store.sessions import (
    Session,
    close_sessions,
    drop_expired_sessions,
    open_session,
    resolve_session,
)
from helpers import open_test_session


def session_files(store: Path) -> list[Path]:
    folder = local_dir(store) / "sessions"
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def plain_store(tmp_path: Path) -> Path:
    store = tmp_path / "context-rail"
    store.mkdir()
    return store


def test_the_token_is_not_stored(tmp_path: Path) -> None:
    store = plain_store(tmp_path)

    token, session = open_session(store, "lead", role="lead", anchor="project")

    [file] = session_files(store)
    assert file.name == hashlib.sha256(token.encode("utf-8")).hexdigest() + ".json"
    assert session.id == file.stem
    assert token not in file.read_text(encoding="utf-8")
    assert all(token not in path.name for path in file.parent.iterdir())


def test_resolve_round_trips(tmp_path: Path) -> None:
    store = plain_store(tmp_path)

    token, session = open_session(
        store,
        "orders-lead",
        run="01RUN",
        role="lead",
        anchor="orders",
        profile="narrow",
        granted_by="lead",
        ttl_s=60,
        now=1000.0,
    )

    assert resolve_session(store, token, now=1001.0) == session
    assert session == Session(
        id=session.id,
        seat="orders-lead",
        run="01RUN",
        role="lead",
        anchor="orders",
        profile="narrow",
        granted_by="lead",
        created=1000.0,
        expires=1060.0,
    )


def test_an_unknown_token_resolves_to_none(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    open_session(store, "lead")

    assert resolve_session(store, "not-a-token") is None
    assert resolve_session(store, "") is None
    assert resolve_session(store, "../../etc/passwd") is None
    assert resolve_session(store, "x" * 5000) is None


def test_a_session_of_another_store_does_not_resolve(tmp_path: Path) -> None:
    one = tmp_path / "one" / "context-rail"
    two = tmp_path / "two" / "context-rail"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    token, _ = open_session(one, "lead")

    assert resolve_session(two, token) is None


def test_an_expired_session_is_removed_and_none(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead", ttl_s=10, now=1000.0)

    assert resolve_session(store, token, now=1009.0) is not None
    assert resolve_session(store, token, now=1010.0) is None
    assert session_files(store) == []


def test_a_run_session_has_no_expiry(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, session = open_session(store, "worker@run1", run="run1", ttl_s=None, now=1.0)

    assert session.expires is None
    assert resolve_session(store, token, now=10.0**12) == session


def test_close_by_seat_run_and_id(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    a, _ = open_session(store, "lead")
    b, _ = open_session(store, "lead")
    c, run_session = open_session(store, "worker@r1", run="r1", ttl_s=None)
    d, other = open_session(store, "other")

    assert close_sessions(store, seat="lead") == 2
    assert resolve_session(store, a) is None and resolve_session(store, b) is None
    assert close_sessions(store, run="r1") == 1
    assert resolve_session(store, c) is None
    assert close_sessions(store, session_id=other.id) == 1
    assert resolve_session(store, d) is None
    assert close_sessions(store, seat="lead") == 0
    assert run_session.run == "r1"


def test_close_with_several_filters_needs_all_to_match(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead", run="r1")

    assert close_sessions(store, seat="lead", run="r2") == 0
    assert resolve_session(store, token) is not None
    assert close_sessions(store, seat="lead", run="r1") == 1


def test_close_refuses_to_close_everything(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead")

    with pytest.raises(ValueError):
        close_sessions(store)

    assert resolve_session(store, token) is not None


def test_close_ignores_an_id_that_is_not_a_file_name(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead")

    assert close_sessions(store, session_id="../sessions") == 0
    assert resolve_session(store, token) is not None


def test_prune_removes_only_expired(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    old, _ = open_session(store, "a", ttl_s=10, now=1000.0)
    fresh, _ = open_session(store, "b", ttl_s=1000, now=1000.0)
    forever, _ = open_session(store, "c@r", run="r", ttl_s=None, now=1000.0)

    assert drop_expired_sessions(store, now=1500.0) == 1

    assert resolve_session(store, old, now=1500.0) is None
    assert resolve_session(store, fresh, now=1500.0) is not None
    assert resolve_session(store, forever, now=1500.0) is not None
    assert drop_expired_sessions(store, now=1500.0) == 0


def test_a_corrupt_session_file_resolves_to_none(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead")
    [file] = session_files(store)

    file.write_text("{not json", encoding="utf-8")

    assert resolve_session(store, token) is None


def test_a_session_file_for_another_id_resolves_to_none(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, session = open_session(store, "lead")
    [file] = session_files(store)
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["id"] = "0" * 64
    file.write_text(json.dumps(payload), encoding="utf-8")

    assert resolve_session(store, token) is None
    assert session.id != "0" * 64


def test_a_session_with_a_wrong_type_resolves_to_none(tmp_path: Path) -> None:
    store = plain_store(tmp_path)
    token, _ = open_session(store, "lead")
    [file] = session_files(store)
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["seat"] = 7
    file.write_text(json.dumps(payload), encoding="utf-8")

    assert resolve_session(store, token) is None


def test_sessions_live_under_the_git_dir_inside_a_worktree_and_rail_local_outside(
    tmp_path: Path, git_repo_with_worktree: tuple[Path, Path]
) -> None:
    outside = plain_store(tmp_path)
    open_session(outside, "lead")
    assert session_files(outside)[0].parent == outside / ".rail-local" / "sessions"

    main_store, wt_store = git_repo_with_worktree
    main_token, _ = open_session(main_store, "lead")
    repo = main_store.parent
    [file] = session_files(main_store)
    assert file.parent == local_dir(main_store) / "sessions"
    assert (repo / ".git") in file.parents
    assert resolve_session(wt_store, main_token) is None


def test_the_test_helper_returns_a_resolvable_token(tmp_path: Path) -> None:
    store = plain_store(tmp_path)

    token = open_test_session(store, "lead", role="lead")

    assert isinstance(token, str)
    assert resolve_session(store, token).role == "lead"


def test_tokens_are_unique_and_long(tmp_path: Path) -> None:
    store = plain_store(tmp_path)

    tokens = {open_session(store, "lead")[0] for _ in range(20)}

    assert len(tokens) == 20
    assert all(len(token) >= 43 for token in tokens)
