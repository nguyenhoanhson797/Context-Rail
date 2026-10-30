"""Adapter markers inside generated content, byte fidelity and keyed replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.adapters import (
    END_MARK,
    START_MARK,
    AdapterConflict,
    render_agents,
)
from context_rail.store.doc import etag

AGENTS = "AGENTS.md"
SKILL = ".agents/skills/test-first/SKILL.md"
MARKER_BODY = (
    "# test-first\n\nWrite the failing test first.\n\n"
    "A fenced snippet a skill may legitimately show:\n\n"
    "```html\n" + START_MARK + "\n```\n"
)


def _agents(store: Path) -> Path:
    return store.parent / AGENTS


def _skill(store: Path) -> Path:
    return store.parent / ".agents" / "skills" / "test-first" / "SKILL.md"


def _skill_source(store: Path) -> Path:
    return store / "workspace" / "skills" / "test-first.md"


def _sync(store: Path, key: str | None = None, **extra: object) -> dict:
    args: dict[str, object] = dict(extra)
    if key is not None:
        args["key"] = key
    return invoke("adapters sync", args, store=store, actor=extra.pop("actor", "cto"))


# ── a marker inside generated content ─────────────────────────────────────


def test_a_marker_bearing_skill_body_keeps_one_paired_block(
    skill_store: Path, tmp_path: Path
) -> None:
    body = tmp_path / "marker-body.md"
    body.write_text(MARKER_BODY, encoding="utf-8", newline="\n")
    updated = invoke(
        "update",
        {
            "slug": "test-first",
            "expect": etag(_skill_source(skill_store).read_bytes()),
            "body_file": str(body),
            "key": "marker-skill",
        },
        store=skill_store,
        actor="cto",
    )
    assert is_ok(updated), updated

    refused = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    message = refused["error"]["message"]
    assert "test-first" in message
    assert not _agents(skill_store).exists()
    assert not _skill(skill_store).exists()


def test_a_marker_bearing_payload_is_refused_at_render_time() -> None:
    payload = "# Context Rail\n\n" + START_MARK + "\n"
    with pytest.raises(AdapterConflict) as raised:
        render_agents(None, payload)
    assert "CONTEXT-RAIL" in str(raised.value)


def test_a_clean_sync_after_a_marker_body_is_refused_stays_stable(
    skill_store: Path, tmp_path: Path
) -> None:
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))
    before_agents = _agents(skill_store).read_bytes()
    body = tmp_path / "marker-body.md"
    body.write_text(MARKER_BODY, encoding="utf-8", newline="\n")
    assert is_ok(
        invoke(
            "update",
            {
                "slug": "test-first",
                "expect": etag(_skill_source(skill_store).read_bytes()),
                "body_file": str(body),
                "key": "marker-2",
            },
            store=skill_store,
            actor="cto",
        )
    )

    first = invoke("adapters sync", {}, store=skill_store, actor="cto")
    assert first["ok"] is False, first
    assert _agents(skill_store).read_bytes() == before_agents


# ── non-UTF-8 unmanaged prose ─────────────────────────────────────────────


def test_non_utf8_human_prose_is_never_replaced(skill_store: Path) -> None:
    agents = _agents(skill_store)
    agents.parent.mkdir(parents=True, exist_ok=True)
    prefix = "# Team notes\n\ncaf\u00e9 \u2014 latin-1 bytes follow:\n".encode("utf-8")
    latin = "d\u00e9j\u00e0 vu\n".encode("latin-1")
    agents.write_bytes(prefix + latin)

    result = invoke("adapters sync", {}, store=skill_store, actor="cto")

    assert result["ok"] is False, result
    assert result["error"]["code"] in {"CONFLICT", "VALIDATION_FAILED"}
    assert AGENTS in str(result["error"].get("details") or result["error"]["message"])
    rebuilt = agents.read_bytes()
    assert rebuilt == prefix + latin
    assert b"\xef\xbf\xbd" not in rebuilt


def test_utf8_human_prose_survives_byte_for_byte(skill_store: Path) -> None:
    agents = _agents(skill_store)
    agents.parent.mkdir(parents=True, exist_ok=True)
    prefix = "# Notes\n\ncaf\u00e9 na\u00efve \u2014 \u65e5\u672c\u8a9e\n\n"
    agents.write_text(prefix, encoding="utf-8", newline="\n")
    assert is_ok(invoke("adapters sync", {}, store=skill_store, actor="cto"))

    after = agents.read_bytes()
    assert after.startswith(prefix.encode("utf-8"))


# ── keyed replay reconciles first ─────────────────────────────────────────


def test_a_reused_sync_key_after_a_source_change_regenerates(
    skill_store: Path, tmp_path: Path
) -> None:
    first = invoke("adapters sync", {"key": "k-reuse"}, store=skill_store, actor="cto")
    assert is_ok(first), first
    agents_before = _agents(skill_store).read_bytes()

    body = tmp_path / "body.md"
    body.write_text("# test-first\n\nAlways prove it with a run.\n", encoding="utf-8")
    assert is_ok(
        invoke(
            "update",
            {
                "slug": "test-first",
                "expect": etag(_skill_source(skill_store).read_bytes()),
                "body_file": str(body),
                "key": "skill-change",
            },
            store=skill_store,
            actor="cto",
        )
    )

    second = invoke("adapters sync", {"key": "k-reuse"}, store=skill_store, actor="cto")

    assert is_ok(second), second
    assert _agents(skill_store).read_bytes() != agents_before
    assert "Always prove it with a run." in _agents(skill_store).read_text(encoding="utf-8")
    assert "Always prove it with a run." in _skill(skill_store).read_text(encoding="utf-8")


def test_a_reused_force_key_after_a_hand_edit_rewrites_the_block(
    skill_store: Path,
) -> None:
    assert is_ok(invoke("adapters sync", {"key": "k-force-reuse"}, store=skill_store, actor="cto"))
    agents = _agents(skill_store)
    text = agents.read_text(encoding="utf-8")
    agents.write_text(
        text.replace(END_MARK, "hand edited line\n" + END_MARK), encoding="utf-8"
    )

    edited = agents.read_text(encoding="utf-8")
    refused = invoke("adapters sync", {"key": "k-force-reuse"}, store=skill_store, actor="cto")
    assert refused["ok"] is False, refused
    assert agents.read_text(encoding="utf-8") == edited

    forced = invoke(
        "adapters sync",
        {
            "key": "k-force-reuse",
            "force": True,
            "confirm": "FORCE ADAPTERS adapters",
        },
        store=skill_store,
        actor="human",
    )
    assert is_ok(forced), forced
    after = agents.read_text(encoding="utf-8")
    assert "hand edited line" not in after
    assert after.count(START_MARK) == 1 and after.count(END_MARK) == 1


def test_an_unchanged_reused_key_still_replays_without_a_new_record(
    skill_store: Path,
) -> None:
    first = invoke("adapters sync", {"key": "k-idem"}, store=skill_store, actor="cto")
    assert is_ok(first), first
    records_before = sorted((skill_store / "records").rglob("*.json"))

    second = invoke("adapters sync", {"key": "k-idem"}, store=skill_store, actor="cto")

    assert is_ok(second), second
    assert second["value"]["written"] == []
    assert sorted((skill_store / "records").rglob("*.json")) == records_before
