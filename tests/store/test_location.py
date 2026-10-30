"""Store discovery, per-worktree local directories and path confinement."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from context_rail.core.util import sha256_hex, slugify
from context_rail.model.manifest import load_manifest
from context_rail.store.location import (
    PathEscape,
    confine,
    find_store,
    index_path,
    local_dir,
    owns_path,
    store_prefixes,
)
from helpers import run_git

_SRC = Path(__file__).resolve().parents[2] / "src"


def _key(rel: str) -> str:
    return f"{slugify(rel)}-{sha256_hex(rel.encode('utf-8'))[:8]}"


def _git_dir(path: Path) -> Path:
    """``git rev-parse --absolute-git-dir``; tests may compare against git."""
    return Path(run_git(path, "rev-parse", "--absolute-git-dir").stdout.strip())


def test_find_store_walks_up(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    (store / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    start = tmp_path / "src" / "a"
    start.mkdir(parents=True)
    assert find_store(start) == store


def test_find_store_prefers_the_starting_folder(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    nested = tmp_path / "context-rail"
    nested.mkdir()
    (nested / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    assert find_store(tmp_path) == tmp_path


def test_find_store_returns_none_when_no_store_is_above(tmp_path: Path) -> None:
    start = tmp_path / "empty" / "deeper"
    start.mkdir(parents=True)
    assert find_store(start) is None


def test_local_dir_outside_git(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    store.mkdir()
    (store / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    assert local_dir(store) == store / ".rail-local"
    assert index_path(store) == store / ".rail-local" / "index.sqlite3"


def test_local_dir_of_a_missing_store_creates_nothing(tmp_path: Path) -> None:
    store = tmp_path / "new" / "context-rail"
    assert local_dir(store) == store / ".rail-local"
    assert not (tmp_path / "new").exists()


def test_local_dir_inside_git_uses_the_worktree_git_dir(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    main_store, _ = git_repo_with_worktree
    repo = main_store.parent
    expected = repo / ".git" / "context-rail" / _key("context-rail")
    assert local_dir(main_store) == expected
    assert index_path(main_store) == expected / "index.sqlite3"
    assert local_dir(main_store).parent.parent == _git_dir(repo)
    assert run_git(repo, "status", "--porcelain").stdout == ""


def test_store_keys_are_hashed_and_root_gets_its_own_key(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    main_store, _ = git_repo_with_worktree
    repo = main_store.parent
    git_dir = repo / ".git"
    ab = local_dir(repo / "a" / "b" / "context-rail")
    a_dash_b = local_dir(repo / "a-b" / "context-rail")
    assert ab == git_dir / "context-rail" / _key("a/b/context-rail")
    assert a_dash_b == git_dir / "context-rail" / _key("a-b/context-rail")
    assert ab != a_dash_b
    assert local_dir(repo) == git_dir / "context-rail" / "_root"


def test_two_worktrees_get_separate_local_dirs(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    main_store, wt_store = git_repo_with_worktree
    repo = main_store.parent
    git_file = wt_store.parent / ".git"
    assert git_file.is_file()
    text = git_file.read_text(encoding="utf-8")
    assert text.startswith("gitdir:")
    named_git_dir = Path(os.path.normpath(text.split(":", 1)[1].strip()))
    if not named_git_dir.is_absolute():
        named_git_dir = git_file.parent / named_git_dir
    assert named_git_dir == _git_dir(wt_store.parent)
    assert local_dir(main_store) != local_dir(wt_store)
    assert local_dir(wt_store).parent.parent == named_git_dir.resolve()
    assert named_git_dir.parent.name == "worktrees"
    assert local_dir(main_store).parent.parent == _git_dir(repo).resolve()
    assert index_path(main_store) != index_path(wt_store)


def test_relative_gitdir_resolves_against_the_dot_git_folder(tmp_path: Path) -> None:
    git_dir = tmp_path / "R" / ".git" / "worktrees" / "wt"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..", encoding="utf-8")
    worktree = tmp_path / "W"
    store = worktree / "context-rail"
    store.mkdir(parents=True)
    (store / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    (worktree / ".git").write_text("gitdir: ../R/.git/worktrees/wt\n", encoding="utf-8")
    found = local_dir(store)
    assert found.parent.parent.resolve() == git_dir.resolve()
    assert found.parent.name == "context-rail"
    common = (tmp_path / "R" / ".git" / "context-rail").resolve()
    assert not found.resolve().is_relative_to(common)


def test_local_dir_follows_dot_git_appearing_and_disappearing(tmp_path: Path) -> None:
    root = tmp_path / "T"
    store = root / "context-rail"
    store.mkdir(parents=True)
    (store / "rail.yaml").write_text("schema: 3\n", encoding="utf-8")
    assert local_dir(store) == store / ".rail-local"
    run_git(root, "init")
    assert local_dir(store) == root / ".git" / "context-rail" / _key("context-rail")
    shutil.rmtree(root / ".git")
    assert local_dir(store) == store / ".rail-local"


def test_local_dir_and_find_store_launch_no_process(
    git_repo_with_worktree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    main_store, _ = git_repo_with_worktree
    expected = local_dir(main_store)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("location helpers must not launch processes")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(os, "system", refuse)
    if hasattr(os, "popen"):
        monkeypatch.setattr(os, "popen", refuse)
    assert local_dir(main_store) == expected
    assert find_store(main_store / "items") == main_store


def test_local_dir_records_no_process_audit_events(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    main_store, _ = git_repo_with_worktree
    watched = {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn"}
    events: list[str] = []
    active = True

    def hook(event: str, args: tuple[object, ...]) -> None:
        if active and event in watched:
            events.append(event)

    sys.addaudithook(hook)
    try:
        local_dir(main_store)
    finally:
        active = False
    assert events == []


def test_local_dir_works_with_an_empty_path(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    main_store, _ = git_repo_with_worktree
    expected = local_dir(main_store)
    empty = main_store.parent / "no-tools"
    empty.mkdir()
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from context_rail.store.location import local_dir\n"
        "print(local_dir(Path(sys.argv[1])))\n"
    )
    env = dict(os.environ)
    env["PATH"] = str(empty)
    env["PYTHONPATH"] = str(_SRC)
    proc = subprocess.run(
        [sys.executable, "-c", code, str(main_store)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == expected


ESCAPES = [
    "",
    "../x.md",
    "items/../../x.md",
    "a/../b",
    "/etc/passwd",
    "\\x.md",
    "\\\\server\\share\\x.md",
    "C:/x.md",
    "C:x.md",
    "items/a\x00b.md",
    "CON",
    "items/nul.md",
    "items/COM1.txt",
    "items/LPT9.md",
    "items/con",
]


@pytest.mark.parametrize("rel", ESCAPES)
def test_confine_refuses_escapes(tmp_path: Path, rel: str) -> None:
    with pytest.raises(PathEscape):
        confine(tmp_path, rel)


def test_confine_returns_a_contained_absolute_path(tmp_path: Path) -> None:
    target = confine(tmp_path, "items/task/a.md")
    assert target.is_absolute()
    assert target.is_relative_to(tmp_path.resolve())
    assert target == (tmp_path / "items" / "task" / "a.md").resolve()


def test_confine_refuses_symlinks_that_escape_the_store(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    (store / "items").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("x\n", encoding="utf-8")
    try:
        (store / "items" / "linked").symlink_to(outside, target_is_directory=True)
        (store / "items" / "f.md").symlink_to(outside / "x.md")
    except OSError as exc:
        pytest.skip(f"symlinks are not permitted here: {exc}")
    with pytest.raises(PathEscape):
        confine(store, "items/linked/x.md")
    with pytest.raises(PathEscape):
        confine(store, "items/f.md")


def test_confine_allows_a_symlink_that_stays_inside_the_store(tmp_path: Path) -> None:
    store = tmp_path / "context-rail"
    (store / "items" / "real").mkdir(parents=True)
    (store / "items" / "real" / "a.md").write_text("a\n", encoding="utf-8")
    try:
        (store / "items" / "alias").symlink_to(
            store / "items" / "real", target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"symlinks are not permitted here: {exc}")
    assert confine(store, "items/alias/a.md") == (store / "items" / "real" / "a.md").resolve()


# ── which paths the store owns ─────────────────────────────────────────────


def _rooted_store(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (tmp_path / "rail.yaml").write_text(
        "schema: 3\npack: small\n"
        "placement:\n"
        "  item: items/{preset}/{slug}.md\n"
        "  note: notes/{preset}/{slug}.md\n"
        "  check: checks/{slug}.md\n"
        "  record: records/{yyyy}-{mm}/{id}.json\n"
        "  actor: workspace/profiles/{slug}/profile.md\n"
        "  policy: policies/{slug}.md\n",
        encoding="utf-8",
        newline="\n",
    )
    return tmp_path


def test_owns_path_is_a_literal_prefix_matcher(tmp_path: Path) -> None:
    assert owns_path("context-rail/items/a.md", ("context-rail",)) is True
    assert owns_path("context-rail", ("context-rail",)) is True
    assert owns_path("context-railish/x.md", ("context-rail",)) is False
    assert owns_path("src/app.py", ("context-rail", "AGENTS.md", ".agents")) is False
    # Nothing a caller passes may mean "everything": the rooted layout is
    # classified from the store's own structure instead.
    assert owns_path("src/app.py", (".",)) is False
    assert owns_path("src/app.py", ("",)) is False


def test_a_nested_store_owns_its_folder_and_not_the_project(tmp_path: Path) -> None:
    store = _rooted_store(tmp_path / "project") / "context-rail"
    store.mkdir()
    (store / "rail.yaml").write_text(
        (tmp_path / "project" / "rail.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    manifest = load_manifest(store)
    prefixes = store_prefixes(store, tmp_path / "project", manifest)

    assert prefixes == ("context-rail",)
    assert owns_path("context-rail/items/task/a.md", prefixes) is True
    assert owns_path("src/app.py", prefixes) is False
    assert owns_path("AGENTS.md", prefixes) is False


def test_a_rooted_store_owns_its_layout_and_not_the_project(tmp_path: Path) -> None:
    store = _rooted_store(tmp_path / "project")
    manifest = load_manifest(store)
    prefixes = store_prefixes(store, store, manifest)

    for owned in (
        "rail.yaml",
        "items/task/a.md",
        "notes/note/a.md",
        "checks/unit.md",
        "records/2026-10/01J.json",
        "workspace/profiles/cto/profile.md",
        "policies/charter.md",
        "packs/small/pack.yaml",
        "templates/item.md",
        "engine-issues/2026-10-01.md",
    ):
        assert owns_path(owned, prefixes) is True, owned
    for foreign in ("src/app.py", "README.md", ".env", "AGENTS.md", ".agents/skills/x/SKILL.md"):
        assert owns_path(foreign, prefixes) is False, foreign
    assert "." not in prefixes and "" not in prefixes


def test_a_rooted_store_owns_a_non_default_manifest_placement_root(tmp_path: Path) -> None:
    """Ownership follows the manifest's own placement, not a hardcoded folder list."""
    store = _rooted_store(tmp_path / "project")
    (store / "rail.yaml").write_text(
        "schema: 3\npack: small\n"
        "placement:\n"
        "  item: tickets/{preset}/{slug}.md\n"
        "  note: notes/{preset}/{slug}.md\n"
        "  check: checks/{slug}.md\n"
        "  record: records/{yyyy}-{mm}/{id}.json\n"
        "  actor: workspace/profiles/{slug}/profile.md\n"
        "  policy: policies/{slug}.md\n"
        "presets:\n"
        "  goal: goals/{slug}.md\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = load_manifest(store)
    prefixes = store_prefixes(store, store, manifest)

    assert owns_path("tickets/task/a.md", prefixes) is True
    assert owns_path("goals/g.md", prefixes) is True
    assert owns_path("src/app.py", prefixes) is False
