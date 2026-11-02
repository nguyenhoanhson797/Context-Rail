"""The workspace stage seeds pack profiles and skills without overwriting."""

from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.init import skeleton
from context_rail.gateway.handlers.onboarding_pipeline import workspace_seed
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack


def _context(store: Path) -> Context:
    manifest = load_manifest(store)
    return Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )


def _args(tmp_path: Path) -> dict[str, object]:
    return {
        "dir": str(tmp_path),
        "root": "context-rail",
        "pack": "basic",
        "key": None,
    }


def test_workspace_seed_writes_profiles_and_skills(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        result = workspace_seed(context)
    finally:
        context.close()

    assert result["state"] == "written"
    assert "workspace/cto/profile.md" in result["written"]
    assert "workspace/profiles/implementer/profile.md" in result["written"]
    assert "workspace/profiles/reviewer/profile.md" in result["written"]
    assert (store / "workspace" / "cto" / "profile.md").is_file()
    assert (store / "workspace" / "cto" / "skills" / "test-first.md").is_file()
    assert (
        store / "workspace" / "profiles" / "implementer" / "skills" / "test-first.md"
    ).is_file()


def test_workspace_seed_is_absent_only(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        path = store / "workspace" / "cto" / "profile.md"
        edited = path.read_bytes() + b"\nlocal edit\n"
        path.write_bytes(edited)

        again = workspace_seed(context)
    finally:
        context.close()

    assert again["state"] == "unchanged"
    assert path.read_bytes() == edited


def test_workspace_seed_returns_unchanged_when_complete(tmp_path: Path) -> None:
    skeleton(None, _args(tmp_path), "human", allow_existing=True)
    store = tmp_path / "context-rail"
    context = _context(store)
    try:
        workspace_seed(context)
        result = workspace_seed(context)
    finally:
        context.close()

    assert result == {"state": "unchanged", "written": []}
