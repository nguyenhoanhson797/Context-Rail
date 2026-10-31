from __future__ import annotations

from pathlib import Path

FORBIDDEN = (
    "--force", "--force-with-lease", "--no-verify", '"--all"', '"--tags"', "--mirror",
    "commit --amend", '"reset"', '"stash"', "add -A", "add --all", '"add", "."', "commit-tree", '"clean"',
)


def test_sync_argv_is_clean() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "context_rail"
    text = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("runner/git_sync.py", "store/sync.py", "gateway/handlers/sync.py", "store/gitattributes.py")
    )
    assert [token for token in FORBIDDEN if token in text] == []
