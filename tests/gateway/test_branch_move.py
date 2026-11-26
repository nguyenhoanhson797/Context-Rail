"""Moving a whole branch to a new parent with ``link --replace``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import init_nested_store

from context_rail.gateway.gateway import invoke


def _add(store: Path, preset: str, title: str, rel: list[str] | None = None) -> Any:
    result = invoke(
        "add",
        {"preset": preset, "title": title, "rel": rel or []},
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def _relink(store: Path, src: str, dst: str, **flags: Any) -> dict[str, Any]:
    branch = invoke("map", {"item": src}, store=store, actor="cto")
    assert branch["ok"] is True, branch
    return invoke(
        "link",
        {
            "src": src,
            "rel": "part_of",
            "dst": dst,
            "expect": branch["value"]["center"]["etag"],
            **flags,
        },
        store=store,
        actor="cto",
    )


@pytest.fixture
def branches(tmp_path: Path) -> Path:
    store = init_nested_store(tmp_path)
    _add(store, "phase", "Phase 1")
    _add(store, "phase", "Phase 2")
    _add(store, "sprint", "Sprint 3", ["part_of=phase-1"])
    _add(store, "task", "Checkout", ["part_of=sprint-3"])
    return store


def test_replace_moves_the_branch_and_its_descendants(branches: Path) -> None:
    moved = _relink(branches, "sprint-3", "phase-2", replace=True)

    assert moved["ok"] is True, moved
    assert moved["value"]["path"] == "items/phase/phase-2/sprint-3.md"
    assert (branches / "items/phase/phase-2/sprint-3/general/checkout.md").is_file()
    assert not (branches / "items/phase/phase-1/sprint-3.md").exists()
    text = (branches / "items/phase/phase-2/sprint-3.md").read_text(encoding="utf-8")
    assert "[[phase-2]]" in text and "[[phase-1]]" not in text
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (branches / "records").rglob("*.json")
    ]
    assert [p["kind"] for p in payloads].count("doc.relocated") == 1
    changed = [p for p in payloads if p["kind"] == "relation.changed"]
    assert changed[-1]["data"]["replaced"] == ["phase-1"]


@pytest.mark.parametrize(
    ("src", "dst", "flags", "code"),
    [
        ("phase-1", "sprint-3", {"replace": True}, "CYCLE_DETECTED"),
        ("sprint-3", "phase-2", {"replace": True, "remove": True}, "VALIDATION_FAILED"),
        ("sprint-3", "phase-1", {"replace": True}, "VALIDATION_FAILED"),
    ],
    ids=["cycle", "with-remove", "already-only-parent"],
)
def test_refused_replace_writes_nothing(
    branches: Path, src: str, dst: str, flags: dict[str, Any], code: str
) -> None:
    before = sorted(path for path in branches.rglob("*") if path.is_file())

    refused = _relink(branches, src, dst, **flags)

    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == code, refused
    assert sorted(path for path in branches.rglob("*") if path.is_file()) == before
