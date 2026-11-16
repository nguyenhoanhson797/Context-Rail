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
    assert tuple(value["sections"]) == (*WORK_BRIEF_SECTIONS, "result")
    headings = [line[3:] for line in value["text"].splitlines() if line.startswith("## ")]
    assert tuple(headings) == (*WORK_BRIEF_SECTIONS, "result")
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

    assert refusal(result) == ("INVALID_STATE", "no_result_or_candidate")


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


def test_brief_reads_never_invoke_git_subprocess_or_hash_source_tree(
    leaf_item: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from context_rail.runner import git as runner_git
    from context_rail.store import location as store_location

    recorded = candidate(leaf_item)

    def boom(*args: object, **kwargs: object) -> Any:
        raise AssertionError("read path must not invoke git, subprocess, or tree hashing")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(runner_git, "diff_text", boom)
    monkeypatch.setattr(runner_git, "resolve_commit", boom)
    monkeypatch.setattr(runner_git, "rev_parse_head", boom)
    monkeypatch.setattr(runner_git, "ls_files", boom)
    monkeypatch.setattr(store_location, "head_sha", boom, raising=False)

    def guarded_read(self: Path, *args: object, **kwargs: object) -> Any:
        resolved = self.resolve()
        store_resolved = leaf_item.store.resolve()
        if resolved != store_resolved and not resolved.is_relative_to(store_resolved):
            if ".git" not in resolved.parts:
                raise AssertionError(f"brief must not read source tree file {resolved}")
        return real_read_bytes(self, *args, **kwargs)

    real_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", guarded_read)

    worker = brief(leaf_item)
    assert is_ok(worker), worker

    reviewer = brief(leaf_item, role="reviewer")
    assert is_ok(reviewer), reviewer
    assert reviewer["value"]["candidate"] == recorded["record"]
    assert "VALUE = 2" in reviewer["value"]["sections"]["diff"]

    explicit = brief(leaf_item, role="reviewer", commit=recorded["commit"][:8])
    assert is_ok(explicit), explicit
    assert explicit["value"]["candidate"] == recorded["record"]


def test_legacy_candidate_without_snapshot_reports_explicit_unavailable_notice(
    leaf_item: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from context_rail.runner import git as runner_git
    from helpers import write_candidate

    sha = commit_file(leaf_item.root, "src/app.py", "VALUE = 99\n")
    write_candidate(
        leaf_item.store,
        "01J0LDN0SNAPSHOT0000000001",
        leaf_item.slug,
        sha,
        base=sha,
        files_changed=["src/app.py"],
    )

    def boom(*args: object, **kwargs: object) -> Any:
        raise AssertionError("brief must not fall back to live git diff")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(runner_git, "diff_text", boom)

    reviewer = brief(leaf_item, role="reviewer")
    assert is_ok(reviewer), reviewer
    diff_text = reviewer["value"]["sections"]["diff"]
    assert "unavailable" in diff_text.lower()
    assert f"git diff {sha[:12]} {sha[:12]}" in diff_text
    assert "VALUE = 99" not in diff_text


def test_reviewer_brief_redacts_candidate_diff_outside_code_read_reach(
    tmp_path: Path,
) -> None:
    from helpers import (
        DEFAULT_PRODUCER_SECTIONS,
        DEFAULT_REVIEW_SECTIONS,
        _write_charter,
        approve_picture,
        as_human,
        as_seat,
        init_pack_store,
        require_git_identity,
        result_body,
        result_pack_document,
    )

    require_git_identity()
    doc = result_pack_document()
    doc["presets"]["phase"] = {"primitive": "item", "tags": ["phase"], "container": True}
    doc["operating"] = {
        "version": 2,
        "unit": "task",
        "hierarchy": [{"preset": "phase", "required": True}],
        "roles": {
            "lead": {
                "anchor": "project",
                "reach": {"read": ["project"], "write": ["project"], "code": {"read": ["**"], "write": []}},
                "can": {"add": ["phase", "task"], "grant": ["implementer", "reviewer"]},
                "sees": {"up": 0, "down": 1, "parts": ["title", "status", "results"]},
            },
            "implementer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["**"], "write": ["**"]}},
                "can": {"result": ["task"], "candidate": ["task"]},
                "sees": {"up": 1, "parts": ["title", "body", "results"]},
            },
            "reviewer": {
                "anchor": {"item": ["task"]},
                "reach": {"read": ["anchor", {"up": 1}], "write": ["anchor"], "code": {"read": ["docs/**"], "write": []}},
                "independent_of": ["produce"],
                "can": {"review": ["task"]},
                "sees": {"up": 1, "parts": ["title", "diff", "results", "evidence"]},
            },
        },
    }
    project = tmp_path / "proj"
    templates = project / "context-rail" / "packs" / doc["name"] / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for preset_name, sections in (
        ("finding", DEFAULT_PRODUCER_SECTIONS),
        ("assessment", DEFAULT_REVIEW_SECTIONS),
    ):
        (templates / f"{preset_name}.md").write_text(
            "# {title}\n\n" + "\n".join(f"## {s}\n" for s in sections),
            encoding="utf-8",
            newline="\n",
        )
    store = init_pack_store(
        project,
        doc,
        layout={
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
        approve=False,
    )
    _write_charter(store)
    with as_human():
        invoke("reindex", {}, store=store, actor=None)
        assert is_ok(approve_picture(store, actor=None))
        phase = invoke("add", {"preset": "phase", "title": "P1"}, store=store, actor=None)["value"]["slug"]
        task = invoke("add", {"preset": "task", "title": "T1", "rel": [f"part_of={phase}"]}, store=store, actor=None)["value"]["slug"]
        assert is_ok(invoke("seat grant", {"seat": "impl-1", "role": "implementer", "anchor": task}, store=store, actor=None))
        assert is_ok(invoke("seat grant", {"seat": "rev-1", "role": "reviewer", "anchor": task}, store=store, actor=None))

    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "src" / "secret.py").write_text("SECRET_KEY = 'old'\n", encoding="utf-8", newline="\n")
    run_git(project, "init", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", "base")
    (project / "src" / "secret.py").write_text("SECRET_KEY = 'top-secret-token'\n", encoding="utf-8", newline="\n")
    run_git(project, "add", "src/secret.py")
    run_git(project, "commit", "-m", "secret change")

    cand = as_seat(store, "impl-1", "candidate", {"item": task, "commit": "HEAD"})
    assert is_ok(cand), cand
    body_path = tmp_path / "res.md"
    body_path.write_text(result_body(DEFAULT_PRODUCER_SECTIONS), encoding="utf-8", newline="\n")
    res = as_seat(
        store,
        "impl-1",
        "result",
        {"item": task, "body_file": str(body_path), "commit": cand["value"]["commit"]},
    )
    assert is_ok(res), res

    brief_res = as_seat(store, "rev-1", "brief", {"item": task})
    assert is_ok(brief_res), brief_res
    diff_section = brief_res["value"]["sections"]["diff"]
    assert "diff hidden" in diff_section
    assert "top-secret-token" not in brief_res["value"]["text"]
