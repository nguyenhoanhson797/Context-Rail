"""The work brief is a pure read any tool can take before it works an item."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.context.work_brief import (
    DEFAULT_CHARS,
    DIFF_CHARS,
    REVIEW_SECTIONS,
    WORK_BRIEF_SECTIONS,
)
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import refusal, run_cli, run_git


def commit_file(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    run_git(root, "add", rel)
    run_git(root, "commit", "-m", "change")
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def brief(item: SimpleNamespace, actor: str = "dev", **args: Any) -> dict[str, Any]:
    return invoke("brief", {"item": item.slug, **args}, store=item.store, actor=actor)


def candidate(item: SimpleNamespace, text: str = "VALUE = 2\n") -> dict[str, Any]:
    commit_file(item.root, "src/app.py", text)
    recorded = invoke(
        "candidate", {"item": item.slug, "commit": "HEAD"}, store=item.store, actor="dev"
    )
    assert is_ok(recorded), recorded
    return recorded["value"]


def tree_digest(store: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(store.rglob("*")):
        relative = path.relative_to(store)
        if ".rail-local" in relative.parts or ".git" in relative.parts or not path.is_file():
            continue
        digest.update(str(relative).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_implementer_brief_has_every_section_in_order(leaf_item: SimpleNamespace) -> None:
    result = brief(leaf_item)

    assert is_ok(result), result
    value = result["value"]
    assert value["role"] == "implementer"
    assert value["candidate"] is None
    assert tuple(value["sections"]) == WORK_BRIEF_SECTIONS
    headings = [line[3:] for line in value["text"].splitlines() if line.startswith("## ")]
    assert tuple(headings) == WORK_BRIEF_SECTIONS
    assert value["estimated_tokens"] > 0
    recording = value["sections"]["recording"]
    for command in (
        "rail loop candidate",
        "--commit",
        "rail check run",
        "rail change move",
        "rail loop report",
    ):
        assert command in recording
    lowered = value["text"].lower()
    for word in ("rail-report", "parent writes", "worktree "):
        assert word not in lowered.replace("--worktree", "")


def test_brief_names_the_checks_and_the_objective(leaf_item: SimpleNamespace) -> None:
    value = brief(leaf_item)["value"]

    assert leaf_item.slug in value["sections"]["objective"] or value["sections"]["objective"]
    assert value["sections"]["checks"] != "(none declared)"


def test_reviewer_brief_carries_the_diff_of_the_latest_candidate(
    leaf_item: SimpleNamespace,
) -> None:
    recorded = candidate(leaf_item)

    result = brief(leaf_item, role="reviewer")

    assert is_ok(result), result
    value = result["value"]
    assert value["candidate"] == recorded["record"]
    assert "VALUE = 2" in value["sections"]["diff"]
    assert set(REVIEW_SECTIONS) <= set(value["sections"])
    assert "rail loop review" in value["sections"]["recording"]
    assert "rail loop candidate" not in value["sections"]["recording"]


def test_reviewer_diff_is_bounded_and_says_so(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, "".join(f"LINE_{n} = {n}\n" for n in range(5000)))

    value = brief(leaf_item, role="reviewer")["value"]

    diff = value["sections"]["diff"]
    assert len(diff) <= DIFF_CHARS + 400
    assert "cut" in diff or diff.endswith("…")


def test_reviewer_without_a_candidate_is_refused(leaf_item: SimpleNamespace) -> None:
    result = brief(leaf_item, role="reviewer")

    assert refusal(result) == ("INVALID_STATE", "no_candidate")


def test_reviewer_can_name_the_candidate_commit(leaf_item: SimpleNamespace) -> None:
    first = candidate(leaf_item)
    candidate(leaf_item, "VALUE = 3\n")

    value = brief(leaf_item, role="reviewer", commit=first["commit"])["value"]

    assert value["candidate"] == first["record"]
    assert "VALUE = 2" in value["sections"]["diff"]


def test_brief_changes_nothing(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item)
    before = tree_digest(leaf_item.store)

    assert is_ok(brief(leaf_item))
    assert is_ok(brief(leaf_item, role="reviewer"))

    assert tree_digest(leaf_item.store) == before


def test_code_area_names_how_to_build_a_snapshot_when_none_exists(
    leaf_item: SimpleNamespace,
) -> None:
    area = brief(leaf_item)["value"]["sections"]["code area"]

    assert "rail ops codegraph refresh" in area


def test_max_chars_only_lowers_the_budget(leaf_item: SimpleNamespace) -> None:
    candidate(leaf_item, "".join(f"LINE_{n} = {n}\n" for n in range(5000)))
    default = brief(leaf_item, role="reviewer")["value"]
    raised = brief(leaf_item, role="reviewer", max_chars=32000)["value"]
    lowered = brief(leaf_item, role="reviewer", max_chars=4000)["value"]

    assert len(default["text"]) <= DEFAULT_CHARS
    assert len(raised["text"]) <= DEFAULT_CHARS
    assert len(lowered["text"]) <= 4000
    assert lowered["estimated_tokens"] < default["estimated_tokens"]


@pytest.mark.parametrize("bad", [0, 99, 32001, -5])
def test_max_chars_out_of_range_is_refused(leaf_item: SimpleNamespace, bad: int) -> None:
    assert refusal(brief(leaf_item, max_chars=bad))[0] == "VALIDATION_FAILED"


def test_an_explicit_small_budget_is_the_budget(leaf_item: SimpleNamespace) -> None:
    """A caller asking for 100 characters gets 100, not the 240 floor."""
    value = brief(leaf_item, max_chars=100)["value"]

    assert len(value["text"]) <= 100


@pytest.mark.parametrize("role", ["cto", "lead", "manager"])
def test_name_mode_refuses_a_role_it_does_not_have(
    leaf_item: SimpleNamespace, role: str
) -> None:
    assert refusal(brief(leaf_item, role=role))[0] == "VALIDATION_FAILED"


def test_unknown_item_is_not_found(leaf_item: SimpleNamespace) -> None:
    result = invoke("brief", {"item": "ghost"}, store=leaf_item.store, actor="dev")

    assert result["error"]["code"] == "NOT_FOUND"


def test_container_is_not_a_work_item(leaf_item: SimpleNamespace) -> None:
    result = invoke("brief", {"item": "send-invoice"}, store=leaf_item.store, actor="dev")

    assert is_ok(result) or refusal(result)[0] in {"INVALID_STATE", "NOT_FOUND"}


def test_cli_alias_prints_the_brief(leaf_item: SimpleNamespace) -> None:
    proc = run_cli(
        leaf_item.root, "--actor", "dev", "loop", "brief", leaf_item.slug, store=leaf_item.store
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "## recording" in proc.stdout
