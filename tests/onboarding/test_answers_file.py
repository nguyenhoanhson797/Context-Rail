"""Onboarding by answers file: the picture, charter and profiles land atomically."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.core.util import load_yaml
from context_rail.gateway.gateway import invoke
from context_rail.model.policy import DECISION_RIGHTS, HUMAN_ONLY_IDS
from context_rail.store.doc import Doc, parse_doc
from helpers import run_git

STORE = "context-rail"


def _answers(tmp_path: Path, answers: Path, *, actor: str = "human", confirm: str | None = "APPROVE charter") -> dict[str, Any]:
    params: dict[str, Any] = {"dir": str(tmp_path), "answers_file": str(answers)}
    if confirm is not None:
        params["confirm"] = confirm
    return invoke("init", params, actor=actor)


def _write(path: Path, payload: dict[str, Any]) -> Path:
    import json

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def _frontmatter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    return load_yaml("\n".join(lines[1:end]))


def _doc(store: Path, rel: str) -> Doc:
    return parse_doc(rel, (store / rel).read_bytes())


def _store_files(store: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(store.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(store).as_posix()
        if rel.split("/")[0] == ".rail-local":
            continue
        files[rel] = path.read_bytes()
    return files


def _init_scaffold(tmp_path: Path) -> Path:
    created = invoke("init", {"dir": str(tmp_path)}, actor="human")
    assert is_ok(created), created
    return tmp_path / STORE


def test_answers_file_writes_the_picture_atomically(tmp_path: Path, answers: Path) -> None:
    result = _answers(tmp_path, answers)

    assert is_ok(result), result
    value = result["value"]
    assert set(value) == {"store", "written", "picture", "charter", "approval"}
    assert value["approval"] == "missing"
    assert value["charter"] == "charter"
    assert value["picture"] == [
        "ship-invoicing",
        "invoices-on-autopilot",
        "send-invoice",
        "track-payment",
        "chase-overdue",
        "wont",
    ]

    store = Path(value["store"])
    assert store == (tmp_path / STORE).resolve()
    expected = {
        "rail.yaml",
        "charter.md",
        "project/ship-invoicing.md",
        "project/invoices-on-autopilot.md",
        "project/send-invoice.md",
        "project/track-payment.md",
        "project/chase-overdue.md",
        "project/wont.md",
    }
    assert expected <= set(value["written"])
    for rel in value["written"]:
        assert (store / rel).is_file(), rel

    extra = set(_store_files(store)) - set(value["written"])
    assert all(rel.startswith("records/") for rel in extra), extra
    assert (store / "checks").is_dir()
    assert list((store / "checks").iterdir()) == []


def test_picture_documents_carry_the_pinned_roles_priorities_and_placement(
    tmp_path: Path, answers: Path
) -> None:
    result = _answers(tmp_path, answers)
    assert is_ok(result), result
    store = tmp_path / STORE

    goal = _doc(store, "project/ship-invoicing.md")
    assert (goal.primitive, goal.preset, goal.status) == ("item", "goal", "todo")
    assert goal.fields["picture_role"] == "goal"

    vision = _doc(store, "project/invoices-on-autopilot.md")
    assert (vision.primitive, vision.preset) == ("note", "vision")
    assert vision.fields["picture_role"] == "vision"

    for rel, priority in (
        ("project/send-invoice.md", "must"),
        ("project/track-payment.md", "should"),
        ("project/chase-overdue.md", "could"),
    ):
        scope = _doc(store, rel)
        assert scope.primitive == "item"
        assert scope.fields["picture_role"] == "scope"
        assert scope.fields["priority"] == priority
        assert scope.relations["part_of"] == ("ship-invoicing",)

    wont = _doc(store, "project/wont.md")
    assert wont.primitive == "note"
    assert wont.fields["picture_role"] == "wont"
    assert wont.fields["entries"] == [
        {"title": "Payroll", "reason": "A payroll product does it better."}
    ]

    charter = _doc(store, "charter.md")
    assert charter.primitive == "policy"
    assert charter.preset == "charter"
    assert charter.slug == "charter"
    assert charter.title == "Charter"
    front = _frontmatter(store / "charter.md")
    assert front["project"] == {
        "goal": "ship-invoicing",
        "vision": "invoices-on-autopilot",
        "scope": ["send-invoice", "track-payment", "chase-overdue"],
        "wont": ["wont"],
    }


def test_picture_bodies_are_the_supplied_text_and_no_acceptance_is_invented(
    tmp_path: Path, answers: Path, picture_answers: dict[str, Any]
) -> None:
    result = _answers(tmp_path, answers)
    assert is_ok(result), result
    store = tmp_path / STORE

    assert _doc(store, "project/ship-invoicing.md").body == picture_answers["goal"]["body"]
    assert (
        _doc(store, "project/invoices-on-autopilot.md").body
        == picture_answers["vision"]["body"]
    )
    for rel, entry in zip(
        ("project/send-invoice.md", "project/track-payment.md", "project/chase-overdue.md"),
        picture_answers["scope"],
        strict=True,
    ):
        assert _doc(store, rel).body == entry["body"]

    items = [
        doc
        for doc in (
            parse_doc(path.relative_to(store).as_posix(), path.read_bytes())
            for path in store.glob("project/*.md")
        )
        if doc.primitive == "item"
    ]
    assert {doc.preset for doc in items} == {"goal", "scope"}
    assert len(items) == 4


def test_shipped_profiles_are_materialized_with_the_pinned_fields(
    tmp_path: Path, answers: Path
) -> None:
    result = _answers(tmp_path, answers)
    assert is_ok(result), result
    store = tmp_path / STORE

    seven = [
        "read-cli",
        "ls-cli",
        "grep_tool_cli",
        "glob-search-cli",
        "create-cli",
        "edit-cli",
        "execute-cli",
    ]
    cto = _frontmatter(store / "workspace/cto/profile.md")
    assert cto["recipe"] == "cto"
    assert cto["model"] == "custom:Deepseek-V4-flash-0"
    assert cto["effort"] is None
    assert cto["auto"] == "high"
    assert cto["tools"] == seven
    assert "cto tick" not in cto["allowed_methods"]

    implementer = _frontmatter(store / "workspace/profiles/implementer/profile.md")
    assert implementer["recipe"] == "implementer"
    assert implementer["model"] == "custom:Deepseek-V4-flash-0"
    assert implementer["effort"] is None
    assert implementer["auto"] == "medium"
    assert implementer["tools"] == seven
    assert implementer["allowed_methods"] == [
        "find",
        "show",
        "cto brief",
        "ledger",
        "report",
    ]

    reviewer = _frontmatter(store / "workspace/profiles/reviewer/profile.md")
    assert reviewer["recipe"] == "reviewer"
    assert reviewer["model"] == "custom:gemini-3.8-flash-LTN-0"
    assert reviewer["effort"] is None
    assert "auto" in reviewer and reviewer["auto"] is None
    assert reviewer["tools"] == seven[:4]
    assert reviewer["allowed_methods"] == [
        "find",
        "show",
        "cto brief",
        "ledger",
        "report",
    ]

    profiles = invoke("find", {"preset": "profile"}, store=store)
    assert profiles["value"]["total"] == 2
    assert invoke("find", {"preset": "cto"}, store=store)["value"]["total"] == 1
    skills = invoke("find", {"preset": "skill"}, store=store)
    assert skills["value"]["total"] == 4


def test_materialized_profiles_are_well_formed_store_documents(
    tmp_path: Path, answers: Path
) -> None:
    result = _answers(tmp_path, answers)
    assert is_ok(result), result
    store = tmp_path / STORE

    cto = _frontmatter(store / "workspace/cto/profile.md")
    assert (cto["primitive"], cto["preset"], cto["title"]) == ("actor", "cto", "CTO")
    for name in ("implementer", "reviewer"):
        front = _frontmatter(store / f"workspace/profiles/{name}/profile.md")
        assert (front["primitive"], front["preset"]) == ("actor", "profile")
        assert front["title"]

    report = invoke("doctor", {}, store=store)["value"]
    assert [
        issue for issue in report["issues"] if issue["path"].startswith("workspace/")
    ] == []


def test_charter_defaults_persist_exactly(tmp_path: Path, answers: Path) -> None:
    result = _answers(tmp_path, answers)
    assert is_ok(result), result
    front = _frontmatter(tmp_path / STORE / "charter.md")

    assert front["schema"] == 1
    assert front["gates"] == {
        "item_done": {
            "require_checks": True,
            "require_acceptance": False,
            "require_review": True,
            "require_human": False,
        },
        "scope_done": {
            "require_checks": True,
            "require_acceptance": True,
            "require_review": True,
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
    }
    assert front["budgets"] == {
        "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
        "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
    }
    assert front["thresholds"] == {
        "stuck_hours": 24,
        "near_budget_ratio": 0.8,
        "triage_reports": 2,
        "triage_weight": 2,
    }
    assert front["change_rule"] == {"approval_required": True, "delegable": False}
    assert front["human_in_loop"] == list(HUMAN_ONLY_IDS)
    assert front["decision_rights"] == {
        name: list(ids) for name, ids in DECISION_RIGHTS.items()
    }
    assert front["allow_push"] is False
    assert front["remote"] == "origin"
    assert front["reviewer_different_model"] is True
    assert front["protected_files"] == [
        "rail.yaml",
        "charter.md",
        "project/ship-invoicing.md",
        "project/invoices-on-autopilot.md",
        "project/wont.md",
        "project/send-invoice.md",
        "project/track-payment.md",
    ]


def test_unknown_answer_key_writes_nothing(tmp_path: Path, answers_with_extra_key: Path) -> None:
    result = _answers(tmp_path, answers_with_extra_key)

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_fifth_gate_and_missing_release_are_refused(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    extra_gate = copy.deepcopy(picture_answers)
    extra_gate["charter"]["gates"]["deploy"] = {
        "require_checks": True,
        "require_acceptance": True,
        "require_review": True,
        "require_human": True,
    }
    result = _answers(tmp_path, _write(tmp_path / "gates-extra.json", extra_gate))
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()

    missing = copy.deepcopy(picture_answers)
    del missing["charter"]["gates"]["release"]
    result = _answers(tmp_path, _write(tmp_path / "gates-missing.json", missing))
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_answers_file_change_rule_cannot_be_disabled_or_delegated(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    delegated = copy.deepcopy(picture_answers)
    delegated["charter"]["change_rule"] = {"approval_required": True, "delegable": True}
    result = _answers(tmp_path, _write(tmp_path / "rule-delegated.json", delegated))
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()

    disabled = copy.deepcopy(picture_answers)
    disabled["charter"]["change_rule"] = {"approval_required": False, "delegable": False}
    result = _answers(tmp_path, _write(tmp_path / "rule-disabled.json", disabled))
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_unknown_answers_schema_is_refused(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    payload = copy.deepcopy(picture_answers)
    payload["schema"] = 2

    result = _answers(tmp_path, _write(tmp_path / "schema2.json", payload))

    assert result["error"]["code"] in {"SCHEMA_UNSUPPORTED", "VALIDATION_FAILED"}
    assert not (tmp_path / STORE).exists()


def test_wrong_or_missing_phrase_writes_nothing(
    tmp_path: Path, answers: Path
) -> None:
    typo = _answers(tmp_path, answers, confirm="APPROVE chart")
    assert typo["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()

    missing = _answers(tmp_path, answers, confirm=None)
    assert missing["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_cto_actor_is_refused_for_adoption(tmp_path: Path, answers: Path) -> None:
    result = _answers(tmp_path, answers, actor="cto")

    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert not (tmp_path / STORE).exists()


def test_supplied_protected_files_must_match_the_computed_picture(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    payload = copy.deepcopy(picture_answers)
    payload["charter"]["protected_files"] = ["rail.yaml", "charter.md"]

    result = _answers(tmp_path, _write(tmp_path / "protected.json", payload))

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_default_branch_comes_from_the_symbolic_head(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "master")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "fixture")
    payload = copy.deepcopy(picture_answers)
    del payload["charter"]["default_branch"]

    result = _answers(repo, _write(repo / "answers.json", payload))

    assert is_ok(result), result
    front = _frontmatter(repo / STORE / "charter.md")
    assert front["default_branch"] == "master"


def test_detached_head_needs_an_explicit_default_branch(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "master")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "fixture")
    run_git(repo, "checkout", "--detach")
    omitted = copy.deepcopy(picture_answers)
    del omitted["charter"]["default_branch"]

    refused = _answers(repo, _write(repo / "answers-omitted.json", omitted))

    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (repo / STORE).exists()

    explicit = copy.deepcopy(picture_answers)
    explicit["charter"]["default_branch"] = "main"
    accepted = _answers(repo, _write(repo / "answers-explicit.json", explicit))

    assert is_ok(accepted), accepted
    front = _frontmatter(repo / STORE / "charter.md")
    assert front["default_branch"] == "main"


def test_scaffold_completion_keeps_the_existing_rail_yaml_bytes(
    tmp_path: Path, answers: Path
) -> None:
    store = _init_scaffold(tmp_path)
    before = {
        rel: hashlib.sha256(data).hexdigest()
        for rel, data in _store_files(store).items()
        if rel in {"rail.yaml", ".gitignore"}
    }

    result = _answers(tmp_path, answers)

    assert is_ok(result), result
    after = {
        rel: hashlib.sha256(data).hexdigest()
        for rel, data in _store_files(store).items()
        if rel in {"rail.yaml", ".gitignore"}
    }
    assert after == before
    assert (store / "charter.md").is_file()
    assert (store / "project" / "ship-invoicing.md").is_file()
    assert "rail.yaml" not in result["value"]["written"]


def test_a_pre_existing_charter_blocks_the_whole_write(
    tmp_path: Path, answers: Path
) -> None:
    store = _init_scaffold(tmp_path)
    hand_edit = store / "charter.md"
    hand_edit.write_text("HAND EDIT", encoding="utf-8", newline="\n")
    before = _store_files(store)

    result = _answers(tmp_path, answers)

    assert result["error"]["code"] == "CONFLICT"
    assert _store_files(store) == before
    assert hand_edit.read_text(encoding="utf-8") == "HAND EDIT"
    assert not (store / "project").exists()


def test_a_replayed_init_does_not_read_the_answers_file(
    tmp_path: Path, answers: Path
) -> None:
    args = {
        "dir": str(tmp_path),
        "answers_file": str(answers),
        "confirm": "APPROVE charter",
        "key": "k-init-answers",
    }
    first = invoke("init", args, actor="human")
    assert is_ok(first), first
    answers.unlink()

    second = invoke("init", args, actor="human")

    assert is_ok(second), second
    assert second["value"] == first["value"]
