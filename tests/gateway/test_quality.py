"""A workflow's quality rule chooses the gate and the checks a move evaluates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_rail.core.util import dump_frontmatter
from context_rail.gateway.gateway import invoke
from helpers import init_pack_store, nested_pack_document

STAMP = "2026-10-28T00:00:00Z"

_GATES = {
    "item_done": {
        "require_checks": True,
        "require_acceptance": False,
        "require_review": False,
        "require_human": False,
    },
    "scope_done": {
        "require_checks": True,
        "require_acceptance": True,
        "require_review": False,
        "require_human": False,
    },
    "merge": {
        "require_checks": True,
        "require_acceptance": False,
        "require_review": True,
        "require_human": False,
    },
    "release": {
        "require_checks": True,
        "require_acceptance": True,
        "require_review": True,
        "require_human": True,
    },
    "code_review": {
        "require_checks": True,
        "require_acceptance": False,
        "require_review": False,
        "require_human": False,
    },
}


def _gated_store(
    tmp_path: Path,
    *,
    quality: dict[str, Any],
    gates: dict[str, Any] | None = None,
    scope: list[str] | None = None,
) -> Path:
    document = nested_pack_document("gated")
    document["statuses"] = {
        "todo": "not_started",
        "review": "active",
        "done": "done",
        "dropped": "dropped",
    }
    document["initial"] = "todo"
    document["transitions"] = {"todo": ["review", "done"], "review": ["done"], "done": []}
    document["workflows"] = {
        "sprint": {
            "initial": "todo",
            "transitions": {
                "todo": ["review", "done"],
                "review": ["done"],
                "done": [],
                "dropped": [],
            },
            "quality": quality,
        }
    }
    document["presets"]["task"]["workflow"] = "sprint"
    store = init_pack_store(tmp_path, document)
    charter = {
        "primitive": "policy",
        "preset": "charter",
        "title": "Charter",
        "tags": [],
        "created": STAMP,
        "updated": STAMP,
        "schema": 1,
        "project": {
            "goal": "ship",
            "vision": "v",
            "scope": scope or [],
            "wont": [],
        },
        "protected_files": [],
        "gates": gates or _GATES,
        "default_branch": "main",
    }
    (store / "charter.md").write_text(
        "---\n" + dump_frontmatter(charter) + "---\n# Charter\n",
        encoding="utf-8",
        newline="\n",
    )
    approved = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "test"},
        store=store,
        actor="human",
    )
    assert approved["ok"], approved
    return store


def _add_task(store: Path) -> None:
    created = invoke("add", {"preset": "task", "title": "Task"}, store=store, actor="cto")
    assert created["ok"], created


def _move(store: Path, status: str, actor: str = "cto") -> dict[str, Any]:
    shown = invoke("show", {"slug": "task"}, store=store)
    assert shown["ok"], shown
    return invoke(
        "move",
        {
            "slug": "task",
            "status": status,
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor=actor,
    )


def test_a_quality_rule_evaluates_its_gate(tmp_path: Path) -> None:
    store = _gated_store(tmp_path, quality={"review": {"gate": "code_review"}})
    _add_task(store)

    refused = _move(store, "review")

    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "BUSINESS_RULE_VIOLATED", refused
    assert refused["error"]["details"]["gate"] == "code_review"


def test_a_done_move_without_a_rule_uses_item_done(tmp_path: Path) -> None:
    store = _gated_store(tmp_path, quality={"review": {"gate": "code_review"}})
    _add_task(store)

    refused = _move(store, "done")

    assert refused["ok"] is False
    assert refused["error"]["details"]["gate"] == "item_done"


def test_a_required_check_is_named_with_the_command(tmp_path: Path) -> None:
    store = _gated_store(
        tmp_path,
        quality={"review": {"gate": "code_review", "checks": ["repro-check"]}},
    )
    _add_task(store)

    refused = _move(store, "review")

    assert refused["ok"] is False
    details = refused["error"]["details"]
    assert details["gate"] == "code_review"
    assert details["missing"] == ["repro-check"]
    assert "rail check repro-check" in details["command"]


def test_doctor_warns_when_the_legacy_scope_rule_chooses_the_gate(
    tmp_path: Path,
) -> None:
    store = _gated_store(
        tmp_path,
        quality={"review": {"gate": "code_review"}},
        scope=["task"],
    )
    path = store / "items/task/task.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + dump_frontmatter(
            {
                "primitive": "item",
                "preset": "task",
                "title": "Task",
                "status": "done",
                "tags": ["task"],
                "area": "general",
                "created": STAMP,
                "updated": STAMP,
            }
        )
        + "---\n# Task\n",
        encoding="utf-8",
        newline="\n",
    )

    report = invoke("doctor", {}, store=store, actor="human")

    assert report["ok"], report
    assert "quality_legacy" in {issue["code"] for issue in report["value"]["issues"]}
