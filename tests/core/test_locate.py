"""Store location and slugs, importable without YAML or SQLite."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from context_rail.core import locate, slug, util
from context_rail.store import location
from helpers import _REPO_SRC
from perf.gen import make_store


def _key(rel: str) -> str:
    return f"{slug.slugify(rel)}-{slug.sha256_hex(rel.encode('utf-8'))[:8]}"


def test_locate_and_daemon_import_no_third_party() -> None:
    probe = (
        "import sys\n"
        "import context_rail.core.locate, context_rail.gateway.daemon\n"
        "heavy = [m for m in ('yaml', 'sqlite3') if m in sys.modules]\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_REPO_SRC)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_old_import_paths_resolve_to_the_same_objects() -> None:
    for name in (
        "local_dir",
        "find_store",
        "worktree_git_dir",
        "PathEscape",
        "StoreLocationError",
        "LOCAL_DIR",
        "GIT_SUBDIR",
        "ROOT_KEY",
    ):
        assert getattr(location, name) is getattr(locate, name), name
    assert util.slugify is slug.slugify
    assert util.sha256_hex is slug.sha256_hex


def test_local_dir_cases(
    tmp_path: Path,
    git_repo_with_worktree: tuple[Path, Path],
    branch_fixture_repo: SimpleNamespace,
) -> None:
    plain = make_store(tmp_path / "plain", 3)
    assert locate.local_dir(plain) == plain / ".rail-local"

    main_store, wt_store = git_repo_with_worktree
    main_dir = locate.local_dir(main_store)
    wt_dir = locate.local_dir(wt_store)
    assert main_dir != wt_dir
    assert main_dir == main_store.parent / ".git" / "context-rail" / _key("context-rail")
    assert wt_dir.parents[2].name == "worktrees" and wt_dir.parent.name == "context-rail"

    odd = make_store(branch_fixture_repo.root, 3)
    assert locate.local_dir(odd) == (
        branch_fixture_repo.root / ".git" / "context-rail" / _key("context-rail")
    )
    assert "\u00fc" in str(locate.local_dir(odd))


def test_find_store_walks_up_to_a_nested_store(tmp_path: Path) -> None:
    store = make_store(tmp_path, 3)
    deep = store / "items" / "task"
    assert locate.find_store(deep) == store
    assert locate.find_store(tmp_path) == store
